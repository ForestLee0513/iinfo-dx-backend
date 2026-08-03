"""회원 목록 조회 CRUD — admin_list_users RPC 래퍼 (auth.users 직접 조회)."""

from app.db.session import get_supabase


def list_users(
    email: str | None,
    provider: str | None,
    is_banned: bool | None,
    role: str | None,
    page: int,
    per_page: int,
) -> dict:
    """필터·페이지네이션을 적용한 회원 목록을 조회한다.

    반환: {"users": [...], "total": n} — total은 필터 적용 후 개수.
    """
    sb = get_supabase()
    result = sb.rpc(
        "admin_list_users",
        {
            "p_email": email,
            "p_provider": provider,
            "p_is_banned": is_banned,
            "p_role": role,
            "p_page": page,
            "p_per_page": per_page,
        },
    ).execute()
    return result.data
