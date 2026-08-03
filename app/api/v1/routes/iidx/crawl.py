"""통합 크롤 라우터 (/api/v1/iidx/crawl/*) — 곡 마스터 + 난이도표 크롤을 한 곳에서 다룬다.

- GET  /targets   : 크롤 대상 + 등록된 크롤러 목록 (공개, 읽기 전용)
- POST /targets, GET·PUT·DELETE /targets/{target_key} : 크롤 대상 CRUD (ADMIN)
  대상/스케줄은 Supabase(crawl_targets/crawl_schedules 테이블)에 영구 저장된다
  — 여기 등록해야 잡/스케줄에서 참조 가능하다.
- POST /preview   : 선택한 크롤러로 미리보기 — Supabase 반영 없음 (공개, 읽기 전용)
- POST /jobs      : 크롤 동기화 작업 생성 — 즉시 202, FE는 폴링으로 확인 (ADMIN)
                    scope="song"/"table" + target_id(등록된 대상 참조) 또는
                    target(대상을 body로 직접 지정 — crawler 키 필수, 미등록 대상도 즉시 실행)
- GET  /jobs, /jobs/{id} : 작업 조회 (ADMIN)
- GET·PUT·DELETE /schedules[/{target_key}] : 등록된 대상별 스케줄 조회/변경/삭제 (ADMIN)
  (ad-hoc target으로 실행한 작업은 target_key가 없어 스케줄 대상이 될 수 없다)

/targets(GET), /preview는 공개 스펙(/docs)에도 노출된다. 나머지는 ADMIN 역할 필요
— 리버스 프록시에서 공개 도메인에 대해 POST/GET/PUT/DELETE /iidx/crawl/targets/*(GET 목록 제외),
/iidx/crawl/jobs*, /iidx/crawl/schedules*는 차단해야 한다(README 참고).
"""

import logging
from dataclasses import asdict

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from redis.exceptions import RedisError

from app.api.deps import AdminUser
from app.core.config import settings
from app.core.openapi import PUBLIC
from app.services.iidx.admin import jobs, store
from app.services.iidx.admin import targets as admin_targets
from app.services.iidx.difficulty_crawl import scheduler
from app.services.iidx.difficulty_crawl.crawlers import CRAWLER_REGISTRY, get_crawler as get_table_crawler
from app.services.iidx.songs_crawl.crawlers.base import SONG_CRAWLER_REGISTRY, get_crawler as get_song_crawler
from app.schemas.iidx.crawl import (
    CrawlJob,
    CrawlPreviewRequest,
    CrawlPreviewResponse,
    CrawlTarget,
    CrawlTargetCreateRequest,
    CrawlTargetDetail,
    CrawlTargetsResponse,
    CrawlTargetUpdateRequest,
    JobCreateRequest,
    JobListResponse,
    SongMasterPreviewResponse,
    TargetScheduleConfig,
    TargetScheduleListResponse,
    TargetScheduleResponse,
    VersionPreview,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_REDIS_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Redis에 연결할 수 없습니다.",
)


def _registry_for(kind: str) -> dict:
    return SONG_CRAWLER_REGISTRY if kind == "song" else CRAWLER_REGISTRY


async def _target_or_404(target_key: str) -> dict:
    target = await admin_targets.get_target(target_key)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"크롤 대상을 찾을 수 없습니다: {target_key}",
        )
    return target


# 크롤러 종류와 무관하게 중복을 막을 식별 필드 (config 키 -> 사람이 읽는 이름)
_DUP_FIELDS = {"id": "id", "label": "이름", "slug": "slug", "name": "표 이름"}


def _norm(value) -> str | None:
    """중복 비교용 정규화 — 앞뒤 공백 제거 + 대소문자 무시. 빈 값이면 None."""
    return value.strip().casefold() if isinstance(value, str) and value.strip() else None


def _apply_slug_default(config: dict) -> None:
    """table 대상은 slug 생략 시 id를 slug로 채운다(공개 표 식별자 기본값).

    song 대상은 slug 개념이 없어 건드리지 않는다. slug를 명시하면 그대로 존중한다.
    """
    if config.get("kind") == "table" and not (config.get("slug") or "").strip():
        config["slug"] = config["id"]


async def _reject_duplicate_target(config: dict, exclude_key: str | None = None) -> None:
    """생성/수정하려는 대상이 기존 대상과 id/이름/slug/표 이름이 겹치면 409.

    크롤러 종류를 가리지 않고 등록된 모든 대상과 대조한다(크롤러마다 config 키가
    달라도 값이 실제로 존재하는 필드만 비교). exclude_key는 자기 자신(수정 시) 제외용.
    """
    for existing in await admin_targets.list_targets():
        if existing["key"] == exclude_key:
            continue
        dupes = [
            human
            for field, human in _DUP_FIELDS.items()
            if _norm(config.get(field)) is not None
            and _norm(config.get(field)) == _norm(existing.get(field))
        ]
        if dupes:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"이미 존재하는 값입니다: {', '.join(dupes)} (대상 {existing['key']}와 충돌).",
            )


# ── 크롤 대상 (목록 공개, CRUD는 ADMIN) ────────────────


@router.get("/targets", response_model=CrawlTargetsResponse, openapi_extra=PUBLIC)
async def list_crawl_targets():
    """등록된 곡/난이도표 크롤 대상과 등록된 크롤러 목록.

    어드민 대시보드의 대상 선택 드롭다운/스케줄 키(target_key) 소스이기도 하다.
    최소 필드만 반환한다 — url 등 크롤러별 설정은 GET /targets/{target_key}(ADMIN)에서만 조회.
    """
    all_targets = await admin_targets.list_targets()
    return CrawlTargetsResponse(
        registered_crawlers={
            "song": sorted(SONG_CRAWLER_REGISTRY),
            "table": sorted(CRAWLER_REGISTRY),
        },
        targets=[
            CrawlTarget(key=t["key"], kind=t["kind"], id=t["id"], label=t["label"], crawler=t["crawler"])
            for t in all_targets
        ],
    )


@router.post(
    "/targets", response_model=CrawlTargetDetail, status_code=status.HTTP_201_CREATED,
)
async def create_crawl_target(body: CrawlTargetCreateRequest, user: AdminUser):
    """크롤 대상을 새로 등록한다. 같은 kind 안에서 id가 이미 있으면 409."""
    registry = _registry_for(body.kind)
    if body.crawler not in registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"등록되지 않은 크롤러: {body.crawler} (사용 가능: {list(registry)})",
        )
    target_key = f"{body.kind}:{body.id}"
    existing = await store.load_target(target_key)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 존재하는 크롤 대상입니다: {target_key}",
        )
    config = body.model_dump()
    _apply_slug_default(config)
    await _reject_duplicate_target(config)
    await store.save_target(target_key, config)
    logger.info("크롤 대상 생성: %s (by %s)", target_key, user.email)
    return CrawlTargetDetail(key=target_key, **config)


@router.get("/targets/{target_key}", response_model=CrawlTargetDetail)
async def get_crawl_target(target_key: str, _: AdminUser):
    """크롤 대상 상세 — url 등 크롤러별 설정을 포함한다 (수정 폼 프리필용)."""
    target = await _target_or_404(target_key)
    return CrawlTargetDetail(**target)


@router.put("/targets/{target_key}", response_model=CrawlTargetDetail)
async def update_crawl_target(target_key: str, body: CrawlTargetUpdateRequest, user: AdminUser):
    """크롤 대상의 label/crawler/크롤러별 설정을 전체 교체한다. kind/id는 변경 불가."""
    target = await _target_or_404(target_key)
    registry = _registry_for(target["kind"])
    if body.crawler not in registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"등록되지 않은 크롤러: {body.crawler} (사용 가능: {list(registry)})",
        )
    config = {"kind": target["kind"], "id": target["id"], **body.model_dump()}
    _apply_slug_default(config)
    await _reject_duplicate_target(config, exclude_key=target_key)
    await store.save_target(target_key, config)
    logger.info("크롤 대상 수정: %s -> %s (by %s)", target_key, config, user.email)
    return CrawlTargetDetail(key=target_key, **config)


@router.delete("/targets/{target_key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_crawl_target(target_key: str, user: AdminUser):
    """크롤 대상을 삭제한다 — 걸려 있는 스케줄(DB cascade)과 등록된 job도 함께 정리한다."""
    target = await _target_or_404(target_key)
    await store.delete_target(target_key)  # crawl_schedules 행도 on delete cascade로 함께 삭제됨
    scheduler.apply_target_schedule(
        target_key, target["kind"], target["id"], {"enabled": False, "triggers": []}
    )
    logger.info("크롤 대상 삭제: %s (by %s)", target_key, user.email)


# ── 미리보기 (공개, Supabase 반영 없음) ────────────────


@router.post("/preview", response_model=CrawlPreviewResponse, openapi_extra=PUBLIC)
async def preview(body: CrawlPreviewRequest):
    """선택한 크롤러를 실제 크롤 경로 그대로 실행해 결과를 반환한다. Supabase에는 반영하지 않는다."""
    # 제공되지 않은(None) 필드는 제외해 크롤러의 필수값 누락(KeyError→422) 판별을 유지한다.
    target = {**body.target.model_dump(exclude_none=True), "crawler": body.crawler}

    if body.kind == "song":
        if body.crawler not in SONG_CRAWLER_REGISTRY:
            raise HTTPException(
                status_code=404,
                detail=f"등록되지 않은 곡 마스터 크롤러: {body.crawler} "
                       f"(사용 가능: {list(SONG_CRAWLER_REGISTRY)})",
            )
        try:
            async with httpx.AsyncClient() as client:
                results = await get_song_crawler(body.crawler).crawl(client, target)
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=str(e))

        songs = [s for r in results for s in r.songs]
        if body.title:
            songs = [s for s in songs if body.title.lower() in s["title"].lower()]
        song_result = SongMasterPreviewResponse(
            source=",".join(r.source for r in results),
            songs_total=sum(len(r.songs) for r in results),
            charts_total=sum(len(s["charts"]) for r in results for s in r.songs),
            versions=[VersionPreview(**v) for r in results for v in r.versions],
            songs=songs,
        )
        return CrawlPreviewResponse(
            kind="song", crawler=body.crawler, target=target, song_result=song_result,
        )

    if body.crawler not in CRAWLER_REGISTRY:
        raise HTTPException(
            status_code=404,
            detail=f"등록되지 않은 크롤러: {body.crawler} (사용 가능: {list(CRAWLER_REGISTRY)})",
        )
    async with httpx.AsyncClient() as client:
        try:
            results = await get_table_crawler(body.crawler).crawl(client, target)
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

    return CrawlPreviewResponse(
        kind="table",
        crawler=body.crawler,
        target=target,
        tables=[
            {"table": asdict(r.table), "entry_count": len(r.entries), "entries": r.entries}
            for r in results
        ],
    )


# ── 크롤 수동 실행 (ADMIN) ─────────────────────────────


@router.post("/jobs", response_model=CrawlJob, status_code=status.HTTP_202_ACCEPTED)
async def trigger_crawl(body: JobCreateRequest, user: AdminUser):
    """크롤 동기화 작업을 생성하고 백그라운드로 실행한다. FE는 작업 id로 폴링.

    scope="song"/"table" + target_id로 등록된 대상 하나를 참조하거나,
    target으로 대상 설정을 직접 내려 등록 여부와 무관하게 즉시 실행할 수 있다.
    """
    if body.target_id is not None:
        target = await _target_or_404(f"{body.scope}:{body.target_id}")
        if target["kind"] != body.scope:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"scope({body.scope})와 target_id({body.target_id})의 종류가 일치하지 않습니다.",
            )
    elif body.target is not None:
        registry = _registry_for(body.scope)
        crawler_name = body.target["crawler"]
        if crawler_name not in registry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"등록되지 않은 크롤러: {crawler_name} (사용 가능: {list(registry)})",
            )
    try:
        job = await jobs.create_job(
            scope=body.scope,
            target_id=body.target_id,
            target=body.target,
            triggered_by=f"admin:{user.email}",
        )
    except jobs.JobAlreadyRunning as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 실행 중인 작업이 있습니다: {e.job_id}",
        )
    except RedisError:
        raise _REDIS_UNAVAILABLE
    jobs.launch(job)
    return job


@router.get("/jobs", response_model=JobListResponse)
async def list_crawl_jobs(_: AdminUser, limit: int = Query(20, ge=1, le=50)):
    try:
        return {"jobs": await store.list_jobs(limit)}
    except RedisError:
        raise _REDIS_UNAVAILABLE


@router.get("/jobs/{job_id}", response_model=CrawlJob)
async def get_crawl_job(job_id: str, _: AdminUser):
    try:
        job = await store.get_job(job_id)
    except RedisError:
        raise _REDIS_UNAVAILABLE
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="작업을 찾을 수 없습니다.")
    return job


# ── 등록된 대상별 스케줄 (ADMIN) ───────────────────────


def _schedule_response(target: dict, config: dict) -> dict:
    next_run = scheduler.next_run_time(target["key"])
    return {
        **config,
        "target_key": target["key"],
        "kind": target["kind"],
        "label": target["label"],
        "timezone": settings.TIMEZONE,
        "next_run_at": next_run.isoformat() if next_run else None,
    }


@router.get("/schedules", response_model=TargetScheduleListResponse)
async def list_schedules(_: AdminUser):
    saved = await store.load_all_schedules()
    all_targets = await admin_targets.list_targets()
    schedules = [
        _schedule_response(target, saved.get(target["key"], {"enabled": False, "triggers": []}))
        for target in all_targets
    ]
    return {"schedules": schedules}


@router.get("/schedules/{target_key}", response_model=TargetScheduleResponse)
async def get_schedule(target_key: str, _: AdminUser):
    target = await _target_or_404(target_key)
    config = await store.load_schedule(target_key)
    return _schedule_response(target, config or {"enabled": False, "triggers": []})


@router.put("/schedules/{target_key}", response_model=TargetScheduleResponse)
async def update_schedule(target_key: str, body: TargetScheduleConfig, user: AdminUser):
    """등록된 대상 하나의 스케줄 변경 — Supabase에 저장 후 실행 중인 스케줄러에 즉시 반영."""
    target = await _target_or_404(target_key)
    config = body.model_dump()
    await store.save_schedule(target_key, config)
    scheduler.apply_target_schedule(target_key, target["kind"], target["id"], config)
    logger.info("크롤 스케줄 변경: %s -> %s (by %s)", target_key, config, user.email)
    return _schedule_response(target, config)


@router.delete("/schedules/{target_key}", response_model=TargetScheduleResponse)
async def delete_schedule(target_key: str, user: AdminUser):
    """등록된 대상 하나의 스케줄 삭제 — 비활성 상태로 되돌리고 등록된 job을 제거한다."""
    target = await _target_or_404(target_key)
    await store.delete_schedule(target_key)
    config = {"enabled": False, "triggers": []}
    scheduler.apply_target_schedule(target_key, target["kind"], target["id"], config)
    logger.info("크롤 스케줄 삭제: %s (by %s)", target_key, user.email)
    return _schedule_response(target, config)
