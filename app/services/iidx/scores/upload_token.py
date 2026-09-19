"""북마크릿 업로드용 단기 토큰 관리.

웹 FE에서 POST /iidx/scores/token으로 발급받아 북마크릿에 복붙한다.
Redis에 user_id를 10분 TTL로 저장; 업로드·프로필 동기화 엔드포인트에서 검증.
버튼을 여러 번 눌러도, 아직 만료되지 않은 토큰이 있으면 재발급하지 않고 유지한다
(user_id → token 역방향 인덱스로 조회).
"""

import uuid

from app.db.redis import get_redis

_PREFIX = "iidx:upload_token:"
_USER_PREFIX = "iidx:upload_token_user:"
TTL = 600  # 10분


async def create_token(user_id: str) -> tuple[str, int]:
    """user_id에 매핑된 단기 업로드 토큰을 발급하고 (토큰, 남은 유효시간)을 반환한다.

    아직 만료되지 않은 토큰이 이미 있으면 새로 만들지 않고 기존 토큰을 그대로
    반환한다 — 버튼을 여러 번 눌러도 토큰이 난발되지 않는다.
    """
    redis = get_redis()

    existing = await redis.get(_USER_PREFIX + user_id)
    if existing is not None:
        ttl = await redis.ttl(_PREFIX + existing)
        if ttl > 0:
            return existing, ttl

    token = uuid.uuid4().hex
    await redis.set(_PREFIX + token, user_id, ex=TTL)
    await redis.set(_USER_PREFIX + user_id, token, ex=TTL)
    return token, TTL


async def get_user_id(token: str) -> str | None:
    """토큰으로 user_id를 조회한다. 만료·미존재 시 None."""
    return await get_redis().get(_PREFIX + token)


async def revoke_token(token: str) -> None:
    """토큰을 즉시 무효화한다."""
    redis = get_redis()
    user_id = await redis.get(_PREFIX + token)
    keys = [_PREFIX + token]
    # 역방향 인덱스가 여전히 이 토큰을 가리킬 때만 함께 제거한다.
    if user_id is not None and await redis.get(_USER_PREFIX + user_id) == token:
        keys.append(_USER_PREFIX + user_id)
    await redis.delete(*keys)
