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
