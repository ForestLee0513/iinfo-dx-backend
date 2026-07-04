"""admin 모듈 라우터 (/admin/*) — 별도 어드민 FE 전용 API.

- 크롤 동기화 수동 실행: POST /crawl/jobs (즉시 202 응답, FE가 폴링으로 확인)
- 작업 조회: GET /crawl/jobs, GET /crawl/jobs/{id}
- 주간 크롤 스케줄 조회/변경: GET·PUT /crawl/schedule
  (변경값은 Redis에 저장되어 재기동 후에도 유지, .env 기본값보다 우선)

모든 엔드포인트는 Supabase 토큰 검증 + ADMIN_EMAILS 화이트리스트로 보호한다.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status
from redis.exceptions import RedisError

from app.services.admin import jobs, store
from app.api.deps import AdminUser
from app.schemas.admin import (
    CrawlJob,
    JobCreateRequest,
    JobListResponse,
    ScheduleConfig,
    ScheduleResponse,
)
from app.core.config import settings
from app.services.difficulty_crawl import scheduler

logger = logging.getLogger(__name__)

router = APIRouter()

_REDIS_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Redis에 연결할 수 없습니다.",
)


# ── 크롤 수동 실행 ────────────────────────────────────


@router.post("/crawl/jobs", response_model=CrawlJob, status_code=status.HTTP_202_ACCEPTED)
async def trigger_crawl(body: JobCreateRequest, user: AdminUser):
    """크롤 동기화 작업을 생성하고 백그라운드로 실행한다. FE는 작업 id로 폴링."""
    try:
        job = await jobs.create_job(scope=body.scope, triggered_by=f"admin:{user.email}")
    except jobs.JobAlreadyRunning as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 실행 중인 작업이 있습니다: {e.job_id}",
        )
    except RedisError:
        raise _REDIS_UNAVAILABLE
    jobs.launch(job)
    return job


@router.get("/crawl/jobs", response_model=JobListResponse)
async def list_crawl_jobs(_: AdminUser, limit: int = Query(20, ge=1, le=50)):
    try:
        return {"jobs": await store.list_jobs(limit)}
    except RedisError:
        raise _REDIS_UNAVAILABLE


@router.get("/crawl/jobs/{job_id}", response_model=CrawlJob)
async def get_crawl_job(job_id: str, _: AdminUser):
    try:
        job = await store.get_job(job_id)
    except RedisError:
        raise _REDIS_UNAVAILABLE
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="작업을 찾을 수 없습니다.")
    return job


# ── 크롤 스케줄 ───────────────────────────────────────


def _schedule_response(config: dict, source: str) -> dict:
    next_run = scheduler.next_run_time()
    return {
        **config,
        "timezone": settings.TIMEZONE,
        "source": source,
        "next_run_at": next_run.isoformat() if next_run else None,
    }


@router.get("/crawl/schedule", response_model=ScheduleResponse)
async def get_schedule(_: AdminUser):
    config, source = await scheduler.get_effective_schedule()
    return _schedule_response(config, source)


@router.put("/crawl/schedule", response_model=ScheduleResponse)
async def update_schedule(body: ScheduleConfig, user: AdminUser):
    """스케줄 변경 — Redis에 저장 후 실행 중인 스케줄러에 즉시 반영."""
    config = body.model_dump()
    try:
        await store.save_schedule(config)
    except RedisError:
        raise _REDIS_UNAVAILABLE
    scheduler.apply_schedule(config)
    logger.info("크롤 스케줄 변경: %s (by %s)", config, user.email)
    return _schedule_response(config, "redis")
