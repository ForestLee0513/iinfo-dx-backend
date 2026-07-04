"""곡 마스터 크롤 진단 라우터 (공개, 읽기 전용).

difficulty_crawl의 /targets, /preview와 동일한 성격:
- /targets : 설정된 타깃과 등록된 크롤러 목록
- /preview : 크롤만 수행하고 sync 없이 결과 반환.
             textage SLOTS(actbl 슬롯 배치) 검증 용도 — 아는 곡의 레벨과 대조할 것.
수동 sync 엔드포인트는 의도적으로 없다 (admin 레포에서 처리).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.services.songs_crawl.crawlers.base import SONG_CRAWLER_REGISTRY
from app.services.songs_crawl.pipeline import crawl_song_targets
from app.schemas.song import (
    SongCrawlTargetsResponse,
    SongMasterPreviewResponse,
)

router = APIRouter()


@router.get(
    "/targets",
    summary="곡 마스터 크롤 타깃/크롤러 목록 조회",
    response_model=SongCrawlTargetsResponse,
)
async def list_targets() -> SongCrawlTargetsResponse:
    """설정된 곡 마스터 크롤 타깃과 등록된 크롤러 목록."""
    return SongCrawlTargetsResponse(
        targets=settings.SONG_CRAWL_TARGETS,
        registered_crawlers=sorted(SONG_CRAWLER_REGISTRY),
    )


@router.get(
    "/preview",
    summary="곡 마스터 크롤링 미리보기 (Supabase 반영 없음)",
    response_model=list[SongMasterPreviewResponse],
)
async def preview(
    limit: int = Query(20, ge=1, le=200, description="응답에 포함할 곡 샘플 수"),
    title: str | None = Query(None, description="곡명 부분 일치 필터 (SLOTS 검증용)"),
) -> list[SongMasterPreviewResponse]:
    """크롤만 수행하고 sync 없이 결과를 반환한다.

    전체 곡을 다 내려주면 응답이 너무 커지므로 totals + 샘플만 반환.
    특정 곡의 슬롯 파싱을 확인하려면 ?title=곡명 으로 필터.
    """
    try:
        results = await crawl_song_targets()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"크롤 실패: {e}")

    previews: list[SongMasterPreviewResponse] = []
    for r in results:
        songs = r.songs
        if title:
            songs = [s for s in songs if title.lower() in s["title"].lower()]

        previews.append(SongMasterPreviewResponse(
            source=r.source,
            songs_total=len(r.songs),
            charts_total=sum(len(s["charts"]) for s in r.songs),
            versions=r.versions,
            songs=songs[:limit],
        ))
    return previews
