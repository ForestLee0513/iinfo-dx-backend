"""곡 마스터 크롤 파이프라인.

SONG_CRAWL_TARGETS 순회 -> 각 타깃의 'crawler' 값으로 크롤러 선택 -> RPC 반영.
스케줄 진입점은 difficulty_crawl/scheduler.py (곡 마스터 -> 난이도표 순차 실행).
수동 실행은 이 레포에 두지 않는다 (admin 레포에서 RPC 직접 호출).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

# 크롤러 모듈 import 시점에 레지스트리에 등록되므로 반드시 import 유지
from app.core.config import settings
from app.services.songs_crawl.crawlers import textage  # noqa: F401
from app.services.songs_crawl.crawlers.base import SongMasterResult, get_crawler
from app.crud.crud_songs import sync_song_master

logger = logging.getLogger(__name__)

# 동시 실행 방지 (스케줄 재진입 대비)
_sync_lock = asyncio.Lock()


async def crawl_song_targets() -> list[SongMasterResult]:
    """SONG_CRAWL_TARGETS 전체를 크롤만 수행 (동기화 없음). preview에서도 사용."""
    results: list[SongMasterResult] = []

    async with httpx.AsyncClient() as client:
        for target in settings.SONG_CRAWL_TARGETS:
            crawler = get_crawler(target["crawler"])
            results.extend(await crawler.crawl(client, target))

    return results


async def run_song_sync() -> dict:
    """곡 마스터 크롤 + Supabase 반영. 스케줄러에서 난이도표 동기화보다 먼저 호출된다."""
    if _sync_lock.locked():
        return {"status": "SKIPPED", "reason": "곡 마스터 동기화가 이미 진행 중입니다."}

    async with _sync_lock:
        all_results = []

        for target in settings.SONG_CRAWL_TARGETS:
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

        return {"status": "DONE", "results": all_results}
