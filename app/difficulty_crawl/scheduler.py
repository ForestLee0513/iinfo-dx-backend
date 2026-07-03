"""곡 마스터 → 난이도표 순차 동기화 주간 스케줄러 (APScheduler).

스케줄러는 여기 하나만 존재. 순서는 시간차가 아니라 코드로 보장한다:
곡 마스터가 먼저 갱신돼야 난이도표 매핑(제목 매칭)이 최신 기준으로 동작한다.
곡 마스터가 실패해도 난이도표 동기화는 지난 곡 마스터 기준으로 계속 진행한다.

기본: 매주 월요일 05:00 Asia/Seoul — .env의 CRAWL_SCHEDULE_*로 변경 가능.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.difficulty_crawl.pipeline import run_table_sync
from app.songs_crawl.pipeline import run_song_sync

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)

JOB_ID = "weekly-full-sync"


async def run_weekly_sync(triggered_by: str = "schedule") -> None:
    """주간 전체 동기화: 곡 마스터 → 난이도표 순차 실행."""
    song_result = await run_song_sync()
    logger.info("곡 마스터 동기화 결과: %s", song_result)
    if song_result.get("status") != "DONE":
        logger.warning("곡 마스터 동기화가 정상 완료되지 않음 — 난이도표는 기존 곡 마스터 기준으로 진행")

    table_result = await run_table_sync(triggered_by=triggered_by)
    logger.info("난이도표 동기화 결과: %s", table_result)


def start() -> None:
    scheduler.add_job(
        run_weekly_sync,
        CronTrigger(
            day_of_week=settings.CRAWL_SCHEDULE_DAY,
            hour=settings.CRAWL_SCHEDULE_HOUR,
            minute=settings.CRAWL_SCHEDULE_MINUTE,
            timezone=settings.TIMEZONE,
        ),
        id=JOB_ID,
        kwargs={"triggered_by": "schedule"},
        replace_existing=True,
        max_instances=1,  # 중복 실행 방지
        coalesce=True,  # 밀린 실행이 여러 번 쌓여도 한 번만 실행
        misfire_grace_time=3600,
    )
    scheduler.start()
    job = scheduler.get_job(JOB_ID)
    logger.info(
        "주간 동기화 스케줄 등록 (곡 마스터 → 난이도표): 매주 %s %02d:%02d %s (다음 실행: %s)",
        settings.CRAWL_SCHEDULE_DAY,
        settings.CRAWL_SCHEDULE_HOUR,
        settings.CRAWL_SCHEDULE_MINUTE,
        settings.TIMEZONE,
        job.next_run_time if job else "N/A",
    )


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
