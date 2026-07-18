"""크롤링 → Supabase 동기화 실행 파이프라인."""

import asyncio
import logging

import httpx

from app.services.difficulty_crawl.crawlers import get_crawler  # import 시 크롤러 레지스트리 등록됨
from app.services.admin import targets as admin_targets
from app.crud.crud_difficulty import log_sync_failure, sync_table_result

logger = logging.getLogger(__name__)
_sync_lock = asyncio.Lock()


async def run_table_sync(
    triggered_by: str = "schedule",
    target_id: str | None = None,
    target: dict | None = None,
) -> dict:
    """
    등록된(Redis, kind="table") 크롤 대상을 순회 → 각 타깃의 'crawler' 값으로 크롤러 선택 → Supabase 반영.
    target_id 지정 시 해당 id의 타깃 하나만 동기화한다(어드민 대상별 스케줄/수동 실행용).
    target 지정 시 등록 여부와 무관하게 그 설정 그대로 동기화한다
    (어드민이 body로 직접 내린 ad-hoc 대상).
    """
    if _sync_lock.locked():
        return {"status": "SKIPPED", "reason": "이미 동기화가 진행 중입니다."}

    if target is not None:
        targets = [target]
    else:
        all_targets = await admin_targets.list_targets()
        targets = [t for t in all_targets if t["kind"] == "table"]
        if target_id is not None:
            targets = [t for t in targets if t["id"] == target_id]
            if not targets:
                raise ValueError(f"난이도표 크롤 타깃을 찾을 수 없습니다: {target_id}")

    async with _sync_lock:
        logger.info("크롤링 동기화 시작 (triggered_by=%s, target_id=%s)", triggered_by, target_id)
        all_results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for target in targets:
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

        failed = [r for r in all_results if r.get("status") == "FAILED"]
        status = "FAILED" if failed else "DONE"
        log = "\n".join(
            f"{r.get('crawler') or r.get('slug') or '?'}: {r.get('error')}" for r in failed
        ) if failed else ""

        logger.info(
            "크롤링 동기화 완료 (triggered_by=%s, status=%s, results=%d)",
            triggered_by, status, len(all_results),
        )
        return {
            "status": status,
            "log": log,
            "triggered_by": triggered_by,
            "results": all_results,
        }
