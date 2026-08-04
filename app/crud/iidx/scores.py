"""사용자 성적 업로드/스냅샷 CRUD.

iidx 스키마의 score_uploads / score_current / user_chart_scores 테이블 접근.
service_role 클라이언트(get_supabase_iidx)를 사용하므로 RLS를 우회한다.
"""

from datetime import datetime, timezone

from app.db.session import get_supabase_iidx


# ── score_uploads ────────────────────────────────────────────────────────────

def insert_upload(
    *,
    upload_id: str,
    user_id: str,
    play_style: str,
    source: str,
    content_hash: str,
    storage_path: str,
    song_count: int,
) -> dict:
    """업로드 메타 행을 삽입하고 삽입된 행을 반환한다."""
    result = (
        get_supabase_iidx()
        .table("score_uploads")
        .insert({
            "id": upload_id,
            "user_id": user_id,
            "play_style": play_style,
            "source": source,
            "content_hash": content_hash,
            "storage_path": storage_path,
            "song_count": song_count,
        })
        .execute()
    )
    return result.data[0]


def get_upload_by_hash(user_id: str, play_style: str, content_hash: str) -> dict | None:
    """동일 해시의 기존 업로드를 반환한다. 없으면 None."""
    result = (
        get_supabase_iidx()
        .table("score_uploads")
        .select("*")
        .eq("user_id", user_id)
        .eq("play_style", play_style)
        .eq("content_hash", content_hash)
        .maybe_single()
        .execute()
    )
    return result.data if result and result.data else None


def get_upload(upload_id: str) -> dict | None:
    result = (
        get_supabase_iidx()
        .table("score_uploads")
        .select("*")
        .eq("id", upload_id)
        .maybe_single()
        .execute()
    )
    return result.data if result and result.data else None


def list_uploads(user_id: str, play_style: str) -> list[dict]:
    """사용자의 업로드 목록을 최신순으로 반환한다."""
    result = (
        get_supabase_iidx()
        .table("score_uploads")
        .select("id, play_style, source, song_count, uploaded_at")
        .eq("user_id", user_id)
        .eq("play_style", play_style)
        .order("uploaded_at", desc=True)
        .execute()
    )
    return result.data or []


# ── score_current ────────────────────────────────────────────────────────────

def get_current(user_id: str, play_style: str) -> dict | None:
    """현재 활성 스냅샷 포인터를 반환한다. 없으면 None."""
    result = (
        get_supabase_iidx()
        .table("score_current")
        .select("upload_id, applied_at")
        .eq("user_id", user_id)
        .eq("play_style", play_style)
        .maybe_single()
        .execute()
    )
    return result.data if result and result.data else None


def upsert_current(user_id: str, play_style: str, upload_id: str) -> dict:
    """현재 활성 스냅샷을 갱신(없으면 생성)하고 행을 반환한다."""
    now = datetime.now(tz=timezone.utc).isoformat()
    result = (
        get_supabase_iidx()
        .table("score_current")
        .upsert({
            "user_id": user_id,
            "play_style": play_style,
            "upload_id": upload_id,
            "applied_at": now,
        })
        .execute()
    )
    return result.data[0]


# ── user_chart_scores ─────────────────────────────────────────────────────────

_CHUNK = 500  # PostgREST 단일 요청 권장 상한


def insert_chart_scores(
    upload_id: str,
    user_id: str,
    play_style: str,
    rows: list[dict],
) -> None:
    """성적 행을 청크 단위로 bulk insert한다."""
    if not rows:
        return
    payload = [
        {**row, "upload_id": upload_id, "user_id": user_id, "play_style": play_style}
        for row in rows
    ]
    db = get_supabase_iidx()
    for i in range(0, len(payload), _CHUNK):
        db.table("user_chart_scores").insert(payload[i : i + _CHUNK]).execute()


def get_chart_scores(user_id: str, play_style: str) -> list[dict]:
    """현재 활성 스냅샷의 성적 전체를 반환한다."""
    current = get_current(user_id, play_style)
    if not current:
        return []
    result = (
        get_supabase_iidx()
        .table("user_chart_scores")
        .select("*")
        .eq("upload_id", current["upload_id"])
        .eq("user_id", user_id)
        .execute()
    )
    return result.data or []


def get_chart_scores_by_upload(upload_id: str, user_id: str) -> list[dict]:
    """특정 업로드의 성적 전체를 반환한다."""
    result = (
        get_supabase_iidx()
        .table("user_chart_scores")
        .select("*")
        .eq("upload_id", upload_id)
        .eq("user_id", user_id)
        .execute()
    )
    return result.data or []
