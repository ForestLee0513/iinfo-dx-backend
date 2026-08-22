"""사용자 성적 CSV 업로드/스냅샷 API.

엔드포인트:
  POST   /iidx/scores/upload                      — CSV 업로드 (multipart/form-data)
  GET    /iidx/scores/snapshots?style=SP|DP        — 스냅샷 목록
  POST   /iidx/scores/restore/{upload_id}          — 스냅샷 복구
  GET    /iidx/scores?style=SP|DP                  — 현재 성적 조회
  GET    /iidx/scores/snapshots/{upload_id}/download — 원본 CSV 다운로드 URL
  GET    /iidx/scores/summary?identifier=&style=&level= — 클리어 현황 요약
  GET    /iidx/scores/upload-calendar?identifier=&style=&since=&until=&days=&tz= — 날짜별 업로드 횟수(기여도 그래프)
"""

import asyncio
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, OptionalIdentity, UploadUser
from app.core.openapi import PUBLIC
from app.crud.account import profiles as crud_profiles
from app.crud.iidx import charts as crud_charts, scores as crud_scores
from app.schemas.account.profile import HANDLE_PATTERN
from app.schemas.iidx.scores import (
    ChartScoreItem,
    DownloadUrlResponse,
    MultiUploadResponse,
    RestoreResponse,
    ScoreListResponse,
    ScoreSummaryResponse,
    ScoreUploadRequest,
    SnapshotListResponse,
    SnapshotSummary,
    UploadCalendarResponse,
    UploadResponse,
    UploadTokenResponse,
)
from app.services.iidx.scores import storage as score_storage, upload_token as _upload_token
from app.services.iidx.scores.summary import build_score_summary
from app.services.iidx.scores.upload_calendar import build_upload_calendar
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
    """북마크릿에서 사용할 단기 업로드 토큰을 발급한다 (10분 유효).

    웹 FE에서 로그인 후 이 API로 토큰을 발급받아 북마크릿 UI에 붙여넣으면
    크롤 완료 후 성적 CSV와 프로필이 자동으로 서버에 업로드된다.
    아직 만료되지 않은 토큰이 있으면 새로 발급하지 않고 기존 토큰을 반환한다.
    """
    token, expires_in = await _upload_token.create_token(user.id)
    return UploadTokenResponse(token=token, expires_in=expires_in)
_MAX_CSV_BYTES = 10 * 1024 * 1024  # 10 MB 상한


def _check_style(style: str) -> None:
    if style not in _ALLOWED_STYLES:
        raise HTTPException(status_code=422, detail="style은 SP 또는 DP 여야 합니다.")


@router.post(
    "/upload",
    summary="성적 업로드 (SP/DP 한 번에)",
    response_model=MultiUploadResponse,
    openapi_extra=PUBLIC,
)
async def upload_scores(user: UploadUser, body: ScoreUploadRequest):
    """eagate 성적을 SP/DP 한 번의 JSON 요청으로 업로드해 성적·프로필을 갱신한다.

    - `csv.SP` / `csv.DP` 중 있는 스타일만 각각 처리한다(최소 하나 필수). 스타일을
      쿼리 파라미터로 나눠 두 번 호출하지 않으므로, 첫 호출에서 토큰이 만료돼
      두 번째가 실패하던 문제가 없다.
    - 공식 CSV(score_download.html)와 북마크릿 크롤 CSV를 자동 판별한다.
      공식: バージョン/ジャンル/アーティスト/プレー回数/ミスカウント/最終プレー日시 모두 채워짐.
      크롤: 위 필드가 모두 null로 저장됨.
    - 이전 업로드와 내용이 완전히 동일하면 스냅샷을 생성하지 않고 changed=false를 반환한다.
    - `profile`(크롤러 Profile)을 함께 보내면 IIDX 프로필도 같은 요청에서 동기화한다
      (미온보딩이면 무시).
    - 업로드 토큰(X-Upload-Token)으로 인증했다면 모든 성적 처리 성공 후 그 토큰을
      즉시 만료시킨다.
    """
    items = [(s, c) for s, c in (("SP", body.csv.SP), ("DP", body.csv.DP)) if c is not None]
    if not items:
        raise HTTPException(
            status_code=400, detail="csv.SP 또는 csv.DP 중 최소 하나가 필요합니다."
        )

    results: list[UploadResponse] = []
    for style, text in items:
        content = text.encode("utf-8")
        if not content:
            raise HTTPException(status_code=400, detail=f"{style} CSV가 비어 있습니다.")
        if len(content) > _MAX_CSV_BYTES:
            raise HTTPException(
                status_code=413, detail=f"{style} 파일이 너무 큽니다 (최대 10 MB)."
            )
        try:
            result = await upload_score_csv(user.id, style, content)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"{style}: {e}")
        results.append(
            UploadResponse(
                upload_id=result.upload_id,
                play_style=style,
                source=result.source,
                song_count=result.song_count,
                uploaded_at=result.uploaded_at,
                changed=result.changed,
            )
        )

    # 모든 스타일 업로드가 성공한 뒤에만 프로필 동기화 + 토큰 소모를 수행한다.
    if body.profile is not None:
        p = body.profile
        await asyncio.to_thread(
            crud_profiles.sync_iidx_stats,
            user.id,
            dj_name=p.djName,
            dj_id=p.iidxId,
            community_nickname=p.communityNickname,
            play_count=p.playCount,
            notes_radar=p.notesRadar.model_dump() if p.notesRadar is not None else None,
            dan=p.dan.model_dump() if p.dan is not None else None,
            arena_class=p.arenaClass.model_dump() if p.arenaClass is not None else None,
        )

    if user.upload_token is not None:
        await _upload_token.revoke_token(user.upload_token)

    return MultiUploadResponse(results=results)


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


def _resolve_identifier(identifier: str) -> dict | None:
    """identifier(UUID 또는 handle)로 account 프로필 행을 찾는다. 없으면 None."""
    try:
        uuid.UUID(identifier)
    except ValueError:
        if not HANDLE_PATTERN.match(identifier):
            return None
        return crud_profiles.get_profile_row_by_handle(identifier)
    return crud_profiles.get_profile_row(identifier)


@router.get(
    "/summary",
    summary="클리어 현황 요약 (클리어 램프 비율)",
    response_model=ScoreSummaryResponse,
    openapi_extra=PUBLIC,
)
async def get_score_summary(
    identity: OptionalIdentity,
    identifier: str = Query(..., description="대상 유저의 UUID 또는 handle"),
    style: str = Query(..., description="SP 또는 DP"),
    level: int | None = Query(
        None, ge=1, description="특정 레벨만 집계. 생략하면 해당 스타일 전체 레벨 합산"
    ),
):
    """대상 유저의 현재 성적 스냅샷을 스타일/레벨 기준 클리어 램프 비율로 요약한다.

    - identifier(UUID/handle)로 조회한 유저의 iidx.profiles 공개 여부를 따른다
      (`GET /iidx/profile/{identifier}`와 동일한 규칙) — 비공개면 본인만 조회 가능,
      IIDX 서비스 미가입이면 404.
    - 비율의 **모수는 업로드한 CSV 행 수가 아니라 곡 마스터의 현역 채보
      전체**(iidx.charts, in_ac=true)다. 따라서 CSV에 없는 채보도 NO PLAY로
      집계되어, "게임 전체 대비 클리어 현황"이 된다.
    - 성적 스냅샷이 없으면 전부 NO PLAY(=100%)로 응답한다(404 아님).
    - `available_levels`는 level 필터와 무관하게 해당 스타일에 채보가 존재하는
      전체 레벨 목록이라 FE가 난이도 선택 UI를 구성할 수 있다.
    """
    _check_style(style)
    row = _resolve_identifier(identifier)
    if row is None:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")

    user_id = row["user_id"]
    if not await asyncio.to_thread(crud_profiles.is_iidx_member, user_id):
        raise HTTPException(status_code=404, detail="IIDX 서비스에 가입하지 않은 사용자입니다.")

    is_mine = identity is not None and identity.id == user_id
    if not row.get("iidx_is_public", True) and not is_mine:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")

    rows = await asyncio.to_thread(crud_scores.get_score_summary_rows, user_id, style)
    chart_totals = await asyncio.to_thread(crud_charts.count_charts_by_level, style)
    return build_score_summary(
        rows, play_style=style, level=level, chart_totals=chart_totals
    )


@router.get(
    "/upload-calendar",
    summary="날짜별 업로드 횟수 (기여도 그래프)",
    response_model=UploadCalendarResponse,
    openapi_extra=PUBLIC,
)
async def get_upload_calendar(
    identity: OptionalIdentity,
    identifier: str = Query(..., description="대상 유저의 UUID 또는 handle"),
    style: str | None = Query(None, description="SP 또는 DP. 생략하면 SP+DP 합산"),
    since: date | None = Query(
        None, description="조회 시작 날짜(YYYY-MM-DD, tz 기준). 생략하면 until - (days-1)."
    ),
    until: date | None = Query(
        None, description="조회 종료 날짜(YYYY-MM-DD, tz 기준). 생략하면 tz 기준 오늘."
    ),
    days: int = Query(
        365, ge=1, le=366, description="since를 생략했을 때 until부터 과거로 집계할 일수"
    ),
    tz: str = Query(
        "UTC",
        description="집계 기준 IANA 타임존 (예: Asia/Seoul, Asia/Tokyo, America/New_York). "
        "날짜 경계가 이 타임존 기준으로 정해지며 서머타임도 자동 반영된다.",
    ),
):
    """대상 유저의 날짜별 업로드 횟수를 GitHub 기여도 그래프 형태로 반환한다.

    - identifier(UUID/handle)로 조회한 유저의 iidx.profiles 공개 여부를 따른다
      (`/summary`와 동일한 규칙) — 비공개면 본인만 조회 가능, IIDX 서비스
      미가입이면 404.
    - 날짜 경계는 `tz`(IANA 타임존, 기본 UTC) 기준이다. 일본/미국처럼
      서머타임을 쓰는 지역이어도 zoneinfo가 해당 지역의 DST 규칙을 자동
      반영하므로, 클라이언트는 자신의 로컬 타임존 이름만 넘기면 된다.
    - `since`/`until`을 직접 지정할 수 있다. `until` 생략 시 tz 기준 오늘,
      `since` 생략 시 `until - (days-1)`을 사용한다(둘 다 생략하면 기존처럼
      tz 기준 오늘부터 `days`일 전까지). 업로드가 없는 날짜도 count=0으로
      포함해 FE가 별도 gap-filling 없이 캘린더를 그릴 수 있게 한다.
    - style을 생략하면 SP/DP 업로드를 합산한다.
    """
    if style is not None:
        _check_style(style)
    try:
        zone = ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        raise HTTPException(status_code=422, detail="유효하지 않은 타임존입니다.")

    until_date = until if until is not None else datetime.now(tz=zone).date()
    since_date = since if since is not None else until_date - timedelta(days=days - 1)
    if since_date > until_date:
        raise HTTPException(status_code=422, detail="since는 until보다 이전이거나 같아야 합니다.")
    if (until_date - since_date).days + 1 > 3660:
        raise HTTPException(status_code=422, detail="조회 기간이 너무 깁니다 (최대 3660일).")

    row = _resolve_identifier(identifier)
    if row is None:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")

    user_id = row["user_id"]
    if not await asyncio.to_thread(crud_profiles.is_iidx_member, user_id):
        raise HTTPException(status_code=404, detail="IIDX 서비스에 가입하지 않은 사용자입니다.")

    is_mine = identity is not None and identity.id == user_id
    if not row.get("iidx_is_public", True) and not is_mine:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")

    since_utc = datetime.combine(since_date, time.min, tzinfo=zone).astimezone(timezone.utc)

    rows = await asyncio.to_thread(crud_scores.get_upload_dates, user_id, style, since_utc)
    return build_upload_calendar(
        rows, style=style, tz=tz, since=since_date, until=until_date
    )
