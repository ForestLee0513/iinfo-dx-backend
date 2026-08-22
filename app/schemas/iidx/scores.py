"""사용자 성적 업로드/스냅샷 API 스키마."""

from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.iidx.profile import IidxProfileUpload


class CsvByStyle(BaseModel):
    """스타일별 성적 CSV 문자열 (크롤러 DonePayload.csv). 최소 하나는 있어야 한다."""

    SP: str | None = None
    DP: str | None = None


class ScoreUploadRequest(BaseModel):
    """POST /iidx/scores/upload 요청 — SP/DP 성적과 프로필을 한 번에 보낸다.

    쿼리 파라미터로 스타일을 나눠 두 번 호출하던 방식을 대체한다(토큰이 첫 호출에서
    만료돼 두 번째가 실패하는 문제 해소). source는 백엔드가 CSV 내용으로 자동
    판별하므로 참고용이며 저장에 사용하지 않는다.
    """

    csv: CsvByStyle
    profile: IidxProfileUpload | None = None
    source: str | None = None  # "score_download" 등 — 무시(자동 판별)


class UploadResponse(BaseModel):
    """단일 스타일 업로드 결과."""

    upload_id: str
    play_style: str
    source: str          # "official" | "crawled"
    song_count: int
    uploaded_at: datetime | None = None
    changed: bool        # False = 동일 내용, 스냅샷 미생성


class MultiUploadResponse(BaseModel):
    """POST /iidx/scores/upload 응답 — 업로드된 스타일별 결과 목록."""

    results: list[UploadResponse]


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


class ClearLampCounts(BaseModel):
    """레벨/스타일 기준 클리어 램프별 채보 개수 (8종 표준 램프).

    모수는 곡 마스터의 현역 채보 전체라, `no_play`에는 사용자 CSV에 아예
    없는 채보도 포함된다.
    """

    no_play: int = 0
    failed: int = 0
    assist_clear: int = 0
    easy_clear: int = 0
    clear: int = 0
    hard_clear: int = 0
    ex_hard_clear: int = 0
    full_combo: int = 0


class ClearLampPercentages(BaseModel):
    """ClearLampCounts와 동일한 키의 비율(%, 소수 1자리). total=0이면 전부 0.0."""

    no_play: float = 0.0
    failed: float = 0.0
    assist_clear: float = 0.0
    easy_clear: float = 0.0
    clear: float = 0.0
    hard_clear: float = 0.0
    ex_hard_clear: float = 0.0
    full_combo: float = 0.0


class ScoreSummaryResponse(BaseModel):
    """GET /iidx/scores/summary 응답 — 클리어 램프 비율 요약."""

    play_style: str
    level: int | None = None  # None이면 스타일 전체 레벨 합산
    total: int  # 곡 마스터 기준 해당 스타일/레벨의 현역 채보 수(=비율 모수)
    counts: ClearLampCounts
    percentages: ClearLampPercentages
    available_levels: list[int]  # 곡 마스터에 채보가 있는 레벨 목록(선택 UI용)


class UploadCalendarResponse(BaseModel):
    """GET /iidx/scores/upload-calendar 응답 — 날짜별 업로드 횟수(기여도 그래프용).

    날짜 경계는 요청 시 넘긴 IANA 타임존(`tz`) 기준이다. zoneinfo가 해당
    타임존의 서머타임 규칙을 자동 반영하므로, 일본/미국 등 서머타임을
    쓰는 지역의 사용자도 자신의 로컬 캘린더와 맞는 결과를 받는다.
    """

    style: str | None = None  # None이면 SP/DP 합산
    tz: str  # 집계에 사용된 IANA 타임존 (예: Asia/Seoul, America/New_York)
    since: date  # 조회 시작 날짜 (tz 기준 년/월/일)
    until: date  # 조회 종료 날짜 (tz 기준 년/월/일)
    total: int
    days: dict[date, int]  # "YYYY-MM-DD" -> 업로드 횟수. since~until 전체 날짜 포함(없으면 0)
