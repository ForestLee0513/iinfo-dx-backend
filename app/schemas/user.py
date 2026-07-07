from enum import Enum

from pydantic import BaseModel


class UserRole(str, Enum):
    """서비스 권한 역할 — Supabase 계정의 app_metadata.role 값.

    역할을 추가할 때는 멤버를 늘리고 ROLE_LEVELS에 서열만 배치하면 된다.
    (예: OPERATOR = "OPERATOR" → 레벨 50 — ADMIN은 자동으로 그 권한을 포함)
    서열로 표현할 수 없는 권한이 생기면 그때 권한 집합(permission set) 방식으로 교체한다.
    """

    USER = "USER"
    ADMIN = "ADMIN"


# 역할 서열 — require_role은 "요구 레벨 이상"으로 판정하므로 상위 역할이 하위 권한을 포함한다
ROLE_LEVELS: dict[UserRole, int] = {
    UserRole.USER: 0,
    UserRole.ADMIN: 100,
}


class AuthUser(BaseModel):
    """Supabase 액세스 토큰 클레임에서 추출한 인증 사용자 정보."""

    id: str  # Supabase user UUID (sub 클레임)
    email: str | None = None
    provider: str | None = None  # "google" | "email" 등
    # role: str | None = None  # JWT role 클레임 (일반적으로 "authenticated" — 서비스 권한 아님)
    app_role: UserRole = UserRole.USER  # 서비스 권한 (user_profiles.role)
    is_public: bool = True  # 공개 여부 (user_profiles.is_public)

    def has_role(self, required: UserRole) -> bool:
        return ROLE_LEVELS[self.app_role] >= ROLE_LEVELS[required]
