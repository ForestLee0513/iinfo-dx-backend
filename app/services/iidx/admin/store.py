"""어드민 상태 저장소.

- 크롤 대상/대상별 스케줄: Supabase(crawl_targets/crawl_schedules 테이블)에 영구 저장 —
  Redis가 유실돼도 사라지지 않는다. 실제 I/O는 app/crud/crud_crawl_targets.py(동기)이고,
  여기서는 asyncio.to_thread로 감싸 비동기 인터페이스만 제공한다.
- 크롤 작업(job) 실행 상태(체크포인트/재기동 이어하기)는 휘발성이라 계속 Redis에 저장한다:
  - crawl:jobs:current  실행 중인 작업 id — 재기동 시 이어하기 판단 기준
  - crawl:jobs:index    최근 작업 id 목록 (최신순, 최대 MAX_JOBS개)
  - crawl:job:{id}      작업 상태 JSON (스텝 단위 체크포인트 포함)
"""

import asyncio
import json

from app.crud.iidx import crawl_targets as crud_crawl_targets
from app.db.redis import get_redis

CURRENT_JOB_KEY = "crawl:jobs:current"
JOB_INDEX_KEY = "crawl:jobs:index"
JOB_KEY_PREFIX = "crawl:job:"
MAX_JOBS = 50


# ── 크롤 대상 (Supabase) ──────────────────────────────


async def load_all_targets() -> dict[str, dict]:
    """target_key -> 대상 설정 dict (kind/id/label/crawler + 크롤러별 키)."""
    return await asyncio.to_thread(crud_crawl_targets.list_targets)


async def load_target(target_key: str) -> dict | None:
    return await asyncio.to_thread(crud_crawl_targets.get_target, target_key)


async def save_target(target_key: str, config: dict) -> None:
    await asyncio.to_thread(crud_crawl_targets.upsert_target, target_key, config)


async def delete_target(target_key: str) -> None:
    await asyncio.to_thread(crud_crawl_targets.delete_target, target_key)


# ── 대상별 스케줄 (Supabase) ──────────────────────────


async def load_all_schedules() -> dict[str, dict]:
    """target_key -> 설정 dict. 저장된 적 없는 대상은 포함되지 않는다."""
    return await asyncio.to_thread(crud_crawl_targets.list_schedules)


async def load_schedule(target_key: str) -> dict | None:
    return await asyncio.to_thread(crud_crawl_targets.get_schedule, target_key)


async def save_schedule(target_key: str, config: dict) -> None:
    await asyncio.to_thread(crud_crawl_targets.upsert_schedule, target_key, config)


async def delete_schedule(target_key: str) -> None:
    await asyncio.to_thread(crud_crawl_targets.delete_schedule, target_key)


# ── 작업 (Redis — 휘발성 실행 상태) ────────────────────


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
