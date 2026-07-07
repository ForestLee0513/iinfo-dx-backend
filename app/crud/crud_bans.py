"""user_bans 테이블 CRUD — 사용자 접근 제한 이력 관리."""

from datetime import datetime, timezone

from app.db.session import get_supabase


def get_active_ban(user_id: str) -> dict | None:
    """현재 유효한 접근 제한 레코드를 반환한다. 없으면 None.

    lifted_at IS NULL인 레코드 중 ban_until이 없거나(영구) 아직 지나지 않은 것.
    """
    sb = get_supabase()
    result = (
        sb.table("user_bans")
        .select("id, reason, ban_until, banned_at, banned_by")
        .eq("user_id", user_id)
        .is_("lifted_at", "null")
        .execute()
    )
    now = datetime.now(timezone.utc)
    for ban in result.data:
        if ban["ban_until"] is None:
            return ban  # 영구 정지
        until = datetime.fromisoformat(ban["ban_until"])
        if not until.tzinfo:
            until = until.replace(tzinfo=timezone.utc)
        if until > now:
            return ban  # 기간 정지 (아직 유효)
    return None


def create_ban(
    user_id: str, reason: str, ban_until: str | None, banned_by: str
) -> dict:
    """새 접근 제한 레코드를 생성한다."""
    sb = get_supabase()
    payload: dict = {"user_id": user_id, "reason": reason, "banned_by": banned_by}
    if ban_until is not None:
        payload["ban_until"] = ban_until
    result = sb.table("user_bans").insert(payload).execute()
    return result.data[0]


def lift_active_ban(user_id: str, lifted_by: str) -> bool:
    """현재 활성 정지를 해제한다. 해제할 항목이 없으면 False."""
    sb = get_supabase()
    active = (
        sb.table("user_bans")
        .select("id")
        .eq("user_id", user_id)
        .is_("lifted_at", "null")
        .limit(1)
        .execute()
    )
    if not active.data:
        return False
    ban_id = active.data[0]["id"]
    now = datetime.now(timezone.utc).isoformat()
    sb.table("user_bans").update({"lifted_at": now, "lifted_by": lifted_by}).eq(
        "id", ban_id
    ).execute()
    return True


def list_bans(user_id: str) -> list[dict]:
    """사용자의 전체 접근 제한 이력을 최신 순으로 반환한다."""
    sb = get_supabase()
    result = (
        sb.table("user_bans")
        .select("*")
        .eq("user_id", user_id)
        .order("banned_at", desc=True)
        .execute()
    )
    return result.data
