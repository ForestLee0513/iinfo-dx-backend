"""주 1회 크롤링 동기화 스케줄러 (APScheduler).

기본: 매주 월요일 05:00 Asia/Seoul — .env의 CRAWL_SCHEDULE_*로 변경 가능.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.crawl.pipeline import run_full_sync

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)

JOB_ID = "weekly-crawl-sync"


def start() -> None:
    scheduler.add_job(
        run_full_sync,
        CronTrigger(
            day_of_week=settings.CRAWL_SCHEDULE_DAY,
            hour=settings.CRAWL_SCHEDULE_HOUR,
            minute=settings.CRAWL_SCHEDULE_MINUTE,
            timezone=settings.TIMEZONE,
        ),
        id=JOB_ID,
        kwargs={"triggered_by": "schedule"},
        replace_existing=True,
        coalesce=True,  # 밀린 실행이 여러 번 쌓여도 한 번만 실행
        misfire_grace_time=3600,
    )
    scheduler.start()
    job = scheduler.get_job(JOB_ID)
    logger.info(
        "주간 크롤링 스케줄 등록: 매주 %s %02d:%02d %s (다음 실행: %s)",
        settings.CRAWL_SCHEDULE_DAY,
        settings.CRAWL_SCHEDULE_HOUR,
        settings.CRAWL_SCHEDULE_MINUTE,
        settings.TIMEZONE,
        job.next_run_time if job else "N/A",
    )


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
