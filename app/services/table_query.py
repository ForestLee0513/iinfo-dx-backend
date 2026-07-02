"""난이도표 공개 조회 — Supabase에서 표/엔트리를 읽어온다 (읽기 전용).

difficulty_tables / difficulty_entries는 public read(RLS) 대상이므로
누구나 조회할 수 있다. 쓰기/갱신은 주간 크롤링 스케줄러로만 이뤄진다.

동기(블로킹) 함수이므로 FastAPI의 동기 엔드포인트(스레드풀)에서 호출한다.
"""

import logging

from app.services.supabase_sync import get_supabase

logger = logging.getLogger(__name__)

# 목록 조회 시 엔트리는 제외하고 표 메타데이터만 반환
_TABLE_META_COLUMNS = (
    "slug, name, source, play_style, rating_type, level, grades, updated_at"
)


def fetch_tables() -> list[dict]:
    """모든 난이도표 메타데이터 목록 (엔트리 제외)."""
    res = (
        get_supabase()
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
        get_supabase()
        .table("difficulty_tables")
        .select("*, difficulty_entries(*)")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    rows = res.data
    return rows[0] if rows else None
