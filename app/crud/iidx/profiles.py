"""iidx.profiles 읽기 전용 CRUD — 어드민 회원 상세의 IIDX 서비스 프로필 조회용.

account 계층(app/crud/account/profiles.py)의 병합 프로필과 달리, 여기는
iidx.profiles 행 그 자체만 다룬다 — 계정 계층(핸들/소셜링크 등)은 admin/users.py가
이미 반환하므로 중복하지 않는다.
"""

from __future__ import annotations

from app.db.session import get_supabase_iidx

_COLUMNS = (
    "is_public, dj_name, dj_id, community_nickname, play_count, notes_radar, "
    "dan, arena_class, service_role"
)


def get_profile_row(user_id: str) -> dict | None:
    """iidx.profiles 행 — 온보딩 전(행 없음)이면 None."""
    result = (
        get_supabase_iidx()
        .table("profiles")
        .select(_COLUMNS)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    return result.data if result and result.data else None
