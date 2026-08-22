"""난이도표 공개 조회 — Supabase에서 표/엔트리를 읽어온다 (읽기 전용).

difficulty_tables / difficulty_entries는 public read(RLS) 대상이므로
누구나 조회할 수 있다. 쓰기/갱신은 주간 크롤링 스케줄러로만 이뤄진다.

동기(블로킹) 함수이므로 FastAPI의 동기 엔드포인트(스레드풀)에서 호출한다.
"""

import logging

from app.db.session import get_supabase_iidx

logger = logging.getLogger(__name__)

# 목록 조회 시 엔트리는 제외하고 표 메타데이터만 반환
_TABLE_META_COLUMNS = (
    "slug, name, source, play_style, rating_type, level, grades, updated_at"
)

# PostgREST 단일 응답 행 상한(기본 1000)을 우회하기 위한 페이지 크기.
_PAGE = 1000


def fetch_tables() -> list[dict]:
    """모든 난이도표 메타데이터 목록 (엔트리 제외)."""
    res = (
        get_supabase_iidx()
        .table("difficulty_tables")
        .select(_TABLE_META_COLUMNS)
        .order("slug")
        .execute()
    )
    return res.data


def fetch_table(slug: str) -> dict | None:
    """slug로 표 1개 + 엔트리 전체를 조회. 없으면 None.

    difficulty_entries는 table_id FK로 임베드해 한 번에 가져온다.
    """
    res = (
        get_supabase_iidx()
        .table("difficulty_tables")
        .select("*, difficulty_entries(*)")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    rows = res.data
    return rows[0] if rows else None


def fetch_table_meta(slug: str) -> dict | None:
    """slug로 표 1개의 메타데이터만 조회(엔트리 제외). 없으면 None.

    엔트리는 표 하나가 1000행을 넘길 수 있어 PostgREST 임베드(fetch_table)로는
    잘릴 수 있으므로, 서열표 조회는 이 함수 + fetch_entries 조합을 쓴다.
    """
    res = (
        get_supabase_iidx()
        .table("difficulty_tables")
        .select("*")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    rows = res.data
    return rows[0] if rows else None


def fetch_entries(table_id: str) -> list[dict]:
    """표에 속한 엔트리 전체를 페이지네이션으로 모두 가져온다."""
    db = get_supabase_iidx()
    entries: list[dict] = []
    start = 0
    while True:
        rows = (
            db.table("difficulty_entries")
            .select("*")
            .eq("table_id", table_id)
            .order("id")
            .range(start, start + _PAGE - 1)
            .execute()
            .data
            or []
        )
        entries.extend(rows)
        if len(rows) < _PAGE:
            break
        start += _PAGE
    return entries


# ── 엔트리 정렬 (어드민 상세 조회용, 메모리 내 정렬) ──────────────

# 정렬 가능한 엔트리 필드
ENTRY_SORT_KEYS = ("title", "level", "grade", "rating", "difficulty")

# 채보 난이도의 논리적 순서 (알파벳순이 아님)
_DIFFICULTY_ORDER = {
    "BEGINNER": 0,
    "NORMAL": 1,
    "HYPER": 2,
    "ANOTHER": 3,
    "LEGGENDARIA": 4,
}


def sort_table_entries(table: dict, sort: str, order: str) -> None:
    """table["difficulty_entries"]를 지정 기준으로 제자리 정렬한다.

    - grade: 표의 grades[] 서열 순서(알파벳순 아님)로 랭크 매핑해 정렬.
    - difficulty: NORMAL/HYPER/ANOTHER/LEGGENDARIA 논리 순서로 정렬.
    - level/rating: 수치 정렬. 값이 없는(None) 엔트리는 방향과 무관하게 항상 뒤로.
    - title: 대소문자 무시.
    sort가 ENTRY_SORT_KEYS 밖이면 아무것도 하지 않는다(원본 순서 유지).
    """
    if sort not in ENTRY_SORT_KEYS:
        return
    entries = table.get("difficulty_entries") or []
    reverse = order == "desc"

    if sort == "grade":
        rank = {g: i for i, g in enumerate(table.get("grades") or [])}
        key_fn = lambda e: rank.get(e.get("grade"))  # noqa: E731
    elif sort == "difficulty":
        key_fn = lambda e: _DIFFICULTY_ORDER.get(e.get("difficulty"))  # noqa: E731
    elif sort == "title":
        key_fn = lambda e: (e.get("title") or "").casefold()  # noqa: E731
    else:  # level / rating
        key_fn = lambda e: e.get(sort)  # noqa: E731

    # None(미정/알 수 없음) 값은 정렬 방향과 무관하게 항상 뒤로 보낸다.
    present = [e for e in entries if key_fn(e) is not None]
    missing = [e for e in entries if key_fn(e) is None]
    present.sort(key=key_fn, reverse=reverse)
    table["difficulty_entries"] = present + missing
