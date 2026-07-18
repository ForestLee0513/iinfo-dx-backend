"""곡 마스터 크롤 파이프라인.

크롤 대상은 어드민 API(POST /crawl/targets)로 Redis에 등록된 것을 사용한다(kind="song").
스케줄 진입점은 difficulty_crawl/scheduler.py (곡 마스터 -> 난이도표 순차 실행).
수동 실행은 이 레포에 두지 않는다 (admin 레포에서 RPC 직접 호출).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

# 크롤러 모듈 import 시점에 레지스트리에 등록되므로 반드시 import 유지
from app.services.songs_crawl.crawlers import textage  # noqa: F401
from app.services.songs_crawl.crawlers.base import get_crawler
from app.services.admin import targets as admin_targets
from app.crud.crud_songs import sync_song_master

logger = logging.getLogger(__name__)

# 동시 실행 방지 (스케줄 재진입 대비)
_sync_lock = asyncio.Lock()


async def run_song_sync(target_id: str | None = None, target: dict | None = None) -> dict:
    """곡 마스터 크롤 + Supabase 반영.

    target_id 지정 시 등록된(Redis) 대상 중 해당 id 하나만 동기화한다
    (어드민 대상별 스케줄/수동 실행용).
    target 지정 시 등록 여부와 무관하게 그 설정 그대로 동기화한다
    (어드민이 body로 직접 내린 ad-hoc 대상).
    """
    if _sync_lock.locked():
        return {"status": "SKIPPED", "reason": "곡 마스터 동기화가 이미 진행 중입니다."}

    if target is not None:
        targets = [target]
    else:
        all_targets = await admin_targets.list_targets()
        targets = [t for t in all_targets if t["kind"] == "song"]
        if target_id is not None:
            targets = [t for t in targets if t["id"] == target_id]
            if not targets:
                raise ValueError(f"곡 마스터 크롤 타깃을 찾을 수 없습니다: {target_id}")

    async with _sync_lock:
        all_results = []

        for target in targets:
            try:
                async with httpx.AsyncClient() as client:
                    crawled = await get_crawler(target["crawler"]).crawl(client, target)
            except Exception as e:
                logger.exception("곡 마스터 크롤 실패: %s", target)
                all_results.append({
                    "crawler": target.get("crawler"),
                    "status": "FAILED",
                    "error": str(e),
                })
                continue

            for result in crawled:
                # supabase 클라이언트는 동기식이므로 스레드로 위임
                res = await asyncio.to_thread(sync_song_master, result)
                all_results.append(res)

        failed = [r for r in all_results if r.get("status") == "FAILED"]
        status = "FAILED" if failed else "DONE"
        log = "\n".join(
            f"{r.get('crawler') or '?'}: {r.get('error')}" for r in failed
        ) if failed else ""

        return {"status": status, "log": log, "results": all_results}
