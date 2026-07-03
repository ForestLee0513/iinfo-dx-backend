"""크롤 동기화 작업 실행기 — Redis 체크포인트 기반 이어하기.

작업은 스텝(song_sync → table_sync) 단위로 진행 상태를 Redis에 기록한다.
서버가 스텝 도중 중단되면 재기동 시(resume_interrupted_job) 완료되지 않은
스텝부터 다시 실행한다. 크롤 → RPC 반영은 upsert/전체 교체라 스텝을
처음부터 재실행해도 안전하다(멱등).

수동 실행(어드민 API)과 주간 스케줄 실행이 모두 이 실행기를 거치므로
어느 쪽이든 중단 후 이어하기가 동작한다.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from app.admin import store
from app.core.config import settings
from app.difficulty_crawl.pipeline import run_table_sync
from app.songs_crawl.pipeline import run_song_sync

logger = logging.getLogger(__name__)

STEP_SONG = "song_sync"
STEP_TABLE = "table_sync"

_STEPS_BY_SCOPE = {
    "full": [STEP_SONG, STEP_TABLE],  # 곡 마스터가 먼저 — 순서는 코드로 보장
    "songs": [STEP_SONG],
    "tables": [STEP_TABLE],
}

# 이 프로세스에서 실제 실행 중인 작업 id (Redis의 RUNNING과 달리 살아있음이 보장됨)
_active_job_ids: set[str] = set()
# asyncio 태스크가 GC로 사라지지 않도록 강한 참조 유지
_background_tasks: set[asyncio.Task] = set()


class JobAlreadyRunning(Exception):
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"이미 실행 중인 작업이 있습니다: {job_id}")


def _now() -> str:
    return datetime.now(ZoneInfo(settings.TIMEZONE)).isoformat()


async def create_job(scope: str, triggered_by: str) -> dict:
    """새 작업 생성. 실행 중인 작업이 있으면 JobAlreadyRunning.

    Redis에는 RUNNING인데 이 프로세스에서 실행 중이 아닌 작업(재기동 시
    이어하기까지 실패한 잔재)은 FAILED로 정리하고 새 작업을 만든다.
    """
    current = await store.get_current_job()
    if current and current["status"] == "RUNNING":
        if current["id"] in _active_job_ids:
            raise JobAlreadyRunning(current["id"])
        logger.warning("중단된 채 남은 작업을 FAILED로 정리: %s", current["id"])
        current["status"] = "FAILED"
        current["finished_at"] = _now()
        for step in current["steps"]:
            if step["status"] == "RUNNING":
                step["status"] = "FAILED"
                step["error"] = "서버 중단으로 실행이 끊긴 작업"
        await store.finish_job(current)

    job = {
        "id": uuid.uuid4().hex,
        "scope": scope,
        "status": "RUNNING",
        "triggered_by": triggered_by,
        "steps": [{"name": name, "status": "PENDING", "result": None, "error": None}
                  for name in _STEPS_BY_SCOPE[scope]],
        "resumed": False,
        "created_at": _now(),
        "finished_at": None,
    }
    await store.register_job(job)
    return job


async def execute_job(job: dict) -> dict:
    """스텝을 순차 실행하며 매 스텝 전후로 Redis에 체크포인트를 남긴다.

    곡 마스터 스텝이 실패해도 난이도표 스텝은 계속 진행한다
    (기존 스케줄러와 동일한 정책 — 지난 곡 마스터 기준으로 매핑).
    """
    _active_job_ids.add(job["id"])
    try:
        for step in job["steps"]:
            if step["status"] == "DONE":
                continue  # 이어하기: 완료된 스텝은 건너뜀
            step["status"] = "RUNNING"
            await store.save_job(job)

            try:
                if step["name"] == STEP_SONG:
                    result = await run_song_sync()
                else:
                    result = await run_table_sync(triggered_by=job["triggered_by"])
                step["result"] = result
                step["status"] = "DONE" if result.get("status") == "DONE" else "FAILED"
            except Exception as e:
                logger.exception("작업 스텝 실패: job=%s step=%s", job["id"], step["name"])
                step["status"] = "FAILED"
                step["error"] = str(e)

            await store.save_job(job)

        job["status"] = (
            "DONE" if all(s["status"] == "DONE" for s in job["steps"]) else "FAILED"
        )
        job["finished_at"] = _now()
        await store.finish_job(job)
        logger.info("작업 종료: job=%s status=%s", job["id"], job["status"])
    except Exception:
        # Redis 기록 실패 등 — 작업은 Redis에 RUNNING으로 남고,
        # 재기동 시 resume 또는 create_job의 잔재 정리로 수습된다.
        logger.exception("작업 실행 중 상태 기록 실패: job=%s", job["id"])
    finally:
        _active_job_ids.discard(job["id"])
    return job


def launch(job: dict) -> None:
    """작업을 백그라운드로 실행 (어드민 API는 즉시 응답, FE가 폴링)."""
    task = asyncio.create_task(execute_job(job))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def resume_interrupted_job() -> None:
    """앱 기동 시 호출 — Redis에 RUNNING으로 남은 작업을 이어서 실행한다."""
    try:
        job = await store.get_current_job()
    except Exception:
        logger.warning("Redis 연결 실패 — 중단된 작업 이어하기 생략", exc_info=True)
        return

    if not job or job["status"] != "RUNNING":
        return

    for step in job["steps"]:
        if step["status"] == "RUNNING":
            step["status"] = "PENDING"  # 중단된 스텝은 처음부터 다시 (멱등이라 안전)
    job["resumed"] = True
    await store.save_job(job)
    logger.info(
        "중단된 작업 이어하기: job=%s 남은 스텝=%s",
        job["id"],
        [s["name"] for s in job["steps"] if s["status"] != "DONE"],
    )
    launch(job)
