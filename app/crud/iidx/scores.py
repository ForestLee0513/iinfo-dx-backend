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
    added_chart_count: int,
    updated_chart_count: int,
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
            "added_chart_count": added_chart_count,
            "updated_chart_count": updated_chart_count,
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


_SCORE_VALUE_COLUMNS = (
    "title, difficulty, ex_score, pgreat, great, clear_type, dj_level, "
    "play_count, miss_count, last_played_at"
)


def get_current_score_values(user_id: str, play_style: str) -> list[dict]:
    """직전 활성 스냅샷의 갱신 비교용 성적 필드를 전량 반환한다.

    PostgREST 기본 응답 한도를 넘는 스냅샷도 있으므로 반드시 페이지네이션한다.
    """
    current = get_current(user_id, play_style)
    if not current:
        return []

    rows: list[dict] = []
    start = 0
    db = get_supabase_iidx()
    while True:
        page = (
            db.table("user_chart_scores")
            .select(_SCORE_VALUE_COLUMNS)
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


def _fetch_board_rows(db, upload_id: str, user_id: str) -> list[dict]:
    """서열표 매칭용 최소 컬럼을 한 업로드에서 페이지네이션으로 전량 로드한다."""
    rows: list[dict] = []
    start = 0
    while True:
        page = (
            db.table("user_chart_scores")
            .select("title, difficulty, level, clear_type, dj_level, ex_score, last_played_at")
            .eq("upload_id", upload_id)
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
    return _fetch_board_rows(get_supabase_iidx(), current["upload_id"], user_id)


def get_previous_board_score_rows(user_id: str, play_style: str) -> list[dict]:
    """현재 활성 스냅샷보다 먼저 업로드된 가장 최근 CSV의 서열표용 성적 행을 반환한다.

    현재 스냅샷에서 clear_lamp가 no_play가 아닌데 ex_score가 0인 이상 행은
    사실상 "이번 작에서 아직 플레이하지 않음"으로 보고, 직전 CSV에 남아있는
    실제 플레이 기록으로 보정하기 위한 폴백 조회용. 직전 스냅샷이 없으면 빈 목록.
    """
    current = get_current(user_id, play_style)
    if not current:
        return []
    db = get_supabase_iidx()
    current_upload = (
        db.table("score_uploads")
        .select("uploaded_at")
        .eq("id", current["upload_id"])
        .maybe_single()
        .execute()
    )
    if not current_upload or not current_upload.data:
        return []
    prev_result = (
        db.table("score_uploads")
        .select("id")
        .eq("user_id", user_id)
        .eq("play_style", play_style)
        .lt("uploaded_at", current_upload.data["uploaded_at"])
        .order("uploaded_at", desc=True)
        .limit(1)
        .execute()
    )
    prev_uploads = prev_result.data or []
    if not prev_uploads:
        return []
    return _fetch_board_rows(db, prev_uploads[0]["id"], user_id)


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


def get_score_update_dates(
    user_id: str, play_style: str | None = None, since: datetime | None = None
) -> list[dict]:
    """성적 추가·갱신 수와 해당 업로드 시각을 반환한다 — 기여도 그래프용."""
    query = (
        get_supabase_iidx()
        .table("score_uploads")
        .select("uploaded_at, added_chart_count, updated_chart_count")
        .eq("user_id", user_id)
    )
    if play_style is not None:
        query = query.eq("play_style", play_style)
    if since is not None:
        query = query.gte("uploaded_at", since.isoformat())
    result = query.execute()
    return result.data or []


def get_score_update_year_dates(user_id: str, play_style: str | None = None) -> list[dict]:
    """연동 이력이 있는 연도 계산용 업로드 시각을 반환한다.

    캘린더의 현재 조회 기간과 별개로 전체 이력을 읽어, FE가 하드코딩 없이
    연도 선택지를 만들 수 있게 한다.
    """
    # Supabase Data API의 행 수 상한을 넘는 오래된 이력도 누락하지 않는다.
    page_size = 1000
    offset = 0
    rows: list[dict] = []
    while True:
        query = (
            get_supabase_iidx()
            .table("score_uploads")
            .select("uploaded_at")
            .eq("user_id", user_id)
        )
        if play_style is not None:
            query = query.eq("play_style", play_style)
        result = query.order("uploaded_at").range(offset, offset + page_size - 1).execute()
        page = result.data or []
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def list_score_update_history(
    user_id: str, play_style: str | None, page: int, per_page: int
) -> tuple[list[dict], int]:
    """성적 추가·갱신 이력을 최신순 페이지 단위로 반환한다."""
    query = (
        get_supabase_iidx()
        .table("score_uploads")
        .select(
            "id, play_style, source, uploaded_at, added_chart_count, updated_chart_count",
            count="exact",
        )
        .eq("user_id", user_id)
    )
    if play_style is not None:
        query = query.eq("play_style", play_style)
    result = (
        query.order("uploaded_at", desc=True)
        .order("id", desc=True)
        .range((page - 1) * per_page, page * per_page - 1)
        .execute()
    )
    return result.data or [], result.count or 0


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


# ── IIDX 서비스 탈퇴 ──────────────────────────────────────────────────────────

def get_all_storage_paths(user_id: str) -> list[str]:
    """사용자의 전체 업로드(SP/DP 모두) CSV storage 경로 목록. 서비스/계정 탈퇴 시 파일 정리용.

    업로드가 PostgREST 단일 응답 행 상한(_PAGE)을 넘을 수 있어 페이지네이션으로 전량 로드한다.
    """
    db = get_supabase_iidx()
    paths: list[str] = []
    start = 0
    while True:
        page = (
            db.table("score_uploads")
            .select("storage_path")
            .eq("user_id", user_id)
            .order("id")
            .range(start, start + _PAGE - 1)
            .execute()
            .data
            or []
        )
        paths.extend(r["storage_path"] for r in page)
        if len(page) < _PAGE:
            break
        start += _PAGE
    return paths


def delete_user_scores(user_id: str) -> None:
    """사용자의 성적 데이터(score_current/score_uploads)를 전부 삭제한다.

    score_current.upload_id → score_uploads(id) FK는 cascade가 없어 score_uploads보다
    먼저 지워야 한다. user_chart_scores.upload_id → score_uploads(id)는 on delete
    cascade이므로 score_uploads 삭제만으로 함께 정리된다.
    """
    db = get_supabase_iidx()
    db.table("score_current").delete().eq("user_id", user_id).execute()
    db.table("score_uploads").delete().eq("user_id", user_id).execute()
