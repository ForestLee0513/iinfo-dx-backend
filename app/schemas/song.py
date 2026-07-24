"""곡 마스터 조회 응답 스키마 (어드민 조회 전용).

Supabase songs / charts / versions 컬럼과 대응한다.
스키마 정의: supabase/migrations/20260718005000_song_master_schema.sql
"""

from datetime import datetime

from pydantic import BaseModel


class VersionSummary(BaseModel):
    """IIDX 시리즈 버전 (곡 필터 드롭다운/표시용)."""

    id: int
    name: str
    abbrev: str | None = None


class VersionListResponse(BaseModel):
    versions: list[VersionSummary]


class ChartSummary(BaseModel):
    """곡별 채보 1행 (SP/DP × 난이도)."""

    play_style: str  # 'SP' | 'DP'
    difficulty: str  # BEGINNER/NORMAL/HYPER/ANOTHER/LEGGENDARIA
    level: int | None = None
    notes: int | None = None  # 노트 수 (미수집 시 None)
    in_ac: bool  # 현행 AC 수록 여부


class SongSummary(BaseModel):
    """곡 목록의 곡 1개 요약 (채보 제외)."""

    id: str
    textage_tag: str | None = None  # textage 태그 (안정 식별자)
    title: str
    series: str | None = None
    genre: str | None = None
    artist: str | None = None
    bpm: str | None = None
    version: int | None = None  # versions.id
    version_name: str | None = None  # 임베드된 versions.name 평탄화
    in_ac: bool  # 현행 AC 수록 여부 (크롤에서 빠지면 false)
    updated_at: datetime


class SongListResponse(BaseModel):
    songs: list[SongSummary]
    page: int
    per_page: int
    total: int  # 필터 적용 후 전체 곡 수


class SongDetail(SongSummary):
    """곡 1개 + 채보 전체 (상세 조회)."""

    created_at: datetime
    charts: list[ChartSummary] = []
