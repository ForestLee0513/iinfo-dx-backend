"""크롤링 진단/미리보기 엔드포인트 (공개 — 쓰기 경로 없음).

실제 Supabase 반영은 주간 크롤링 스케줄러(app/crawl/scheduler.py)로만 이뤄진다.
여기서는 크롤러 구성 확인과 크롤러 미리보기만 제공한다(쓰기 없음).
"""

from dataclasses import asdict
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.crawl.crawlers import CRAWLER_REGISTRY, get_crawler

router = APIRouter()


class ScheduleInfo(BaseModel):
    enabled: bool
    day: str
    hour: int
    minute: int
    timezone: str


class TargetsResponse(BaseModel):
    registered_crawlers: list[str]
    targets: list[dict]  # CRAWL_TARGETS 설정 (크롤러별 키가 달라 dict)
    schedule: ScheduleInfo


@router.get(
    "/targets",
    summary="크롤링 대상/크롤러 목록 조회",
    response_model=TargetsResponse,
)
def list_targets():
    return {
        "registered_crawlers": list(CRAWLER_REGISTRY),
        "targets": settings.CRAWL_TARGETS,
        "schedule": {
            "enabled": settings.CRAWL_SCHEDULE_ENABLED,
            "day": settings.CRAWL_SCHEDULE_DAY,
            "hour": settings.CRAWL_SCHEDULE_HOUR,
            "minute": settings.CRAWL_SCHEDULE_MINUTE,
            "timezone": settings.TIMEZONE,
        },
    }


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


class PreviewRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "crawler": "5ch_sheet",
                "target": {
                    "url": "https://docs.google.com/spreadsheets/d/.../pubhtml",
                    "play_style": "SP",
                    "level": 12,
                },
            }
        }
    )

    # 실행할 크롤러 이름. 등록된 크롤러는 GET /crawl/targets 로 확인할 수 있다.
    crawler: str = "5ch_sheet"
    target: PreviewTarget = Field(default_factory=PreviewTarget)


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


class PreviewTable(BaseModel):
    table: PreviewTableDef
    entry_count: int
    entries: list[PreviewEntry]


class PreviewResponse(BaseModel):
    crawler: str
    target: dict  # 입력 target 에코 (크롤러별 추가 키가 있을 수 있어 dict)
    tables: list[PreviewTable]


@router.post(
    "/preview",
    summary="선택한 크롤러로 크롤링 미리보기 (Supabase 반영 없음)",
    response_model=PreviewResponse,
)
async def preview(body: PreviewRequest):
    """선택한 크롤러를 실제 크롤 경로 그대로 실행해 결과(TableResult)를 반환한다.

    Supabase에는 전혀 반영하지 않으며, 반환값은 동기화될 표/엔트리와 동일하다.
    """
    if body.crawler not in CRAWLER_REGISTRY:
        raise HTTPException(
            status_code=404,
            detail=(
                f"등록되지 않은 크롤러: {body.crawler} "
                f"(사용 가능: {list(CRAWLER_REGISTRY)})"
            ),
        )

    # 제공되지 않은(None) 필드는 제외해 크롤러의 필수값 누락(KeyError→422) 판별을 유지한다.
    target = {**body.target.model_dump(exclude_none=True), "crawler": body.crawler}
    async with httpx.AsyncClient() as client:
        try:
            results = await get_crawler(body.crawler).crawl(client, target)
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except KeyError as e:
            raise HTTPException(status_code=422, detail=f"target에 필요한 값이 없습니다: {e}")
        except NotImplementedError:
            raise HTTPException(
                status_code=501,
                detail=f"{body.crawler} 크롤러는 아직 파싱이 구현되지 않았습니다",
            )

    return {
        "crawler": body.crawler,
        "target": target,
        "tables": [
            {
                "table": asdict(r.table),
                "entry_count": len(r.entries),
                "entries": r.entries,
            }
            for r in results
        ],
    }
