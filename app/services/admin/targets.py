"""크롤 대상(song/table target) 레지스트리 — Redis(crawl:targets)에 저장, 어드민 API로 CRUD.

target_key = f"{kind}:{id}"가 Redis 해시(crawl:targets)의 field 자체이므로, 같은 kind
안에서 id가 중복되는 대상은 존재할 수 없다(같은 field에 덮어쓰기된다).

기존에는 SONG_CRAWL_TARGETS/TABLE_CRAWL_TARGETS(.env)가 대상 정의였지만, 이제 Redis가
유일한 저장소다 — env 기본값이나 폴백은 없다.
"""

from app.services.admin import store


async def list_targets() -> list[dict]:
    """저장된 전체 대상. 각 dict는 kind/id/label/crawler + 크롤러별 설정(url 등)을 포함한다."""
    raw = await store.load_all_targets()
    return [{"key": key, **config} for key, config in sorted(raw.items())]


async def get_target(target_key: str) -> dict | None:
    config = await store.load_target(target_key)
    return {"key": target_key, **config} if config else None
