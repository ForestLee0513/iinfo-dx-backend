"""사용자 성적 업로드/스냅샷 API 스키마."""

from datetime import datetime

from pydantic import BaseModel


class UploadResponse(BaseModel):
    """POST /iidx/scores/upload 응답."""

    upload_id: str
    play_style: str
    source: str          # "official" | "crawled"
    song_count: int
    uploaded_at: datetime | None = None
    changed: bool        # False = 동일 내용, 스냅샷 미생성


class SnapshotSummary(BaseModel):
    upload_id: str
    play_style: str
    source: str
    song_count: int
    uploaded_at: datetime
    is_current: bool


class SnapshotListResponse(BaseModel):
    """GET /iidx/scores/snapshots 응답."""

    snapshots: list[SnapshotSummary]
    current_upload_id: str | None = None


class RestoreResponse(BaseModel):
    """POST /iidx/scores/restore/{upload_id} 응답."""

    upload_id: str
    play_style: str
    applied_at: datetime


class ChartScoreItem(BaseModel):
    """단일 채보 성적 — 공식/크롤 공통."""

    title: str
    difficulty: str
    # 공식 CSV 전용 (크롤이면 null)
    version: str | None = None
    genre: str | None = None
    artist: str | None = None
    play_count: int | None = None
    last_played_at: datetime | None = None
    miss_count: int | None = None
    # 공통
    level: int
    ex_score: int
    pgreat: int
    great: int
    clear_type: str | None = None
    dj_level: str | None = None
    song_id: str | None = None


class ScoreListResponse(BaseModel):
    """GET /iidx/scores 응답."""

    play_style: str
    source: str | None = None
    upload_id: str | None = None
    scores: list[ChartScoreItem]


class DownloadUrlResponse(BaseModel):
    """GET /iidx/scores/snapshots/{upload_id}/download 응답."""

    url: str
    expires_in: int


class UploadTokenResponse(BaseModel):
    """POST /iidx/scores/token 응답 — 북마크릿 업로드용 단기 토큰."""

    token: str
    expires_in: int  # 초 단위 유효 기간
