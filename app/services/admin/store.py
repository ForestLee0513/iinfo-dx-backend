"""어드민 상태 저장소 (Redis).

키 구조:
- crawl:schedule      스케줄 오버라이드 JSON (없으면 .env 기본값 사용)
- crawl:jobs:current  실행 중인 작업 id — 재기동 시 이어하기 판단 기준
- crawl:jobs:index    최근 작업 id 목록 (최신순, 최대 MAX_JOBS개)
- crawl:job:{id}      작업 상태 JSON (스텝 단위 체크포인트 포함)
"""

import json

from app.db.redis import get_redis

SCHEDULE_KEY = "crawl:schedule"
CURRENT_JOB_KEY = "crawl:jobs:current"
JOB_INDEX_KEY = "crawl:jobs:index"
JOB_KEY_PREFIX = "crawl:job:"
MAX_JOBS = 50


# ── 스케줄 ────────────────────────────────────────────


async def load_schedule() -> dict | None:
    raw = await get_redis().get(SCHEDULE_KEY)
    return json.loads(raw) if raw else None


async def save_schedule(config: dict) -> None:
    await get_redis().set(SCHEDULE_KEY, json.dumps(config))


# ── 작업 ──────────────────────────────────────────────


async def save_job(job: dict) -> None:
    await get_redis().set(f"{JOB_KEY_PREFIX}{job['id']}", json.dumps(job, ensure_ascii=False))


async def register_job(job: dict) -> None:
    """새 작업 저장 + 최근 목록 등록 + 실행 중 작업으로 표시."""
    r = get_redis()
    await save_job(job)
    async with r.pipeline(transaction=True) as pipe:
        pipe.lpush(JOB_INDEX_KEY, job["id"])
        pipe.ltrim(JOB_INDEX_KEY, 0, MAX_JOBS - 1)
        pipe.set(CURRENT_JOB_KEY, job["id"])
        await pipe.execute()


async def finish_job(job: dict) -> None:
    """작업 종료 상태 저장 + 실행 중 표시 해제."""
    await save_job(job)
    await get_redis().delete(CURRENT_JOB_KEY)


async def get_job(job_id: str) -> dict | None:
    raw = await get_redis().get(f"{JOB_KEY_PREFIX}{job_id}")
    return json.loads(raw) if raw else None


async def get_current_job() -> dict | None:
    job_id = await get_redis().get(CURRENT_JOB_KEY)
    return await get_job(job_id) if job_id else None


async def list_jobs(limit: int = 20) -> list[dict]:
    job_ids = await get_redis().lrange(JOB_INDEX_KEY, 0, limit - 1)
    jobs = []
    for job_id in job_ids:
        job = await get_job(job_id)
        if job:
            jobs.append(job)
    return jobs
