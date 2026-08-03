"""크롤 대상(song/table target) 레지스트리 — Supabase(crawl_targets)에 저장, 어드민 API로 CRUD.

target_key = f"{kind}:{id}"가 crawl_targets 테이블의 PK이므로, 같은 kind 안에서 id가
중복되는 대상은 존재할 수 없다(같은 행에 덮어쓰기된다).

대상은 어드민 API(POST /iidx/crawl/targets)로 등록한다 — 등록된 대상만 잡/스케줄에서 참조된다.
"""

from app.services.iidx.admin import store


async def list_targets() -> list[dict]:
    """저장된 전체 대상. 각 dict는 kind/id/label/crawler + 크롤러별 설정(url 등)을 포함한다."""
    raw = await store.load_all_targets()
    return [{"key": key, **config} for key, config in sorted(raw.items())]


async def get_target(target_key: str) -> dict | None:
    config = await store.load_target(target_key)
    return {"key": target_key, **config} if config else None
