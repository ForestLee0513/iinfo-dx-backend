"""크롤 대상/스케줄 Supabase 저장소 — crawl_targets, crawl_schedules 테이블.

동기(블로킹) 함수이므로 비동기 컨텍스트(app/services/admin/store.py)에서는
asyncio.to_thread로 호출한다. 스키마: supabase/migrations/20260718000000_crawl_targets_schedules.sql

target_key(f"{kind}:{id}")가 PK이므로 같은 kind 안에서 id 중복은 애초에 불가능하다.
crawl_schedules.target_key는 crawl_targets.target_key를 on delete cascade로 참조하므로
대상을 지우면 스케줄 행도 DB에서 자동으로 함께 삭제된다.
"""

from app.db.session import get_supabase


def _row_to_target(row: dict) -> dict:
    """DB 행 -> 기존 Redis JSON blob과 동일한 모양(kind/id/label/crawler + 크롤러별 설정)."""
    return {
        "kind": row["kind"],
        "id": row["target_id"],
        "label": row["label"],
        "crawler": row["crawler"],
        **(row.get("config") or {}),
    }


def list_targets() -> dict[str, dict]:
    """target_key -> 대상 설정 dict."""
    res = get_supabase().table("crawl_targets").select("*").execute()
    return {row["target_key"]: _row_to_target(row) for row in res.data}


def get_target(target_key: str) -> dict | None:
    res = (
        get_supabase()
        .table("crawl_targets")
        .select("*")
        .eq("target_key", target_key)
        .execute()
    )
    return _row_to_target(res.data[0]) if res.data else None


def upsert_target(target_key: str, config: dict) -> None:
    """대상 생성/전체 교체. config는 kind/id/label/crawler + 크롤러별 추가 키를 포함한다."""
    extra = {k: v for k, v in config.items() if k not in ("kind", "id", "label", "crawler")}
    row = {
        "target_key": target_key,
        "kind": config["kind"],
        "target_id": config["id"],
        "label": config["label"],
        "crawler": config["crawler"],
        "config": extra,
    }
    get_supabase().table("crawl_targets").upsert(row).execute()


def delete_target(target_key: str) -> None:
    get_supabase().table("crawl_targets").delete().eq("target_key", target_key).execute()


def list_schedules() -> dict[str, dict]:
    """target_key -> {enabled, triggers}."""
    res = get_supabase().table("crawl_schedules").select("*").execute()
    return {row["target_key"]: {"enabled": row["enabled"], "triggers": row["triggers"]} for row in res.data}


def get_schedule(target_key: str) -> dict | None:
    res = (
        get_supabase()
        .table("crawl_schedules")
        .select("*")
        .eq("target_key", target_key)
        .execute()
    )
    if not res.data:
        return None
    row = res.data[0]
    return {"enabled": row["enabled"], "triggers": row["triggers"]}


def upsert_schedule(target_key: str, config: dict) -> None:
    get_supabase().table("crawl_schedules").upsert({
        "target_key": target_key,
        "enabled": config["enabled"],
        "triggers": config["triggers"],
    }).execute()


def delete_schedule(target_key: str) -> None:
    get_supabase().table("crawl_schedules").delete().eq("target_key", target_key).execute()
