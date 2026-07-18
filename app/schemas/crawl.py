"""크롤 관련 요청/응답 모델 — /api/v1/crawl 라우터에서 사용.

곡 마스터(song)와 난이도표(table) 두 종류의 크롤을 하나의 라우터로 다루므로
잡/스케줄/대상/미리보기 모델을 모두 여기 모은다.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

JobScope = Literal["full", "song", "table"]
JobStatus = Literal["RUNNING", "DONE", "FAILED"]
StepStatus = Literal["PENDING", "RUNNING", "DONE", "FAILED"]
ScheduleDay = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CrawlTargetKind = Literal["song", "table"]


# ── 크롤 대상 ──────────────────────────────────────────


class CrawlTarget(BaseModel):
    """크롤 대상 1건 — 어드민 대시보드 드롭다운(스케줄 생성용) 소스."""

    key: str  # f"{kind}:{id}" — 스케줄/작업 API의 target_key/target_id로 사용
    kind: CrawlTargetKind
    id: str
    label: str
    crawler: str


class CrawlTargetsResponse(BaseModel):
    """설정된 크롤 대상 + 등록된 크롤러 목록 (진단 + 드롭다운 소스 겸용)."""

    registered_crawlers: dict[str, list[str]]  # {"song": [...], "table": [...]}
    targets: list[CrawlTarget]


class CrawlTargetCreateRequest(BaseModel):
    """크롤 대상 생성 요청. crawler별 추가 설정(url/play_style/level/slug/name/source 등)은
    정의되지 않은 키로 그대로 허용한다(표준 필드는 크롤러마다 다르므로 여기서 강제하지 않음)."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "kind": "table",
                "id": "5ch_sp12",
                "label": "5ch SP12",
                "crawler": "5ch_sheet",
                "url": "https://docs.google.com/spreadsheets/d/.../pubhtml",
                "play_style": "SP",
                "level": 12,
            }
        },
    )

    kind: CrawlTargetKind
    id: str = Field(min_length=1)
    label: str
    crawler: str


class CrawlTargetUpdateRequest(BaseModel):
    """크롤 대상 수정 요청 — kind/id는 경로(target_key)로 고정되어 본문에서 받지 않는다.

    label/crawler/크롤러별 추가 설정을 전체 교체한다(부분 patch 아님).
    """

    model_config = ConfigDict(extra="allow")

    label: str
    crawler: str


class CrawlTargetDetail(BaseModel):
    """크롤 대상 상세 — 크롤러별 추가 설정(url 등)을 그대로 포함한다 (ADMIN 전용).

    공개 목록(`CrawlTarget`)은 최소 필드만 노출하므로 별도 모델로 분리한다.
    """

    model_config = ConfigDict(extra="allow")

    key: str
    kind: CrawlTargetKind
    id: str
    label: str
    crawler: str


# ── 미리보기 (Supabase 반영 없음) ──────────────────────


class PreviewTarget(BaseModel):
    """크롤러에 넘길 설정. 크롤러마다 사용하는 필드가 다르다.

    - 5ch_sheet   : url, play_style, level
    - numeric_json: url, slug, name, source, play_style, level

    등록된 커스텀 크롤러가 추가 필드를 쓸 수 있도록 정의되지 않은 키도 허용한다.
    """

    model_config = ConfigDict(extra="allow")

    url: str | None = Field(None, description="크롤링 대상 URL (pubhtml/JSON 등)")
    play_style: Literal["SP", "DP"] | None = Field(None, description="플레이 스타일")
    level: int | None = Field(None, ge=1, le=12, description="보면 레벨 (1~12)")
    slug: str | None = Field(None, description="표 slug (numeric_json 등에서 사용)")
    name: str | None = Field(None, description="표 이름")
    source: str | None = Field(None, description="출처 (예: 5ch, cpi)")


class CrawlPreviewRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "kind": "table",
                "crawler": "5ch_sheet",
                "target": {
                    "url": "https://docs.google.com/spreadsheets/d/.../pubhtml",
                    "play_style": "SP",
                    "level": 12,
                },
            }
        }
    )

    kind: CrawlTargetKind = "table"
    # 실행할 크롤러 이름. 등록된 크롤러는 GET /crawl/targets 로 확인할 수 있다.
    crawler: str = "5ch_sheet"
    target: PreviewTarget = Field(default_factory=PreviewTarget)
    # kind="song"일 때만 사용 — 곡명 부분 일치 필터
    title: str | None = None


class PreviewTableDef(BaseModel):
    """미리보기 표 정의 (크롤러가 만드는 TableDef와 동일 필드)."""

    slug: str
    name: str
    source: str
    play_style: str
    rating_type: str
    level: int | None = None
    grades: list[str] | None = None


class PreviewEntry(BaseModel):
    """미리보기 곡 엔트리. GRADE 방식은 grade를, NUMERIC 방식은 rating을 채운다."""

    title: str
    series: str | None = None
    play_style: str
    difficulty: str  # NORMAL/HYPER/ANOTHER/LEGGENDARIA
    level: int | None = None
    grade: str | None = None
    rating: float | None = None
    table_type: str | None = None  # 'STRENGTH'=지력 / 'PERSONAL'=개인차 (5ch)


class PreviewTable(BaseModel):
    table: PreviewTableDef
    entry_count: int
    entries: list[PreviewEntry]


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
    in_ac: bool = True  # actbl 상태플래그 bit0 (false = tt2/firebrick 삭제곡)
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
    songs: list[SongPreview]  # 전체 곡 목록 (title 필터 적용 시 필터링된 결과)


class CrawlPreviewResponse(BaseModel):
    kind: CrawlTargetKind
    crawler: str
    target: dict  # 입력 target 에코 (크롤러별 추가 키가 있을 수 있어 dict)
    tables: list[PreviewTable] | None = None  # kind="table"
    song_result: SongMasterPreviewResponse | None = None  # kind="song"


# ── 크롤 작업(잡) ──────────────────────────────────────


class JobStep(BaseModel):
    """작업의 단일 스텝. 스텝 단위로 Redis에 체크포인트가 남아 이어하기 기준이 된다."""

    name: str  # "song_sync" | "table_sync"
    status: StepStatus = "PENDING"
    result: dict | None = None  # 파이프라인 반환값 그대로
    error: str | None = None


class CrawlJob(BaseModel):
    """크롤 동기화 작업 상태. 어드민 FE가 폴링으로 진행 상황을 확인한다."""

    id: str
    scope: JobScope
    target_id: str | None = None  # scope="song"/"table"일 때 등록된 대상 하나만 지정(None=전체)
    target: dict | None = None  # scope="song"/"table"일 때 대상을 직접 지정(ad-hoc, 등록 여부 무관)
    status: JobStatus
    triggered_by: str  # "schedule" | "admin:<email>"
    steps: list[JobStep]
    resumed: bool = False  # 서버 재기동 후 이어서 실행된 작업인지
    created_at: str
    finished_at: str | None = None


class JobCreateRequest(BaseModel):
    """수동 크롤 실행 요청. full = 곡 마스터 → 난이도표 순차(전체 대상).

    scope="song"/"table"일 때 target_id(등록된 대상 참조) 또는 target(대상을
    직접 지정 — crawler 키 필수, 미등록 대상도 즉시 실행 가능) 중 하나만 줄 수 있다.
    둘 다 생략하면 해당 종류의 등록된 전체 대상을 동기화한다.
    """

    scope: JobScope = "full"
    target_id: str | None = None
    target: dict | None = None

    @model_validator(mode="after")
    def _check_target(self):
        if self.scope == "full" and (self.target_id is not None or self.target is not None):
            raise ValueError("scope='full'에는 target_id/target을 지정할 수 없습니다.")
        if self.target_id is not None and self.target is not None:
            raise ValueError("target_id와 target은 동시에 지정할 수 없습니다.")
        if self.target is not None and "crawler" not in self.target:
            raise ValueError("target에는 'crawler' 키가 필요합니다.")
        return self


class JobListResponse(BaseModel):
    jobs: list[CrawlJob]


# ── 대상별 스케줄 ─────────────────────────────────────


class ScheduleTrigger(BaseModel):
    """스케줄 트리거 1건 (특정 요일 + 시각). 대상 하나에 여러 개 지정 가능."""

    day: ScheduleDay
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)


class TargetScheduleConfig(BaseModel):
    """크롤 대상 1건에 대한 스케줄 설정 — 요일·시각을 여러 개 지정할 수 있다."""

    enabled: bool
    triggers: list[ScheduleTrigger] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_triggers(self):
        if self.enabled and not self.triggers:
            raise ValueError("enabled=true인 스케줄은 triggers가 1개 이상 필요합니다.")
        return self


class TargetScheduleResponse(TargetScheduleConfig):
    target_key: str
    kind: CrawlTargetKind
    label: str
    timezone: str
    next_run_at: str | None = None  # enabled=False거나 트리거가 없으면 None


class TargetScheduleListResponse(BaseModel):
    schedules: list[TargetScheduleResponse]
