"""사용자 성적 CSV 업로드/스냅샷 API.

엔드포인트:
  POST   /iidx/scores/upload                      — CSV 업로드 (multipart/form-data)
  GET    /iidx/scores/snapshots?style=SP|DP        — 스냅샷 목록
  POST   /iidx/scores/restore/{upload_id}          — 스냅샷 복구
  GET    /iidx/scores?style=SP|DP                  — 현재 성적 조회
  GET    /iidx/scores/snapshots/{upload_id}/download — 원본 CSV 다운로드 URL
"""

import asyncio

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.api.deps import CurrentUser, UploadUser
from app.core.openapi import PUBLIC
from app.crud.iidx import scores as crud_scores
from app.schemas.iidx.scores import (
    ChartScoreItem,
    DownloadUrlResponse,
    RestoreResponse,
    ScoreListResponse,
    SnapshotListResponse,
    SnapshotSummary,
    UploadResponse,
    UploadTokenResponse,
)
from app.services.iidx.scores import storage as score_storage, upload_token as _upload_token
from app.services.iidx.scores.upload import upload_score_csv

router = APIRouter()

_ALLOWED_STYLES = {"SP", "DP"}


@router.post(
    "/token",
    summary="북마크릿 업로드 토큰 발급",
    response_model=UploadTokenResponse,
    openapi_extra=PUBLIC,
)
async def create_upload_token(user: CurrentUser):
    """북마크릿에서 사용할 단기 업로드 토큰을 발급한다 (1시간 유효).

    웹 FE에서 로그인 후 이 API로 토큰을 발급받아 북마크릿 UI에 붙여넣으면
    크롤 완료 후 성적 CSV와 프로필이 자동으로 서버에 업로드된다.
    """
    token = await _upload_token.create_token(user.id)
    return UploadTokenResponse(token=token, expires_in=_upload_token.TTL)
_MAX_CSV_BYTES = 10 * 1024 * 1024  # 10 MB 상한


def _check_style(style: str) -> None:
    if style not in _ALLOWED_STYLES:
        raise HTTPException(status_code=422, detail="style은 SP 또는 DP 여야 합니다.")


@router.post(
    "/upload",
    summary="성적 CSV 업로드",
    response_model=UploadResponse,
    openapi_extra=PUBLIC,
)
async def upload_scores(
    user: UploadUser,
    style: str = Query(..., description="SP 또는 DP"),
    file: UploadFile = File(..., description="eagate 성적 CSV (공식 또는 북마크릿 크롤)"),
):
    """eagate 성적 CSV를 업로드해 성적을 갱신하고 스냅샷을 생성한다.

    - 공식 CSV(score_download.html)와 북마크릿 크롤 CSV를 자동 판별한다.
      공식: バージョン/ジャンル/アーティスト/プレー回数/ミスカウント/最終プレー日시 모두 채워짐.
      크롤: 위 필드가 모두 null로 저장됨.
    - 이전 업로드와 내용이 완전히 동일하면 스냅샷을 생성하지 않고 changed=false를 반환한다.
    """
    _check_style(style)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    if len(content) > _MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다 (최대 10 MB).")

    try:
        result = await upload_score_csv(user.id, style, content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return UploadResponse(
        upload_id=result.upload_id,
        play_style=style,
        source=result.source,
        song_count=result.song_count,
        uploaded_at=result.uploaded_at,
        changed=result.changed,
    )


@router.get(
    "/snapshots",
    summary="성적 스냅샷 목록",
    response_model=SnapshotListResponse,
    openapi_extra=PUBLIC,
)
async def list_snapshots(
    user: CurrentUser,
    style: str = Query(..., description="SP 또는 DP"),
):
    """업로드 이력 목록과 현재 활성 스냅샷 ID를 반환한다."""
    _check_style(style)
    uploads, current = await asyncio.gather(
        asyncio.to_thread(crud_scores.list_uploads, user.id, style),
        asyncio.to_thread(crud_scores.get_current, user.id, style),
    )
    current_id = current["upload_id"] if current else None

    return SnapshotListResponse(
        snapshots=[
            SnapshotSummary(
                upload_id=u["id"],
                play_style=u["play_style"],
                source=u["source"],
                song_count=u["song_count"],
                uploaded_at=u["uploaded_at"],
                is_current=(u["id"] == current_id),
            )
            for u in uploads
        ],
        current_upload_id=current_id,
    )


@router.post(
    "/restore/{upload_id}",
    summary="성적 스냅샷 복구",
    response_model=RestoreResponse,
    openapi_extra=PUBLIC,
)
async def restore_snapshot(upload_id: str, user: CurrentUser):
    """지정한 스냅샷을 현재 성적으로 복구한다.

    본인의 업로드만 복구 가능. 파일 재파싱 없이 기존 저장 데이터를 즉시 활성화한다.
    """
    upload = await asyncio.to_thread(crud_scores.get_upload, upload_id)
    if not upload or upload["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="스냅샷을 찾을 수 없습니다.")

    current_row = await asyncio.to_thread(
        crud_scores.upsert_current, user.id, upload["play_style"], upload_id
    )
    return RestoreResponse(
        upload_id=upload_id,
        play_style=upload["play_style"],
        applied_at=current_row["applied_at"],
    )


@router.get(
    "",
    summary="현재 성적 조회",
    response_model=ScoreListResponse,
    openapi_extra=PUBLIC,
)
async def get_scores(
    user: CurrentUser,
    style: str = Query(..., description="SP 또는 DP"),
):
    """현재 활성 스냅샷의 파싱된 성적을 반환한다.

    스냅샷이 없으면 빈 목록을 반환한다.
    """
    _check_style(style)
    current = await asyncio.to_thread(crud_scores.get_current, user.id, style)
    if not current:
        return ScoreListResponse(play_style=style, scores=[])

    upload_id = current["upload_id"]
    upload, rows = await asyncio.gather(
        asyncio.to_thread(crud_scores.get_upload, upload_id),
        asyncio.to_thread(crud_scores.get_chart_scores, user.id, style),
    )

    return ScoreListResponse(
        play_style=style,
        source=upload["source"] if upload else None,
        upload_id=upload_id,
        scores=[
            ChartScoreItem(
                title=r["title"],
                difficulty=r["difficulty"],
                version=r.get("version"),
                genre=r.get("genre"),
                artist=r.get("artist"),
                play_count=r.get("play_count"),
                last_played_at=r.get("last_played_at"),
                miss_count=r.get("miss_count"),
                level=r["level"],
                ex_score=r["ex_score"],
                pgreat=r["pgreat"],
                great=r["great"],
                clear_type=r.get("clear_type"),
                dj_level=r.get("dj_level"),
                song_id=r.get("song_id"),
            )
            for r in rows
        ],
    )


@router.get(
    "/snapshots/{upload_id}/download",
    summary="스냅샷 CSV 다운로드 URL",
    response_model=DownloadUrlResponse,
    openapi_extra=PUBLIC,
)
async def get_snapshot_download_url(upload_id: str, user: CurrentUser):
    """지정한 스냅샷의 원본 CSV를 내려받을 서명 URL을 반환한다 (1시간 유효)."""
    upload = await asyncio.to_thread(crud_scores.get_upload, upload_id)
    if not upload or upload["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="스냅샷을 찾을 수 없습니다.")

    url = await score_storage.get_signed_url_async(upload["storage_path"])
    return DownloadUrlResponse(url=url, expires_in=3600)
