"""북마크릿 업로드용 단기 토큰 관리.

웹 FE에서 POST /iidx/scores/token으로 발급받아 북마크릿에 복붙한다.
Redis에 user_id를 1시간 TTL로 저장; 업로드·프로필 동기화 엔드포인트에서 검증.
"""

import uuid

from app.db.redis import get_redis

_PREFIX = "iidx:upload_token:"
TTL = 3600  # 1시간


async def create_token(user_id: str) -> str:
    """user_id에 매핑된 단기 업로드 토큰을 발급하고 반환한다."""
    token = uuid.uuid4().hex
    await get_redis().set(_PREFIX + token, user_id, ex=TTL)
    return token


async def get_user_id(token: str) -> str | None:
    """토큰으로 user_id를 조회한다. 만료·미존재 시 None."""
    return await get_redis().get(_PREFIX + token)


async def revoke_token(token: str) -> None:
    """토큰을 즉시 무효화한다."""
    await get_redis().delete(_PREFIX + token)
