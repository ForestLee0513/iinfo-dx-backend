"""어드민 API 요청/응답 모델."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

JobScope = Literal["full", "songs", "tables"]
JobStatus = Literal["RUNNING", "DONE", "FAILED"]
StepStatus = Literal["PENDING", "RUNNING", "DONE", "FAILED"]
ScheduleDay = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


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
    status: JobStatus
    triggered_by: str  # "schedule" | "admin:<email>"
    steps: list[JobStep]
    resumed: bool = False  # 서버 재기동 후 이어서 실행된 작업인지
    created_at: str
    finished_at: str | None = None


class JobCreateRequest(BaseModel):
    """수동 크롤 실행 요청. full = 곡 마스터 → 난이도표 순차."""

    scope: JobScope = "full"


class JobListResponse(BaseModel):
    jobs: list[CrawlJob]


class ScheduleConfig(BaseModel):
    """주간 크롤 스케줄 설정 (TIMEZONE 기준 cron)."""

    enabled: bool
    day: ScheduleDay
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)


class ScheduleResponse(ScheduleConfig):
    timezone: str
    source: Literal["env", "redis"]  # redis = 어드민이 변경한 값이 적용 중
    next_run_at: str | None = None  # enabled=False면 None


class BanRequest(BaseModel):
    """사용자 접근 제한 요청."""

    reason: str  # 정지 사유 (필수)
    ban_until: datetime | None = None  # None = 영구 정지, 지정 시 해당 일시까지


class BanRecord(BaseModel):
    """user_bans 테이블 레코드 — 정지 이력 1건."""

    id: str
    user_id: str
    reason: str
    ban_until: datetime | None = None
    banned_by: str
    banned_at: datetime
    lifted_at: datetime | None = None
    lifted_by: str | None = None


class BanListResponse(BaseModel):
    """사용자 접근 제한 이력 목록."""

    records: list[BanRecord]
