"""공통 인프라 — Redis 비동기 클라이언트.

어드민 크롤 작업 상태와 스케줄 오버라이드를 저장한다.
서버가 중단됐다 재기동해도 Redis에 남은 작업 상태를 읽어 이어서 실행한다.
"""

import redis.asyncio as redis

from app.core.config import settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
