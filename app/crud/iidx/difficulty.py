"""Supabase 동기화(쓰기) — sync_table_result RPC 호출.

표 데이터/RPC/로그 모두 iidx 스키마(get_supabase_svc)에 둔다. 실패 로그는 여기서
iidx.crawl_sync_logs에 직접 기록하고, 성공 로그는 RPC 내부에서 남긴다.
스키마/RPC 정의: supabase/migrations/20260803000000_baseline.sql
"""

import logging

from app.db.session import get_supabase_svc
from app.services.iidx.difficulty_crawl.crawlers.base import TableResult

logger = logging.getLogger(__name__)


def sync_table_result(result: TableResult, triggered_by: str) -> dict:
    """TableResult 하나를 Supabase에 반영 (표 upsert + 엔트리 전체 교체).

    동기(블로킹) 함수이므로 비동기 컨텍스트에서는 asyncio.to_thread로 호출할 것.
    """
    t = result.table
    res = get_supabase_svc().rpc(
        "sync_table_result",
        {
            "p_table": {
                "slug": t.slug,
                "name": t.name,
                "source": t.source,
                "play_style": t.play_style,
                "rating_type": t.rating_type,
                "level": t.level,
                "grades": t.grades,
            },
            "p_entries": result.entries,
            "p_triggered_by": triggered_by,
        },
    ).execute()
    return res.data


def log_sync_failure(
    crawler: str, url: str | None, error: str, triggered_by: str
) -> None:
    """크롤링 단계 실패를 iidx.crawl_sync_logs에 기록 (실패 기록 자체는 best-effort)."""
    try:
        get_supabase_svc().table("crawl_sync_logs").insert({
            "crawler": crawler,
            "url": url,
            "triggered_by": triggered_by,
            "status": "FAILED",
            "error": error[:2000],
        }).execute()
    except Exception:
        logger.exception("실패 로그 기록 중 오류 (무시하고 진행)")
