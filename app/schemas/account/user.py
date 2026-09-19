from enum import Enum

from pydantic import BaseModel


class UserRole(str, Enum):
    """서비스 권한 역할 — DB 프로필에서 합성한 유효 역할.

    ADMIN은 iidx.profiles.service_role, SUPER_ADMIN은 public.profiles.platform_role
    에서 온다(crud_profiles._effective_role가 합성). 역할을 추가할 때는 멤버를 늘리고
    ROLE_LEVELS에 서열만 배치하면 된다.
    서열로 표현할 수 없는 권한이 생기면 그때 권한 집합(permission set) 방식으로 교체한다.
    """

    USER = "USER"
    ADMIN = "ADMIN"  # iidx.profiles.service_role = 'ADMIN'
    SUPER_ADMIN = "SUPER_ADMIN"  # public.profiles.platform_role — API로 부여 불가(SQL 전용)


# 역할 서열 — require_role은 "요구 레벨 이상"으로 판정하므로 상위 역할이 하위 권한을 포함한다
ROLE_LEVELS: dict[UserRole, int] = {
    UserRole.USER: 0,
    UserRole.ADMIN: 100,
    UserRole.SUPER_ADMIN: 200,
}


class AuthUser(BaseModel):
    """Supabase 액세스 토큰 클레임에서 추출한 인증 사용자 정보."""

    id: str  # Supabase user UUID (sub 클레임)
    email: str | None = None
    provider: str | None = None  # "google" | "email" 등
    # role: str | None = None  # JWT role 클레임 (일반적으로 "authenticated" — 서비스 권한 아님)
    # 유효 역할 — public.profiles.platform_role(SUPER_ADMIN) + iidx.profiles.service_role(ADMIN)
    # 을 합성한 값(crud_profiles.get_profile). 자세한 합성 규칙은 crud_profiles._effective_role 참고.
    app_role: UserRole = UserRole.USER
    is_public: bool = True  # 공개 여부 (public.profiles.is_public)
    # 이 요청이 업로드 토큰(X-Upload-Token)으로 인증됐을 때 그 토큰값. JWT 인증이면 None.
    # 업로드 성공 후 토큰을 만료(소모)시키는 데 사용한다.
    upload_token: str | None = None

    def has_role(self, required: UserRole) -> bool:
        return ROLE_LEVELS[self.app_role] >= ROLE_LEVELS[required]
