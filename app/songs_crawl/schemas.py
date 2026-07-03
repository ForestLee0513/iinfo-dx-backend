"""songs_crawl 모듈의 요청/응답 모델. /docs에 실제 스키마가 노출되도록 typed model 사용."""

from __future__ import annotations

from pydantic import BaseModel


class SongCrawlTarget(BaseModel):
    """설정된 곡 마스터 크롤 타깃."""

    crawler: str


class SongCrawlTargetsResponse(BaseModel):
    targets: list[SongCrawlTarget]
    registered_crawlers: list[str]


class ChartPreview(BaseModel):
    play_style: str
    difficulty: str
    level: int


class SongPreview(BaseModel):
    tag: str
    title: str
    genre: str | None = None
    artist: str | None = None
    version: int | None = None
    charts: list[ChartPreview]


class VersionPreview(BaseModel):
    id: int
    name: str


class SongMasterPreviewResponse(BaseModel):
    """실제로 sync될 내용의 미리보기. SLOTS 배치 검증에 사용."""

    source: str
    songs_total: int
    charts_total: int
    versions: list[VersionPreview]
    songs: list[SongPreview]  # limit 파라미터만큼의 샘플
