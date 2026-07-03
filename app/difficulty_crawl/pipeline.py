"""크롤링 → Supabase 동기화 실행 파이프라인."""

import asyncio
import logging

import httpx

from app.core.config import settings
from app.difficulty_crawl.crawlers import get_crawler  # import 시 크롤러 레지스트리 등록됨
from app.difficulty_crawl.sync import log_sync_failure, sync_table_result

logger = logging.getLogger(__name__)
_sync_lock = asyncio.Lock()


async def run_table_sync(triggered_by: str = "schedule") -> dict:
    """
    TABLE_CRAWL_TARGETS 순회 → 각 타깃의 'crawler' 값으로 크롤러 선택 → Supabase 반영.
    주간 스케줄러에서만 호출된다(수동 트리거 엔드포인트 없음).
    """
    if _sync_lock.locked():
        return {"status": "SKIPPED", "reason": "이미 동기화가 진행 중입니다."}

    async with _sync_lock:
        logger.info("크롤링 동기화 시작 (triggered_by=%s)", triggered_by)
        all_results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for target in settings.TABLE_CRAWL_TARGETS:
                crawler_name = target["crawler"]

                try:
                    table_results = await get_crawler(crawler_name).crawl(client, target)
                except Exception as e:
                    logger.exception("crawl failed: %s", target)
                    await asyncio.to_thread(
                        log_sync_failure,
                        crawler_name, target.get("url"), str(e), triggered_by,
                    )
                    all_results.append({
                        "crawler": crawler_name, "target": target.get("url"),
                        "status": "FAILED", "error": str(e),
                    })
                    continue

                for tr in table_results:
                    try:
                        res = await asyncio.to_thread(sync_table_result, tr, triggered_by)
                    except Exception as e:
                        logger.exception("sync failed: %s", tr.table.slug)
                        res = {
                            "slug": tr.table.slug,
                            "status": "FAILED", "error": str(e),
                        }
                    all_results.append(res)

        logger.info(
            "크롤링 동기화 완료 (triggered_by=%s, results=%d)",
            triggered_by, len(all_results),
        )
        return {
            "status": "DONE",
            "triggered_by": triggered_by,
            "results": all_results,
        }
