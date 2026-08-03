"""user_bans 테이블 CRUD — 사용자 접근 제한 이력 관리.

user_bans는 공통 계정 계층(public 스키마)에 있으므로 기본 클라이언트(get_supabase)를
쓴다. service 컬럼이 null이면 플랫폼 전체 제재, 'iidx'면 이 서비스 한정 제재다.
이 백엔드는 IIDX 서비스이므로 활성 제재 판정은 (service is null or service='iidx')로
한정하고, 신규 제재는 service='iidx'로 남긴다. banned_by/lifted_by는 profiles(id)를
참조하는 uuid이므로 관리자 이메일이 아니라 관리자 user_id(uuid)를 넘겨야 한다.
"""

from datetime import datetime, timezone

from app.db.session import get_supabase

# 이 서비스 식별자 — user_bans.service 필터/기록에 사용
_SERVICE = "iidx"


def _is_ban_active(ban: dict, now: datetime) -> bool:
    """해제되지 않은(lifted_at IS NULL) 레코드가 현재도 유효한지 판정한다."""
    if ban["ban_until"] is None:
        return True  # 영구 정지
    until = datetime.fromisoformat(ban["ban_until"])
    if not until.tzinfo:
        until = until.replace(tzinfo=timezone.utc)
    return until > now  # 기간 정지 (아직 유효)


def get_active_ban(user_id: str) -> dict | None:
    """현재 유효한 접근 제한 레코드를 반환한다. 없으면 None.

    lifted_at IS NULL인 레코드 중 ban_until이 없거나(영구) 아직 지나지 않은 것.
    """
    sb = get_supabase()
    result = (
        sb.table("user_bans")
        .select("id, user_id, reason, ban_until, banned_at, banned_by")
        .eq("user_id", user_id)
        .is_("lifted_at", "null")
        .or_(f"service.is.null,service.eq.{_SERVICE}")
        .execute()
    )
    now = datetime.now(timezone.utc)
    for ban in result.data:
        if _is_ban_active(ban, now):
            return ban
    return None


def create_ban(
    user_id: str, reason: str, ban_until: str | None, banned_by: str
) -> dict:
    """새 접근 제한 레코드를 생성한다. banned_by는 관리자 user_id(uuid)."""
    sb = get_supabase()
    payload: dict = {
        "user_id": user_id,
        "service": _SERVICE,
        "reason": reason,
        "banned_by": banned_by,
    }
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
