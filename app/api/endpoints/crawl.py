"""크롤링 진단/미리보기 엔드포인트 (공개 — 쓰기 경로 없음).

실제 Supabase 반영은 주간 크롤링 스케줄러(app/services/scheduler.py)로만 이뤄진다.
여기서는 크롤러 구성 확인과 크롤러 미리보기만 제공한다(쓰기 없음).
"""

from dataclasses import asdict

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.crawlers import CRAWLER_REGISTRY, get_crawler

router = APIRouter()


@router.get("/targets", summary="크롤링 대상/크롤러 목록 조회")
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


class PreviewRequest(BaseModel):
    # 실행할 크롤러 이름. 등록된 크롤러는 GET /crawl/targets 로 확인할 수 있다.
    crawler: str = "5ch_sheet"
    # 크롤러에 넘길 설정. 크롤러마다 필요한 키가 다르다.
    #   5ch_sheet   : {"url", "play_style", "level"}
    #   numeric_json: {"url", "slug", "name", "source", "play_style", "level"}
    target: dict = Field(default_factory=dict)


@router.post("/preview", summary="선택한 크롤러로 크롤링 미리보기 (Supabase 반영 없음)")
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

    target = {**body.target, "crawler": body.crawler}
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
