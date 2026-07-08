from dataclasses import dataclass

from app.db.session import get_supabase
from app.schemas.user import UserRole


@dataclass
class UserProfile:
    is_public: bool
    role: UserRole


def get_profile(user_id: str) -> UserProfile:
    """user_profiles에서 프로필 조회. 행이 없으면 기본값 반환."""
    sb = get_supabase()
    result = (
        sb.table("user_profiles")
        .select("is_public, role")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if result.data:
        return UserProfile(
            is_public=bool(result.data["is_public"]),
            role=UserRole(result.data.get("role", "USER")),
        )
    return UserProfile(is_public=True, role=UserRole.USER)


def get_profile_row(user_id: str) -> dict | None:
    """어드민 상세용 — user_profiles 행 전체를 반환한다. 행이 없으면 None."""
    sb = get_supabase()
    result = (
        sb.table("user_profiles")
        .select("is_public, role, updated_at")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    # maybe_single은 행이 없으면 None 응답을 반환할 수 있다
    return result.data if result and result.data else None


def upsert_is_public(user_id: str, is_public: bool) -> None:
    """user_profiles의 is_public 값을 업서트."""
    sb = get_supabase()
    sb.table("user_profiles").upsert(
        {"user_id": user_id, "is_public": is_public}
    ).execute()


def upsert_role(user_id: str, role: UserRole) -> None:
    """user_profiles의 role 값을 업서트."""
    sb = get_supabase()
    sb.table("user_profiles").upsert(
        {"user_id": user_id, "role": role.value}
    ).execute()
