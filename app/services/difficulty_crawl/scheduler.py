"""곡 마스터 → 난이도표 순차 동기화 주간 스케줄러 (APScheduler).

스케줄러는 여기 하나만 존재. 순서는 시간차가 아니라 코드로 보장한다:
곡 마스터가 먼저 갱신돼야 난이도표 매핑(제목 매칭)이 최신 기준으로 동작한다.
곡 마스터가 실패해도 난이도표 동기화는 지난 곡 마스터 기준으로 계속 진행한다.

기본: .env의 CRAWL_SCHEDULE_* (매주 월요일 05:00 Asia/Seoul).
어드민 API(PUT /admin/crawl/schedule)로 변경하면 Redis에 저장되어
재기동 후에도 유지되며 .env 기본값보다 우선한다.

스케줄 실행도 어드민 작업 실행기(app.services.admin.jobs)를 거치므로
실행 도중 서버가 중단돼도 재기동 시 남은 스텝부터 이어서 실행된다.
"""

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.services.admin import jobs as admin_jobs
from app.services.admin import store as admin_store
from app.core.config import settings
from app.services.difficulty_crawl.pipeline import run_table_sync
from app.services.songs_crawl.pipeline import run_song_sync

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)

JOB_ID = "weekly-full-sync"


def _env_schedule() -> dict:
    return {
        "enabled": settings.CRAWL_SCHEDULE_ENABLED,
        "day": settings.CRAWL_SCHEDULE_DAY,
        "hour": settings.CRAWL_SCHEDULE_HOUR,
        "minute": settings.CRAWL_SCHEDULE_MINUTE,
    }


async def get_effective_schedule() -> tuple[dict, str]:
    """(설정, 출처) 반환 — Redis 오버라이드가 있으면 그것을, 없으면 .env 기본값."""
    try:
        saved = await admin_store.load_schedule()
    except Exception:
        logger.warning("Redis에서 스케줄을 읽지 못함 — .env 기본값 사용", exc_info=True)
        saved = None
    return (saved, "redis") if saved else (_env_schedule(), "env")


async def run_weekly_sync() -> None:
    """주간 전체 동기화 — 작업 실행기를 거쳐 Redis에 진행 상태를 남긴다."""
    try:
        job = await admin_jobs.create_job(scope="full", triggered_by="schedule")
    except admin_jobs.JobAlreadyRunning as e:
        logger.warning("이미 실행 중인 동기화 작업이 있어 주간 실행 건너뜀: %s", e.job_id)
        return
    except Exception:
        # Redis 불능 시에도 주간 동기화 자체는 멈추지 않는다 (작업 추적만 포기)
        logger.warning("Redis 사용 불가 — 작업 추적 없이 주간 동기화 실행", exc_info=True)
        song_result = await run_song_sync()
        logger.info("곡 마스터 동기화 결과: %s", song_result)
        if song_result.get("status") != "DONE":
            logger.warning("곡 마스터 동기화가 정상 완료되지 않음 — 난이도표는 기존 곡 마스터 기준으로 진행")
        table_result = await run_table_sync(triggered_by="schedule")
        logger.info("난이도표 동기화 결과: %s", table_result)
        return

    await admin_jobs.execute_job(job)


def apply_schedule(config: dict) -> None:
    """실행 중인 스케줄러에 스케줄 설정을 반영한다. enabled=False면 잡 제거."""
    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)

    if not config["enabled"]:
        logger.info("주간 동기화 스케줄 비활성화됨")
        return

    scheduler.add_job(
        run_weekly_sync,
        CronTrigger(
            day_of_week=config["day"],
            hour=config["hour"],
            minute=config["minute"],
            timezone=settings.TIMEZONE,
        ),
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,  # 중복 실행 방지
        coalesce=True,  # 밀린 실행이 여러 번 쌓여도 한 번만 실행
        misfire_grace_time=3600,
    )
    logger.info(
        "주간 동기화 스케줄 등록 (곡 마스터 → 난이도표): 매주 %s %02d:%02d %s (다음 실행: %s)",
        config["day"], config["hour"], config["minute"],
        settings.TIMEZONE, next_run_time(),
    )


def next_run_time() -> datetime | None:
    job = scheduler.get_job(JOB_ID)
    return job.next_run_time if job else None


async def start() -> None:
    scheduler.start()
    config, source = await get_effective_schedule()
    logger.info("주간 동기화 스케줄 로드 (출처=%s)", source)
    apply_schedule(config)


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
