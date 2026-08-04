"""성적 행을 iidx.songs / iidx.charts 와 매칭해 누락 필드를 보완한다.

매칭 전략:
  1. songs.title 완전 일치로 song_id 획득.
  2. 크롤 CSV(is_crawled=True)는 version/genre/artist 가 null이므로
     매칭된 songs 행의 값으로 보완한다. 공식 CSV는 CSV 원본을 그대로 쓴다.
  3. (song_id, play_style, difficulty) 로 charts 를 찾아 level 검증
     (크롤 CSV 레벨이 없거나 0인 경우 charts.level 로 보완).

N+1 쿼리를 피하려고 songs · charts 를 각각 한 번에 메모리로 로드한다.
"""

import asyncio
from dataclasses import dataclass

from app.db.session import get_supabase_iidx
from app.services.iidx.scores.parser import ChartScore


@dataclass
class EnrichedScore:
    """파싱된 ChartScore 에 DB 매칭 정보를 덧붙인 것."""

    score: ChartScore
    song_id: str | None         # iidx.songs.id (매칭 실패면 None)
    # 크롤 CSV에서 null이었던 필드 — 매칭 성공 시 songs 테이블에서 보완된 값.
    # 공식 CSV나 매칭 실패 시에는 None(→ 원본 score 필드 그대로 사용).
    filled_version: str | None
    filled_genre: str | None
    filled_artist: str | None


# ── DB 로드 헬퍼 ──────────────────────────────────────────────────────────────

def _load_songs() -> dict[str, dict]:
    """title → {id, genre, artist, version_name} 맵을 한 번에 로드한다."""
    result = (
        get_supabase_iidx()
        .table("songs")
        .select("id, title, genre, artist, versions(name)")
        .execute()
    )
    songs: dict[str, dict] = {}
    for row in result.data or []:
        ver = row.get("versions")
        songs[row["title"]] = {
            "id": row["id"],
            "genre": row.get("genre"),
            "artist": row.get("artist"),
            "version_name": ver["name"] if ver else None,
        }
    return songs


def _load_charts(play_style: str) -> dict[tuple[str, str], dict]:
    """(song_id, difficulty) → {id, level} 맵 (play_style 고정)."""
    result = (
        get_supabase_iidx()
        .table("charts")
        .select("id, song_id, difficulty, level")
        .eq("play_style", play_style)
        .execute()
    )
    return {
        (row["song_id"], row["difficulty"]): row
        for row in result.data or []
    }


# ── 공개 API ──────────────────────────────────────────────────────────────────

def enrich(
    scores: list[ChartScore],
    play_style: str,
    is_crawled: bool,
) -> list[EnrichedScore]:
    """songs/charts 마스터와 매칭해 누락 필드를 보완한 목록을 반환한다.

    - song_id: 매칭 성공 시 채워짐.
    - filled_version/genre/artist: is_crawled=True 이고 매칭 성공일 때만 채워짐.
    - level 보완: CSV 레벨이 0이고 charts 에 레벨이 있으면 charts.level 로 대체.
    """
    songs = _load_songs()
    charts = _load_charts(play_style)

    result: list[EnrichedScore] = []
    for s in scores:
        song = songs.get(s.title)
        song_id = song["id"] if song else None

        filled_version: str | None = None
        filled_genre: str | None = None
        filled_artist: str | None = None

        if is_crawled and song:
            filled_version = song.get("version_name")
            filled_genre = song.get("genre")
            filled_artist = song.get("artist")

        # charts 매칭으로 level 보완 (크롤 CSV에서 레벨이 잘못됐을 때 대비)
        if song_id:
            chart = charts.get((song_id, s.difficulty))
            if chart and s.level == 0 and chart.get("level"):
                s.level = chart["level"]

        result.append(EnrichedScore(
            score=s,
            song_id=song_id,
            filled_version=filled_version,
            filled_genre=filled_genre,
            filled_artist=filled_artist,
        ))
    return result


async def enrich_async(
    scores: list[ChartScore],
    play_style: str,
    is_crawled: bool,
) -> list[EnrichedScore]:
    return await asyncio.to_thread(enrich, scores, play_style, is_crawled)
