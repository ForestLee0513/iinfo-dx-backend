"""크롤 대상별 스케줄러 (APScheduler) — 유일한 스케줄러.

스케줄은 env가 아니라 어드민 대시보드(PUT /crawl/schedules/{target_key})에서
크롤 대상(song/table target) 하나하나에 대해 설정하며 Supabase(crawl_schedules 테이블)에
영구 저장된다(Redis가 유실돼도 사라지지 않음). 대상 하나에 여러 (요일, 시각) 트리거를
지정할 수 있고(OrTrigger로 하나의 job에 결합), 대상별로 독립적으로 켜고 끌 수 있다.

곡 마스터와 난이도표는 이제 서로 다른 스케줄로 독립 실행될 수 있다 — "곡 마스터가
먼저"라는 순서 보장은 더 이상 스케줄 단위로는 하지 않는다. 파이프라인이 이미
곡 마스터 실패/지연 시에도 지난 곡 마스터 기준으로 난이도표 동기화를 진행하도록
허용하므로 실질적인 문제는 없다.

배포 직후에는 등록된 대상/스케줄이 하나도 없으므로 어드민이 대시보드에서
대상을 등록하고 스케줄을 설정하기 전까지는 아무 것도 자동 실행되지 않는다.

스케줄 실행도 어드민 작업 실행기(app.services.admin.jobs)를 거치므로
실행 도중 서버가 중단돼도 재기동 시 남은 스텝부터 이어서 실행된다.
"""

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger

from app.services.admin import jobs as admin_jobs
from app.services.admin import store as admin_store
from app.services.admin import targets as admin_targets
from app.core.config import settings
from app.services.difficulty_crawl.pipeline import run_table_sync
from app.services.songs_crawl.pipeline import run_song_sync

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)


def job_id_for(target_key: str) -> str:
    return f"crawl-target-{target_key}"


def _build_trigger(triggers: list[dict]) -> OrTrigger:
    return OrTrigger([
        CronTrigger(
            day_of_week=t["day"], hour=t["hour"], minute=t["minute"],
            timezone=settings.TIMEZONE,
        )
        for t in triggers
    ])


async def run_target_sync(target_key: str, kind: str, target_id: str) -> None:
    """대상 하나에 대한 동기화 — 작업 실행기를 거쳐 Redis에 진행 상태를 남긴다."""
    try:
        job = await admin_jobs.create_job(scope=kind, target_id=target_id, triggered_by="schedule")
    except admin_jobs.JobAlreadyRunning as e:
        logger.warning(
            "이미 실행 중인 동기화 작업이 있어 스케줄 실행 건너뜀: target=%s job=%s",
            target_key, e.job_id,
        )
        return
    except Exception:
        # Redis 불능 시에도 크롤 자체는 멈추지 않는다 (작업 추적만 포기)
        logger.warning("Redis 사용 불가 — 작업 추적 없이 동기화 실행: target=%s", target_key, exc_info=True)
        if kind == "song":
            result = await run_song_sync(target_id=target_id)
        else:
            result = await run_table_sync(triggered_by="schedule", target_id=target_id)
        logger.info("동기화 결과 (target=%s): %s", target_key, result)
        return

    await admin_jobs.execute_job(job)


def apply_target_schedule(target_key: str, kind: str, target_id: str, config: dict) -> None:
    """실행 중인 스케줄러에 대상 하나의 스케줄 설정을 반영한다.

    enabled=False거나 트리거가 없으면 job을 제거한다.
    """
    job_id = job_id_for(target_key)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    if not config.get("enabled") or not config.get("triggers"):
        logger.info("크롤 대상 스케줄 비활성화됨: %s", target_key)
        return

    scheduler.add_job(
        run_target_sync,
        _build_trigger(config["triggers"]),
        id=job_id,
        kwargs={"target_key": target_key, "kind": kind, "target_id": target_id},
        replace_existing=True,
        max_instances=1,  # 중복 실행 방지
        coalesce=True,  # 밀린 실행이 여러 번 쌓여도 한 번만 실행
        misfire_grace_time=3600,
    )
    logger.info(
        "크롤 대상 스케줄 등록: %s (%d개 트리거, 다음 실행: %s)",
        target_key, len(config["triggers"]), next_run_time(target_key),
    )


def next_run_time(target_key: str) -> datetime | None:
    job = scheduler.get_job(job_id_for(target_key))
    return job.next_run_time if job else None


async def start() -> None:
    scheduler.start()
    try:
        saved = await admin_store.load_all_schedules()
        all_targets = await admin_targets.list_targets()
    except Exception:
        logger.warning("Supabase에서 대상/스케줄을 읽지 못함 — 스케줄 없이 기동", exc_info=True)
        return

    for target in all_targets:
        config = saved.get(target["key"], {"enabled": False, "triggers": []})
        apply_target_schedule(target["key"], target["kind"], target["id"], config)


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
