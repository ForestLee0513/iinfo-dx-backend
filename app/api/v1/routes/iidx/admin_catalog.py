"""admin 카탈로그 조회 라우터 (/admin/*) — 지금까지 등록된 서열표·곡 조회.

- 서열표(난이도표): GET /tables (목록), GET /tables/{slug} (상세 + 엔트리)
- 곡 마스터: GET /songs (목록, 검색/필터/페이지네이션), GET /songs/{song_id} (상세 + 채보)
- 버전: GET /versions (곡 필터 드롭다운용 버전 목록)

읽기 전용 — 데이터는 크롤링 스케줄러/수동 잡으로만 갱신된다(app/api/v1/endpoints/crawl.py).
서열표 읽기는 공개 API(/iidx/tables)와 동일한 crud_tables를 재사용하되 어드민 인증 뒤에 둔다.
모든 엔드포인트는 Supabase 토큰 검증 + ADMIN(또는 SUPER_ADMIN) 역할로 보호한다.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import AdminUser
from app.crud.iidx import songs as crud_songs, tables as crud_tables
from app.schemas.iidx.song import SongDetail, SongListResponse, VersionListResponse
from app.schemas.iidx.table import TableDetail, TableListResponse

router = APIRouter()

# 정렬 방향
SortOrder = Literal["asc", "desc"]
# 곡 목록 정렬 기준 (곡은 단일 레벨/등급/레이팅이 없으므로 메타 컬럼만)
SongSort = Literal["title", "version", "artist", "updated_at", "created_at"]
# 서열표 엔트리 정렬 기준
EntrySort = Literal["title", "level", "grade", "rating", "difficulty"]


# ── 서열표(난이도표) 조회 ──────────────────────────────────


@router.get("/tables", response_model=TableListResponse)
async def list_tables(_: AdminUser):
    """등록된 난이도표 전체 목록 (엔트리 제외)."""
    tables = await asyncio.to_thread(crud_tables.fetch_tables)
    return {"tables": tables}


@router.get("/tables/{slug}", response_model=TableDetail)
async def get_table(
    slug: str,
    _: AdminUser,
    sort: EntrySort | None = Query(
        None,
        description="엔트리 정렬 기준(미지정 시 저장 순서). "
        "grade는 표의 grades 서열 순, difficulty는 논리 순서로 정렬한다.",
    ),
    order: SortOrder = Query("asc", description="정렬 방향"),
):
    """난이도표 1개 + 엔트리 전체 (sort 지정 시 엔트리를 정렬해 반환)."""
    table = await asyncio.to_thread(crud_tables.fetch_table, slug)
    if table is None:
        raise HTTPException(status_code=404, detail=f"표를 찾을 수 없습니다: {slug}")
    if sort:
        crud_tables.sort_table_entries(table, sort, order)
    return table


# ── 곡 마스터 조회 ────────────────────────────────────────


@router.get("/songs", response_model=SongListResponse)
async def list_songs(
    _: AdminUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    title: str | None = Query(None, min_length=1, description="곡명 부분 일치 검색"),
    version: int | None = Query(None, description="시리즈 버전 id 필터"),
    in_ac: bool | None = Query(
        None, description="현행 AC 수록 여부 필터 — true=수록, false=삭제곡"
    ),
    sort: SongSort = Query("title", description="정렬 기준"),
    order: SortOrder = Query("asc", description="정렬 방향"),
):
    """곡 목록 조회 — 곡명 검색 / 버전·수록여부 필터 + 정렬 + 페이지네이션.

    total은 필터 적용 후 개수. 채보는 상세 조회에서만 반환한다.
    """
    result = await asyncio.to_thread(
        crud_songs.list_songs, title, version, in_ac, page, per_page, sort, order
    )
    return SongListResponse(
        songs=result["songs"],
        page=page,
        per_page=per_page,
        total=result["total"],
    )


@router.get("/versions", response_model=VersionListResponse)
async def list_versions(_: AdminUser):
    """곡 필터 드롭다운용 버전 목록 (id 오름차순)."""
    versions = await asyncio.to_thread(crud_songs.list_versions)
    return {"versions": versions}


@router.get("/songs/{song_id}", response_model=SongDetail)
async def get_song(song_id: str, _: AdminUser):
    """곡 1개 + 채보(SP/DP × 난이도) 전체."""
    song = await asyncio.to_thread(crud_songs.get_song, song_id)
    if song is None:
        raise HTTPException(status_code=404, detail=f"곡을 찾을 수 없습니다: {song_id}")
    return song
