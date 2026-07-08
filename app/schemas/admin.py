"""어드민 API 요청/응답 모델."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.user import UserRole

JobScope = Literal["full", "songs", "tables"]
JobStatus = Literal["RUNNING", "DONE", "FAILED"]
StepStatus = Literal["PENDING", "RUNNING", "DONE", "FAILED"]
ScheduleDay = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class JobStep(BaseModel):
    """작업의 단일 스텝. 스텝 단위로 Redis에 체크포인트가 남아 이어하기 기준이 된다."""

    name: str  # "song_sync" | "table_sync"
    status: StepStatus = "PENDING"
    result: dict | None = None  # 파이프라인 반환값 그대로
    error: str | None = None


class CrawlJob(BaseModel):
    """크롤 동기화 작업 상태. 어드민 FE가 폴링으로 진행 상황을 확인한다."""

    id: str
    scope: JobScope
    status: JobStatus
    triggered_by: str  # "schedule" | "admin:<email>"
    steps: list[JobStep]
    resumed: bool = False  # 서버 재기동 후 이어서 실행된 작업인지
    created_at: str
    finished_at: str | None = None


class JobCreateRequest(BaseModel):
    """수동 크롤 실행 요청. full = 곡 마스터 → 난이도표 순차."""

    scope: JobScope = "full"


class JobListResponse(BaseModel):
    jobs: list[CrawlJob]


class ScheduleConfig(BaseModel):
    """주간 크롤 스케줄 설정 (TIMEZONE 기준 cron)."""

    enabled: bool
    day: ScheduleDay
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)


class ScheduleResponse(ScheduleConfig):
    timezone: str
    source: Literal["env", "redis"]  # redis = 어드민이 변경한 값이 적용 중
    next_run_at: str | None = None  # enabled=False면 None


class BanRequest(BaseModel):
    """사용자 접근 제한 요청."""

    reason: str  # 정지 사유 (필수)
    ban_until: datetime | None = None  # None = 영구 정지, 지정 시 해당 일시까지


class BanRecord(BaseModel):
    """user_bans 테이블 레코드 — 정지 이력 1건."""

    id: str
    user_id: str
    reason: str
    ban_until: datetime | None = None
    banned_by: str
    banned_at: datetime
    lifted_at: datetime | None = None
    lifted_by: str | None = None


class BanListResponse(BaseModel):
    """사용자 접근 제한 이력 목록."""

    records: list[BanRecord]


class AdminUserSummary(BaseModel):
    """어드민 회원 목록의 사용자 1명 요약 (Supabase Auth 기반)."""

    id: str
    email: str | None = None
    provider: str | None = None  # 가입 경로 ("google" | "email" 등)
    created_at: datetime  # 가입일자
    last_sign_in_at: datetime | None = None
    is_banned: bool  # 현재 정지 여부 (user_bans 활성 레코드 존재)
    ban_reason: str | None = None  # 정지 사유 (정지 중일 때만)
    ban_until: datetime | None = None  # 정지 만료 일시 (정지 중인데 None이면 영구 정지)


class AdminUserListResponse(BaseModel):
    """어드민 회원 목록 응답 — Supabase Auth 페이지네이션 그대로 전달."""

    users: list[AdminUserSummary]
    page: int
    per_page: int
    total: int  # 전체 회원 수


class UserProfileDetail(BaseModel):
    """user_profiles 테이블 내용 — 어드민 회원 상세용."""

    is_public: bool = True
    role: UserRole = UserRole.USER
    updated_at: datetime | None = None  # 행이 아직 없으면 None (기본값 상태)


class AdminUserDetail(AdminUserSummary):
    """어드민 회원 상세 — 요약 + 프로필 + 현재 활성 정지 정보."""

    profile: UserProfileDetail
    active_ban: BanRecord | None = None  # None = 정지 중 아님
