"""admin 모듈 라우터 (/admin/*) — 별도 어드민 FE 전용 API.

- 크롤 동기화 수동 실행: POST /crawl/jobs (즉시 202 응답, FE가 폴링으로 확인)
- 작업 조회: GET /crawl/jobs, GET /crawl/jobs/{id}
- 주간 크롤 스케줄 조회/변경: GET·PUT /crawl/schedule
  (변경값은 Redis에 저장되어 재기동 후에도 유지, .env 기본값보다 우선)
- 회원 관리: GET /users (목록), GET /users/{id} (상세), 접근 제한 부여/해제/이력

모든 엔드포인트는 Supabase 토큰 검증 + ADMIN_EMAILS 화이트리스트로 보호한다.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query, status
from redis.exceptions import RedisError

from app.services import auth_service
from app.services.admin import jobs, store
from app.api.deps import AdminUser, SuperAdminUser
from app.crud import crud_bans, crud_profiles, crud_users
from app.schemas.admin import (
    AdminUserDetail,
    AdminUserListResponse,
    BanListResponse,
    BanRecord,
    BanRequest,
    CrawlJob,
    JobCreateRequest,
    JobListResponse,
    RoleUpdateRequest,
    RoleUpdateResponse,
    ScheduleConfig,
    ScheduleResponse,
    UserProfileDetail,
)
from app.schemas.user import ROLE_LEVELS, UserRole
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


# ── 회원 조회 ─────────────────────────────────────────


def _auth_error_to_http(e: auth_service.AuthServiceError) -> HTTPException:
    """GoTrue Admin API 오류를 HTTP 응답으로 변환한다."""
    if e.status_code == status.HTTP_404_NOT_FOUND:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="사용자를 찾을 수 없습니다."
        )
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=e.message)


def _user_summary_fields(user: dict, active_ban: dict | None) -> dict:
    """GoTrue 사용자 객체 + 현재 정지 레코드에서 목록/상세 공통 필드를 추출한다."""
    app_metadata = user.get("app_metadata") or {}
    return {
        "id": user["id"],
        "email": user.get("email"),
        "provider": app_metadata.get("provider"),
        "created_at": user["created_at"],
        "last_sign_in_at": user.get("last_sign_in_at"),
        "is_banned": active_ban is not None,
        "ban_reason": active_ban["reason"] if active_ban else None,
        "ban_until": active_ban["ban_until"] if active_ban else None,
    }


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    _: AdminUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    email: str | None = Query(None, min_length=1, description="이메일 부분 일치 검색"),
    provider: str | None = Query(
        None, min_length=1, description='가입 경로 필터 ("google" | "email" 등)'
    ),
    is_banned: bool | None = Query(
        None, description="정지 여부 필터 — true=정지 중, false=활성(정지 아님)"
    ),
):
    """회원 목록 조회 — auth.users 직접 조회 (admin_list_users RPC).

    이메일 부분 일치 / 가입 경로 / 정지 여부 필터와 페이지네이션을 DB에서
    한 번에 처리한다. total은 필터 적용 후 개수.
    """
    result = await asyncio.to_thread(
        crud_users.list_users, email, provider, is_banned, page, per_page
    )
    return AdminUserListResponse(
        users=result["users"],
        page=page,
        per_page=per_page,
        total=result["total"],
    )


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user_detail(user_id: str, _: AdminUser):
    """회원 상세 조회 — Auth 정보 + user_profiles + 현재 활성 정지."""
    try:
        user = await auth_service.get_user_by_id(user_id)
    except auth_service.AuthServiceError as e:
        raise _auth_error_to_http(e)
    profile_row, active_ban = await asyncio.gather(
        asyncio.to_thread(crud_profiles.get_profile_row, user_id),
        asyncio.to_thread(crud_bans.get_active_ban, user_id),
    )
    # 프로필 행이 없으면(트리거 도입 전 가입자) 스키마 기본값으로 응답
    profile = UserProfileDetail(**profile_row) if profile_row else UserProfileDetail()
    return AdminUserDetail(
        **_user_summary_fields(user, active_ban),
        profile=profile,
        active_ban=active_ban,
    )


# ── 사용자 접근 제한 ──────────────────────────────────────


@router.post("/users/{user_id}/ban", response_model=BanRecord, status_code=status.HTTP_201_CREATED)
async def ban_user(user_id: str, body: BanRequest, admin: AdminUser):
    """사용자 접근을 제한한다. ban_until 미지정 시 영구 정지.

    본인은 정지할 수 없고, 자신보다 낮은 역할만 정지할 수 있다
    (ADMIN → USER, SUPER_ADMIN → ADMIN/USER).
    """
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="자기 자신은 정지할 수 없습니다.",
        )
    target = await asyncio.to_thread(crud_profiles.get_profile, user_id)
    if ROLE_LEVELS[target.role] >= ROLE_LEVELS[admin.app_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="자신과 같거나 높은 역할의 사용자는 정지할 수 없습니다.",
        )
    ban_until_str = body.ban_until.isoformat() if body.ban_until else None
    ban = await asyncio.to_thread(
        crud_bans.create_ban, user_id, body.reason, ban_until_str, admin.email
    )
    logger.info("사용자 접근 제한: %s (by %s, until=%s)", user_id, admin.email, ban_until_str)
    return ban


@router.delete("/users/{user_id}/ban", status_code=status.HTTP_204_NO_CONTENT)
async def unban_user(user_id: str, admin: AdminUser):
    """현재 활성 접근 제한을 해제한다."""
    lifted = await asyncio.to_thread(crud_bans.lift_active_ban, user_id, admin.email)
    if not lifted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="활성 접근 제한이 없습니다.",
        )
    logger.info("사용자 접근 제한 해제: %s (by %s)", user_id, admin.email)


@router.get("/users/{user_id}/bans", response_model=BanListResponse)
async def list_user_bans(user_id: str, _: AdminUser):
    """사용자의 접근 제한 이력을 최신 순으로 반환한다."""
    records = await asyncio.to_thread(crud_bans.list_bans, user_id)
    return BanListResponse(records=records)


# ── 역할 부여 ─────────────────────────────────────────


@router.put("/users/{user_id}/role", response_model=RoleUpdateResponse)
async def update_user_role(user_id: str, body: RoleUpdateRequest, admin: SuperAdminUser):
    """사용자 역할을 변경한다 — SUPER_ADMIN 전용.

    SUPER_ADMIN 부여는 API로 불가(1명 제한 — DB에서 SQL로만 처리).
    본인 역할도 변경 불가(유일한 SUPER_ADMIN의 강등·락아웃 방지).
    """
    if body.role == UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SUPER_ADMIN 역할은 API로 부여할 수 없습니다.",
        )
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="자기 자신의 역할은 변경할 수 없습니다.",
        )
    try:
        await auth_service.get_user_by_id(user_id)  # 존재하지 않는 사용자면 404
    except auth_service.AuthServiceError as e:
        raise _auth_error_to_http(e)
    await asyncio.to_thread(crud_profiles.upsert_role, user_id, body.role)
    logger.info("역할 변경: %s → %s (by %s)", user_id, body.role.value, admin.email)
    return RoleUpdateResponse(user_id=user_id, role=body.role)
