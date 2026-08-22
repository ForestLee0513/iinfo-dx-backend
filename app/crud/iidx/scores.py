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
_PAGE = 1000  # PostgREST 단일 응답 행 상한(기본값) — 조회 페이지네이션용


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


def get_score_summary_rows(user_id: str, play_style: str) -> list[dict]:
    """현재 활성 스냅샷의 (level, clear_type)만 반환한다 — 클리어 현황 요약용.

    한 스냅샷은 PostgREST 기본 상한(1000행)을 훌쩍 넘기므로 반드시
    페이지네이션으로 전량 로드한다(잘리면 램프 개수가 그대로 누락된다).
    스냅샷이 없으면 빈 목록.
    """
    current = get_current(user_id, play_style)
    if not current:
        return []
    db = get_supabase_iidx()
    rows: list[dict] = []
    start = 0
    while True:
        page = (
            db.table("user_chart_scores")
            .select("level, clear_type")
            .eq("upload_id", current["upload_id"])
            .eq("user_id", user_id)
            .range(start, start + _PAGE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < _PAGE:
            break
        start += _PAGE
    return rows


def get_board_score_rows(user_id: str, play_style: str) -> list[dict]:
    """현재 활성 스냅샷의 서열표 표시용 성적 행 전체를 반환한다.

    난이도표 엔트리와 타이틀·난이도로 매칭하기 위한 최소 컬럼만 가져오되,
    한 스냅샷은 1000행을 훌쩍 넘기므로 반드시 페이지네이션으로 전량 로드한다.
    스냅샷이 없으면 빈 목록.
    """
    current = get_current(user_id, play_style)
    if not current:
        return []
    db = get_supabase_iidx()
    rows: list[dict] = []
    start = 0
    while True:
        page = (
            db.table("user_chart_scores")
            .select("title, difficulty, level, clear_type, dj_level, ex_score, last_played_at")
            .eq("upload_id", current["upload_id"])
            .eq("user_id", user_id)
            .range(start, start + _PAGE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < _PAGE:
            break
        start += _PAGE
    return rows


def get_upload_dates(
    user_id: str, play_style: str | None = None, since: datetime | None = None
) -> list[dict]:
    """사용자의 업로드 시각 목록을 반환한다 — 업로드 기여도 그래프용.

    play_style을 지정하면 해당 스타일만, 생략하면 SP/DP 전체를 반환한다.
    since를 지정하면 그 시각 이후(포함) 업로드만 반환한다.
    """
    query = (
        get_supabase_iidx()
        .table("score_uploads")
        .select("uploaded_at")
        .eq("user_id", user_id)
    )
    if play_style is not None:
        query = query.eq("play_style", play_style)
    if since is not None:
        query = query.gte("uploaded_at", since.isoformat())
    result = query.execute()
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
