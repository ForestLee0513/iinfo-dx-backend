"""곡 마스터 동기화: SongMasterResult -> sync_song_master RPC (service role 전용).

Supabase 파이썬 클라이언트는 동기식이므로 async 경로에서는
asyncio.to_thread로 감싸서 호출한다 (pipeline.py 참고).
스키마/RPC 정의: supabase/migrations/20260703000000_song_master.sql
"""

from __future__ import annotations

import logging

from app.common.supabase import get_supabase
from app.songs_crawl.crawlers.base import SongMasterResult

logger = logging.getLogger(__name__)

# crawl_sync_logs.table_slug에 기록할 곡 마스터 식별 프리픽스
_LOG_SLUG_PREFIX = "song-master:"


def _log(sb, source: str, count: int | None, status: str, error: str | None) -> None:
    """crawl_sync_logs에 실행 기록. 로그 실패가 동기화를 막지 않게 한다."""
    try:
        sb.table("crawl_sync_logs").insert({
            "table_slug": f"{_LOG_SLUG_PREFIX}{source}",
            "crawler": source,
            "triggered_by": "schedule",
            "entry_count": count or 0,
            "status": status,
            "error": error,
        }).execute()
    except Exception:
        logger.exception("crawl_sync_logs insert 실패")


def sync_song_master(result: SongMasterResult) -> dict:
    """곡 마스터 결과 하나를 Supabase에 반영한다.

    RPC 내부가 단일 트랜잭션: versions/songs/charts upsert 후
    이번 크롤에 없는 곡/채보는 in_ac=false 처리 (삭제하지 않음).
    """
    sb = get_supabase()

    try:
        res = sb.rpc("sync_song_master", {
            "p_payload": {"versions": result.versions, "songs": result.songs},
        }).execute()
        counts = res.data if isinstance(res.data, dict) else {}
        total = counts.get("songs_total", len(result.songs))
        _log(sb, result.source, total, "SUCCESS", None)
        logger.info("곡 마스터 동기화 완료 (%s): songs=%s charts=%s",
                    result.source, counts.get("songs_total"), counts.get("charts_total"))
        return {"source": result.source, "status": "SUCCESS", **counts}
    except Exception as e:
        _log(sb, result.source, None, "FAILED", str(e)[:2000])
        logger.exception("곡 마스터 동기화 실패 (%s)", result.source)
        return {"source": result.source, "status": "FAILED", "error": str(e)}
