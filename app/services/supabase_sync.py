"""Supabase 동기화 — sync_table_result RPC 호출.

service role 키를 사용하므로 RLS를 우회한다. 이 키는 절대 클라이언트에
노출하면 안 되며 백엔드 환경변수로만 관리한다.

스키마/RPC 정의: supabase/migrations/20260702000000_crawl_sync.sql
"""

import logging
from functools import lru_cache

from supabase import Client, create_client

from app.core.config import settings
from app.crawlers.base import TableResult

logger = logging.getLogger(__name__)


@lru_cache
def get_supabase() -> Client:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 환경변수가 필요합니다"
        )
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)


def sync_table_result(result: TableResult, triggered_by: str) -> dict:
    """TableResult 하나를 Supabase에 반영 (표 upsert + 엔트리 전체 교체).

    동기(블로킹) 함수이므로 비동기 컨텍스트에서는 asyncio.to_thread로 호출할 것.
    """
    t = result.table
    res = get_supabase().rpc(
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
    """크롤링 단계 실패를 crawl_sync_logs에 기록 (실패 기록 자체는 best-effort)."""
    try:
        get_supabase().table("crawl_sync_logs").insert({
            "crawler": crawler,
            "url": url,
            "triggered_by": triggered_by,
            "status": "FAILED",
            "error": error[:2000],
        }).execute()
    except Exception:
        logger.exception("실패 로그 기록 중 오류 (무시하고 진행)")
