from dataclasses import dataclass
from typing import Any

from postgrest.exceptions import APIError

from app.db.session import get_supabase
from app.schemas.user import UserRole

# 프로필 조회 시 함께 내려주는 확장 컬럼 포함 전체 컬럼 목록
_PROFILE_COLUMNS = (
    "user_id, is_public, role, updated_at, handle, social_links, "
    "dj_name, dj_id, profile_image_url"
)

# update_editable_fields에서 '전달 안 함'과 'null로 명시적으로 지움'을 구분하기
# 위한 내부 전용 sentinel — 호출자는 이 값을 알 필요 없이 키워드를 생략하면 된다.
_UNSET = object()


@dataclass
class UserProfile:
    is_public: bool
    role: UserRole


class HandleTakenError(Exception):
    """요청한 handle이 이미 다른 사용자에게 선점된 경우(DB unique 제약 위반)."""

    def __init__(self, handle: str | None):
        self.handle = handle
        super().__init__(f"이미 사용 중인 handle입니다: {handle}")


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
    """어드민 상세/프로필 조회용 — user_profiles 행 전체를 반환한다. 행이 없으면 None."""
    sb = get_supabase()
    result = (
        sb.table("user_profiles")
        .select(_PROFILE_COLUMNS)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    # maybe_single은 행이 없으면 None 응답을 반환할 수 있다
    return result.data if result and result.data else None


def get_profile_row_by_handle(handle: str) -> dict | None:
    """handle로 프로필 조회 — 대소문자 구분 없이 일치(가입 시 unique 제약과 동일 기준).

    GET /web/profile/{identifier}에서 identifier가 UUID가 아닐 때 쓴다.
    """
    sb = get_supabase()
    result = (
        sb.table("user_profiles")
        .select(_PROFILE_COLUMNS)
        .ilike("handle", handle)
        .maybe_single()
        .execute()
    )
    return result.data if result and result.data else None


def update_editable_fields(
    user_id: str,
    *,
    handle: Any = _UNSET,
    social_links: Any = _UNSET,
) -> dict:
    """본인이 API로 바꿀 수 있는 필드(handle, social_links)만 부분 업데이트한다.

    키워드를 아예 생략하면 해당 필드는 변경하지 않는다. handle=None으로
    명시하면 핸들을 해제(release)한다. 프로필 행이 아직 없는 계정(트리거
    이전 가입자)이어도 upsert라 그대로 생성된다.
    handle 중복(DB unique 제약 위반, code 23505)은 HandleTakenError로 변환한다.
    """
    payload: dict = {"user_id": user_id}
    if handle is not _UNSET:
        payload["handle"] = handle
    if social_links is not _UNSET:
        payload["social_links"] = social_links if social_links is not None else []

    if len(payload) > 1:
        sb = get_supabase()
        try:
            sb.table("user_profiles").upsert(payload).execute()
        except APIError as e:
            if getattr(e, "code", None) == "23505":
                raise HandleTakenError(handle if handle is not _UNSET else None) from e
            raise

    return get_profile_row(user_id) or {}


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
