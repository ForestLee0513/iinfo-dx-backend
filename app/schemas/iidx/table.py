"""web 모듈 응답 스키마.

Supabase iidx.difficulty_tables / iidx.difficulty_entries 컬럼과 대응한다.
스키마 정의: supabase/migrations/20260803000000_baseline.sql
(difficulty_entries.chart_id 등 응답에 불필요한 신규 컬럼은 pydantic이 무시한다.)
"""

from datetime import datetime

from pydantic import BaseModel


class TableSummary(BaseModel):
    """난이도표 메타데이터 (목록 조회 — 엔트리 제외)."""

    slug: str
    name: str
    source: str
    play_style: str
    rating_type: str
    level: int | None = None
    grades: list[str] | None = None
    updated_at: datetime


class TableListResponse(BaseModel):
    tables: list[TableSummary]


class DifficultyEntry(BaseModel):
    """difficulty_entries 한 행 (표에 속한 곡 엔트리)."""

    id: int
    table_id: str
    title: str
    series: str | None = None
    play_style: str
    difficulty: str  # NORMAL/HYPER/ANOTHER/LEGGENDARIA
    level: int | None = None
    grade: str | None = None  # GRADE 방식: 'S+', 'A' 등
    rating: float | None = None  # NUMERIC 방식: 12.3 등
    table_type: str | None = None  # 'STRENGTH'=지력 / 'PERSONAL'=개인차 (5ch)
    created_at: datetime


class TableDetail(BaseModel):
    """난이도표 1개 + 엔트리 전체 (상세 조회)."""

    id: str
    slug: str
    name: str
    source: str
    play_style: str
    rating_type: str
    level: int | None = None
    grades: list[str] | None = None
    created_at: datetime
    updated_at: datetime
    difficulty_entries: list[DifficultyEntry] = []


# ── 서열표(난이도표 + 사용자 클리어 램프) ────────────────────────────────


class BoardScore(BaseModel):
    """엔트리 하나에 매칭된 사용자 성적 (매칭 실패/미플레이면 상위에서 null)."""

    clear_lamp: str  # 8종 표준 램프 키 (no_play/failed/.../full_combo)
    dj_level: str | None = None  # AAA/AA/A ... (성적 CSV 원문)
    ex_score: int | None = None
    level: int | None = None  # 성적 CSV 기준 레벨
    last_played_at: datetime | None = None


class BoardEntry(BaseModel):
    """서열표 한 줄 — 표 엔트리 + 본인/상대 성적."""

    id: int
    title: str
    series: str | None = None
    play_style: str
    difficulty: str
    level: int | None = None
    grade: str | None = None
    rating: float | None = None
    table_type: str | None = None  # 'STRENGTH'=지력 / 'PERSONAL'=개인차
    # 조회 대상(본인)의 성적. 비로그인·미지정이면 null.
    score: BoardScore | None = None
    # 비교 대상(opponent)의 성적. 비교 모드가 아니면 null.
    opponent_score: BoardScore | None = None


class BoardSection(BaseModel):
    """서열표 섹션 — GRADE 표는 등급(+지력/개인차), NUMERIC 표는 rating 단위."""

    id: str
    title: str  # 예: "S+ 지력", "12.3"
    grade: str | None = None
    rating: float | None = None
    table_type: str | None = None
    entries: list[BoardEntry]


class BoardUser(BaseModel):
    """서열표의 주체/비교 대상 사용자 요약."""

    user_id: str
    handle: str | None = None
    dj_name: str | None = None


class BoardComparison(BaseModel):
    """본인 vs 상대 램프 우열 집계 (비교 모드에서만)."""

    total: int  # 집계 대상 엔트리 수(양쪽 모두 성적이 없는 엔트리는 제외)
    win: int
    lose: int
    draw: int
    # 무승부를 뺀 엔트리 중 본인이 앞선 비율(%). 승패가 갈린 엔트리가 없으면 null.
    win_rate: float | None = None


class TableBoardResponse(BaseModel):
    """GET /iidx/tables/{slug}/board 응답 — 난이도표 + (선택) 클리어 램프."""

    slug: str
    name: str
    source: str
    play_style: str
    rating_type: str
    level: int | None = None
    grades: list[str] | None = None
    updated_at: datetime
    user: BoardUser | None = None      # 램프 주체. 비로그인·미지정이면 null
    opponent: BoardUser | None = None  # 비교 대상. 비교 모드가 아니면 null
    total_entries: int
    sections: list[BoardSection]
    comparison: BoardComparison | None = None
