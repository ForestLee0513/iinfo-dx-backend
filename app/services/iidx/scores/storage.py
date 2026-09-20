"""Supabase Storage 기반 성적 CSV 파일 저장소.

버킷: IIDX_SCORE_BUCKET (기본값 "iidx-score-csv", .env로 오버라이드 가능).
경로 형식: {user_id}/{play_style}/{upload_id}.csv

AWS S3 이관 방법:
  Supabase Storage는 S3-compatible REST API를 제공한다. 이관 시 이 모듈만 수정하면 된다:
  boto3.client('s3', endpoint_url='https://<project>.supabase.co/storage/v1/s3',
               aws_access_key_id=<access_key>, aws_secret_access_key=<secret>)
  로 교체하면 동일한 put_object / generate_presigned_url 호출로 대체 가능.
  Storage 경로(user_id/play_style/upload_id.csv) 구조는 그대로 유지된다.
"""

import asyncio
import logging

from app.core.config import settings
from app.db.session import get_supabase_iidx

logger = logging.getLogger(__name__)

SCORE_BUCKET: str = settings.IIDX_SCORE_BUCKET


def _storage():
    return get_supabase_iidx().storage


def ensure_bucket() -> None:
    """버킷이 없으면 비공개 버킷을 생성한다. 이미 있으면 무시."""
    try:
        _storage().create_bucket(SCORE_BUCKET, options={"public": False})
        logger.info("Storage 버킷 생성: %s", SCORE_BUCKET)
    except Exception as e:
        # 이미 존재하는 경우 등 — 무시하고 진행
        logger.debug("Storage 버킷 ensure 건너뜀 (%s): %s", SCORE_BUCKET, e)


def upload_csv(path: str, content: bytes) -> str:
    """CSV 바이트를 Supabase Storage에 업로드하고 경로를 반환한다."""
    _storage().from_(SCORE_BUCKET).upload(
        path=path,
        file=content,
        file_options={"content-type": "text/csv; charset=utf-8"},
    )
    return path


def get_signed_url(path: str, expires_in: int = 3600) -> str:
    """서명된 임시 다운로드 URL을 반환한다 (기본 1시간)."""
    result = _storage().from_(SCORE_BUCKET).create_signed_url(path, expires_in)
    return result.get("signedURL") or result.get("signed_url", "")


def remove_paths(paths: list[str]) -> None:
    """지정한 경로들의 CSV 파일을 Storage에서 삭제한다. IIDX 서비스 탈퇴 시 사용."""
    if not paths:
        return
    _storage().from_(SCORE_BUCKET).remove(paths)


async def cleanup_user_storage(user_id: str) -> None:
    """전체 회원 탈퇴(계정 삭제) 시 사용자의 CSV 파일을 모두 지운다.

    app.services.account.auth_service.register_pre_delete_hook에 등록되어
    auth.users 삭제(DB cascade) 직전에 호출된다 — cascade가 score_uploads 행을
    지워버리면 storage_path를 잃어 파일을 찾을 방법이 없어지므로, 행이 아직
    남아있는 지금 경로를 조회해 먼저 지운다.
    """
    from app.crud.iidx import scores as crud_scores

    paths = await asyncio.to_thread(crud_scores.get_all_storage_paths, user_id)
    if paths:
        await asyncio.to_thread(remove_paths, paths)


async def upload_csv_async(path: str, content: bytes) -> str:
    return await asyncio.to_thread(upload_csv, path, content)


async def get_signed_url_async(path: str, expires_in: int = 3600) -> str:
    return await asyncio.to_thread(get_signed_url, path, expires_in)


async def remove_paths_async(paths: list[str]) -> None:
    await asyncio.to_thread(remove_paths, paths)
