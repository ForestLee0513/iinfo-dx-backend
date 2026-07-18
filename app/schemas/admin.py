"""어드민 회원 관리 API 요청/응답 모델. 크롤 관련 모델은 app/schemas/crawl.py 참고."""

from datetime import datetime

from pydantic import BaseModel

from app.schemas.user import UserRole


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


class RoleUpdateRequest(BaseModel):
    """역할 변경 요청 — SUPER_ADMIN 전용 API의 본문.

    SUPER_ADMIN 부여는 API로 불가(1명 제한, SQL로만 처리) — 엔드포인트에서 거부한다.
    """

    role: UserRole


class RoleUpdateResponse(BaseModel):
    """역할 변경 결과."""

    user_id: str
    role: UserRole


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
