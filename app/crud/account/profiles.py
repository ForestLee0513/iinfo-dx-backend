"""프로필 CRUD — 공통 계정 프로필(public.profiles)과 IIDX 서비스 프로필
(iidx.profiles)에 걸쳐 있다.

역할이 두 테이블에 분산되어 있으므로(플랫폼 SUPER_ADMIN = public.platform_role,
서비스 ADMIN = iidx.service_role) 호출부에는 예전처럼 단일 유효 역할(UserRole)로
합성해 돌려준다. get_profile_row는 예전 단일 user_profiles 행과 같은 평탄한 dict
모양(user_id/is_public/role/updated_at/handle/social_links/dj_name/dj_id/
profile_image_url)을 유지해 API 응답 계약을 바꾸지 않는다.

public.profiles PK는 id, iidx.profiles PK는 user_id다. 가입 트리거
(handle_new_user)가 public.profiles 행은 자동 생성하지만 iidx.profiles
행은 서비스 온보딩 전까지 없을 수 있으므로 서비스 프로필은 항상 nullable로 다룬다.
스키마 정의: supabase/migrations/20260803000000_baseline.sql
"""

from dataclasses import dataclass
from typing import Any

from postgrest.exceptions import APIError

from app.db.session import get_supabase, get_supabase_iidx
from app.schemas.account.user import UserRole

# public.profiles에서 프로필 표시에 필요한 컬럼
_PUBLIC_COLUMNS = (
    "id, handle, social_links, profile_image_url, is_public, platform_role, updated_at"
)
# iidx.profiles에서 서비스 전용 필드
_SVC_COLUMNS = "dj_name, dj_id, service_role, is_public"

# update_editable_fields에서 '전달 안 함'과 'null로 명시적으로 지움'을 구분하기
# 위한 내부 전용 sentinel — 호출자는 이 값을 알 필요 없이 키워드를 생략하면 된다.
_UNSET = object()


@dataclass
class UserProfile:
    is_public: bool
    role: UserRole


class NotMemberError(Exception):
    """iidx.profiles 행이 없어 서비스에 온보딩되지 않은 경우."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        super().__init__(f"IIDX 서비스에 가입하지 않은 사용자입니다: {user_id}")


class HandleTakenError(Exception):
    """요청한 handle이 이미 다른 사용자에게 선점된 경우(DB unique 제약 위반)."""

    def __init__(self, handle: str | None):
        self.handle = handle
        super().__init__(f"이미 사용 중인 handle입니다: {handle}")


def _effective_role(platform_role: str | None, service_role: str | None) -> UserRole:
    """두 테이블의 역할을 단일 유효 역할로 합성한다.

    플랫폼 SUPER_ADMIN이 최상위, 그다음 서비스 ADMIN, 그 외 USER.
    """
    if platform_role == UserRole.SUPER_ADMIN.value:
        return UserRole.SUPER_ADMIN
    if service_role == UserRole.ADMIN.value:
        return UserRole.ADMIN
    return UserRole.USER


def _fetch_svc(user_id: str) -> dict | None:
    """iidx.profiles 행 (온보딩 전이면 None)."""
    result = (
        get_supabase_iidx()
        .table("profiles")
        .select(_SVC_COLUMNS)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    return result.data if result and result.data else None


def _merge_row(pub: dict) -> dict:
    """public.profiles 행 + iidx.profiles 행을 예전 평탄한 프로필 dict로 합친다."""
    svc = _fetch_svc(pub["id"])
    role = _effective_role(pub.get("platform_role"), (svc or {}).get("service_role"))
    return {
        "user_id": pub["id"],
        "is_public": bool(pub["is_public"]),
        "iidx_is_public": bool((svc or {}).get("is_public", True)),
        "role": role.value,
        "updated_at": pub.get("updated_at"),
        "handle": pub.get("handle"),
        "social_links": pub.get("social_links") or [],
        "dj_name": (svc or {}).get("dj_name"),
        "dj_id": (svc or {}).get("dj_id"),
        "profile_image_url": pub.get("profile_image_url"),
    }


def get_profile(user_id: str) -> UserProfile:
    """인증 경로용 경량 조회 — is_public + 유효 역할. 행이 없으면 기본값."""
    pub = (
        get_supabase()
        .table("profiles")
        .select("is_public, platform_role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not (pub and pub.data):
        return UserProfile(is_public=True, role=UserRole.USER)
    svc = _fetch_svc(user_id)
    return UserProfile(
        is_public=bool(pub.data["is_public"]),
        role=_effective_role(pub.data.get("platform_role"), (svc or {}).get("service_role")),
    )


def get_profile_row(user_id: str) -> dict | None:
    """어드민 상세/프로필 조회용 — 평탄한 프로필 dict. 행이 없으면 None."""
    result = (
        get_supabase()
        .table("profiles")
        .select(_PUBLIC_COLUMNS)
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not (result and result.data):
        return None
    return _merge_row(result.data)


def get_profile_row_by_handle(handle: str) -> dict | None:
    """handle로 프로필 조회 — 대소문자 구분 없이 일치(가입 시 unique 제약과 동일 기준).

    GET /profile/{identifier}에서 identifier가 UUID가 아닐 때 쓴다.
    """
    result = (
        get_supabase()
        .table("profiles")
        .select(_PUBLIC_COLUMNS)
        .ilike("handle", handle)
        .maybe_single()
        .execute()
    )
    if not (result and result.data):
        return None
    return _merge_row(result.data)


def get_profile_summaries(user_ids: list[str]) -> dict[str, dict]:
    """팔로워/팔로잉 목록 렌더링용 — user_id로 색인한 {handle, profile_image_url} 맵.

    N+1 조회를 피하려고 IN절로 한 번에 가져온다. 없는 id는 결과에서 빠진다.
    표시에 필요한 handle/profile_image_url은 모두 public.profiles에 있다.
    """
    if not user_ids:
        return {}
    result = (
        get_supabase()
        .table("profiles")
        .select("id, handle, profile_image_url")
        .in_("id", user_ids)
        .execute()
    )
    return {row["id"]: {**row, "user_id": row["id"]} for row in (result.data or [])}


def update_editable_fields(
    user_id: str,
    *,
    handle: Any = _UNSET,
    social_links: Any = _UNSET,
    is_public: Any = _UNSET,
) -> dict:
    """본인이 API로 바꿀 수 있는 필드(handle, social_links, is_public)만 부분 업데이트한다.

    세 필드 모두 public.profiles에 있다. 키워드를 아예 생략하면 해당 필드는
    변경하지 않는다. handle=None으로 명시하면 핸들을 해제(release)한다. 프로필
    행은 가입 트리거로 이미 존재하지만, 안전하게 upsert(PK=id)로 처리한다.
    handle 중복(DB unique 제약 위반, code 23505)은 HandleTakenError로 변환한다.
    """
    payload: dict = {"id": user_id}
    if handle is not _UNSET:
        payload["handle"] = handle
    if social_links is not _UNSET:
        payload["social_links"] = social_links if social_links is not None else []
    if is_public is not _UNSET:
        payload["is_public"] = is_public

    if len(payload) > 1:
        try:
            get_supabase().table("profiles").upsert(payload).execute()
        except APIError as e:
            if getattr(e, "code", None) == "23505":
                raise HandleTakenError(handle if handle is not _UNSET else None) from e
            raise

    return get_profile_row(user_id) or {}


def is_iidx_member(user_id: str) -> bool:
    """iidx.profiles 행이 존재하면(=온보딩 완료) True."""
    return _fetch_svc(user_id) is not None


def update_iidx_editable_fields(user_id: str, *, is_public: Any = _UNSET) -> dict:
    """IIDX 서비스 프로필에서 본인이 API로 바꿀 수 있는 필드를 부분 업데이트한다.

    iidx.profiles 행이 없으면(미온보딩) NotMemberError를 발생시킨다.
    키워드를 생략하면 해당 필드는 변경하지 않는다.
    """
    if _fetch_svc(user_id) is None:
        raise NotMemberError(user_id)
    payload: dict = {}
    if is_public is not _UNSET:
        payload["is_public"] = is_public
    if payload:
        get_supabase_iidx().table("profiles").update(payload).eq("user_id", user_id).execute()
    return get_profile_row(user_id) or {}


def upsert_is_public(user_id: str, is_public: bool) -> None:
    """public.profiles의 is_public 값을 업서트."""
    get_supabase().table("profiles").upsert(
        {"id": user_id, "is_public": is_public}
    ).execute()


def upsert_role(user_id: str, role: UserRole) -> None:
    """유효 역할을 서비스 프로필에 반영한다.

    API로 부여 가능한 역할은 ADMIN/USER뿐이다(SUPER_ADMIN은 엔드포인트에서 거부 —
    플랫폼 역할이라 DB에서 SQL로만 부여). 따라서 iidx.profiles.service_role만
    갱신하면 된다. 행이 없으면 서비스 프로필을 생성(=온보딩 처리)한다.
    """
    service_role = UserRole.ADMIN.value if role == UserRole.ADMIN else UserRole.USER.value
    get_supabase_iidx().table("profiles").upsert(
        {"user_id": user_id, "service_role": service_role}
    ).execute()
