"""곡 마스터 CRUD — 동기화(쓰기) + 어드민 조회(읽기).

- 쓰기: SongMasterResult -> sync_song_master RPC (service role 전용).
- 읽기: 어드민 카탈로그 조회용 곡/채보/버전 조회 (crud_tables.py와 같은 읽기 역할).

Supabase 파이썬 클라이언트는 동기식이므로 async 경로에서는
asyncio.to_thread로 감싸서 호출한다 (pipeline.py / admin_catalog.py 참고).
스키마/RPC 정의: supabase/migrations/20260803000000_baseline.sql
(versions/songs/charts + sync_song_master + 크롤 로그(crawl_sync_logs) 모두 iidx
스키마). 읽기·쓰기·로그 전부 iidx 클라이언트(get_supabase_svc)를 쓴다.
"""

from __future__ import annotations

import logging

from app.db.session import get_supabase_svc
from app.services.iidx.songs_crawl.crawlers.base import SongMasterResult

logger = logging.getLogger(__name__)

# crawl_sync_logs.target_key에 기록할 곡 마스터 식별 프리픽스
_LOG_KEY_PREFIX = "song-master:"


def _log(source: str, count: int | None, status: str, error: str | None) -> None:
    """iidx.crawl_sync_logs에 실행 기록. 로그 실패가 동기화를 막지 않게 한다."""
    try:
        get_supabase_svc().table("crawl_sync_logs").insert({
            "target_key": f"{_LOG_KEY_PREFIX}{source}",
            "crawler": source,
            "triggered_by": "schedule",
            "entry_count": count or 0,
            "status": status,
            "error": error,
        }).execute()
    except Exception:
        logger.exception("crawl_sync_logs insert 실패")


def sync_song_master(result: SongMasterResult) -> dict:
    """곡 마스터 결과 하나를 Supabase에 반영한다.

    RPC 내부가 단일 트랜잭션: versions/songs/charts upsert 후
    이번 크롤에 없는 곡/채보는 in_ac=false 처리 (삭제하지 않음).
    """
    try:
        res = get_supabase_svc().rpc("sync_song_master", {
            "p_payload": {"versions": result.versions, "songs": result.songs},
        }).execute()
        counts = res.data if isinstance(res.data, dict) else {}
        total = counts.get("songs_total", len(result.songs))
        _log(result.source, total, "SUCCESS", None)
        logger.info("곡 마스터 동기화 완료 (%s): songs=%s charts=%s",
                    result.source, counts.get("songs_total"), counts.get("charts_total"))
        return {"source": result.source, "status": "SUCCESS", **counts}
    except Exception as e:
        _log(result.source, None, "FAILED", str(e)[:2000])
        logger.exception("곡 마스터 동기화 실패 (%s)", result.source)
        return {"source": result.source, "status": "FAILED", "error": str(e)}


# ── 어드민 조회(읽기) ─────────────────────────────────────

# 목록 조회 시 채보는 제외하고 곡 메타데이터만 반환
_SONG_LIST_COLUMNS = (
    "id, textage_tag, title, series, genre, artist, bpm, version, in_ac, updated_at"
)

# 곡 목록 정렬 가능 컬럼 (곡은 단일 레벨/등급/레이팅이 없으므로 메타 컬럼만).
# 화이트리스트로 임의 컬럼 정렬을 차단한다.
SONG_SORT_COLUMNS = ("title", "version", "artist", "updated_at", "created_at")


def _flatten_version(row: dict) -> dict:
    """임베드된 versions 객체를 version_name 필드로 평탄화한다."""
    ver = row.pop("versions", None)
    row["version_name"] = ver["name"] if ver else None
    return row


def list_songs(
    title: str | None,
    version: int | None,
    in_ac: bool | None,
    page: int,
    per_page: int,
    sort: str = "title",
    order: str = "asc",
) -> dict:
    """필터·페이지네이션·정렬을 적용한 곡 목록 (채보 제외).

    sort는 SONG_SORT_COLUMNS 화이트리스트로 제한(벗어나면 title), order는 asc/desc.
    반환: {"songs": [...], "total": n} — total은 필터 적용 후 전체 개수.
    """
    q = (
        get_supabase_svc()
        .table("songs")
        .select(f"{_SONG_LIST_COLUMNS}, versions(name)", count="exact")
    )
    if title:
        q = q.ilike("title", f"%{title}%")
    if version is not None:
        q = q.eq("version", version)
    if in_ac is not None:
        q = q.eq("in_ac", in_ac)
    sort_col = sort if sort in SONG_SORT_COLUMNS else "title"
    start = (page - 1) * per_page
    res = (
        q.order(sort_col, desc=(order == "desc"))
        .range(start, start + per_page - 1)
        .execute()
    )
    songs = [_flatten_version(r) for r in res.data]
    return {"songs": songs, "total": res.count or 0}


def get_song(song_id: str) -> dict | None:
    """곡 1개 + 채보 전체를 조회. 없으면 None.

    charts는 song_id FK로 임베드해 한 번에 가져온다.
    """
    res = (
        get_supabase_svc()
        .table("songs")
        .select("*, versions(name), charts(*)")
        .eq("id", song_id)
        .limit(1)
        .execute()
    )
    rows = res.data
    return _flatten_version(rows[0]) if rows else None


def list_versions() -> list[dict]:
    """버전 목록 (곡 필터 드롭다운용), id 오름차순."""
    res = (
        get_supabase_svc()
        .table("versions")
        .select("id, name, abbrev")
        .order("id")
        .execute()
    )
    return res.data
