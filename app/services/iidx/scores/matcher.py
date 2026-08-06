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
import re
import unicodedata
from dataclasses import dataclass

from app.db.session import get_supabase_iidx
from app.services.iidx.scores.parser import ChartScore

# PostgREST 단일 응답 행 상한(기본 1000)을 우회하기 위한 페이지 크기.
# songs·charts 는 이 상한을 넘으므로 반드시 페이지네이션으로 전량 로드해야 한다.
_PAGE = 1000

# loose 키에서 제거할 문자: 영숫자·히라가나·가타카나·한자(CJK) 이외 전부.
# 괄호/대시/물결표/따옴표/공백 유무, 대소문자, ♥ 같은 장식문자를 무시하기 위함.
_LOOSE_STRIP = re.compile(r"[^0-9a-z぀-ヿ一-鿿]")


def _norm_title(t: str) -> str:
    """타이틀 매칭용 정규화.

    NFKC 로 호환문자(℃→°C, 전각 영숫자·기호→반각 등)를 통일하고 연속 공백을
    한 칸으로 접는다. eagate CSV 와 textage 크롤 타이틀의 표기 차이를 흡수한다.
    """
    return " ".join(unicodedata.normalize("NFKC", t).split())


def _loose_title(t: str) -> str:
    """공격적 정규화 — 악센트 제거 + 소문자화 후 영숫자/가나/한자만 남긴다.

    NFKD 로 분해해 결합 문자(악센트)를 떼어내므로 `Amor De Verão`↔`Amor De Verao`,
    `Mächö Mönky`↔`Macho Monky` 같은 발음기호 차이까지 흡수한다. 또한 괄호·대시·
    물결표 앞 공백 유무, 곡선/직선 따옴표, 대소문자, ♥ 같은 장식문자 차이도 무시한다
    (예: `B4U(...)` vs `B4U (...)`, `LOVE SHINE` vs `LOVE♥SHINE`).

    단 서로 다른 곡이 같은 키로 뭉개질 수 있으므로(`ACTØ`/`ACT` 등), 이 키는
    songs 내에서 유일할 때만 매칭에 사용한다(_load_songs 참고).
    """
    decomposed = unicodedata.normalize("NFKD", t.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _LOOSE_STRIP.sub("", stripped)


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

def _load_songs() -> tuple[dict[str, dict], dict[str, dict], dict[str, dict], dict[str, dict]]:
    """songs 전량을 로드해 (완전일치, 정규화, alias, loose) 4개 맵을 반환한다.

    - exact: title → {id, genre, artist, version_name}
    - norm : _norm_title(title) → 같은 값 (완전일치 실패 시 1차 폴백)
    - alias: _norm_title(별칭) → 같은 값 (2차 폴백). songs.aliases 에 수동 등록한
             eagate 표기를 정규화 키로 매핑한다.
    - loose: _loose_title(title) → 같은 값 (최종 폴백). 단 서로 다른 곡이 같은
             loose 키를 갖는 경우(충돌) 오매칭을 막기 위해 해당 키는 제외한다.

    PostgREST 기본 1000행 상한을 넘기므로 반드시 페이지네이션으로 전량 로드한다.
    exact/norm/alias 맵은 먼저 들어온 곡이 우선(중복 키는 무시)한다.
    """
    exact: dict[str, dict] = {}
    norm: dict[str, dict] = {}
    alias: dict[str, dict] = {}
    loose: dict[str, dict] = {}
    loose_conflicts: set[str] = set()  # 서로 다른 song_id가 물린 loose 키
    db = get_supabase_iidx()
    start = 0
    while True:
        rows = (
            db.table("songs")
            .select("id, title, aliases, genre, artist, versions(name)")
            .range(start, start + _PAGE - 1)
            .execute()
            .data
            or []
        )
        for row in rows:
            ver = row.get("versions")
            info = {
                "id": row["id"],
                "genre": row.get("genre"),
                "artist": row.get("artist"),
                "version_name": ver["name"] if ver else None,
            }
            exact[row["title"]] = info
            norm.setdefault(_norm_title(row["title"]), info)

            for a in row.get("aliases") or []:
                if a:
                    alias.setdefault(_norm_title(a), info)

            lk = _loose_title(row["title"])
            if not lk:
                continue
            existing = loose.get(lk)
            if existing is None:
                loose[lk] = info
            elif existing["id"] != info["id"]:
                loose_conflicts.add(lk)
        if len(rows) < _PAGE:
            break
        start += _PAGE
    for lk in loose_conflicts:
        loose.pop(lk, None)
    return exact, norm, alias, loose


def _load_charts(play_style: str) -> dict[tuple[str, str], dict]:
    """(song_id, difficulty) → {id, level} 맵 (play_style 고정).

    PostgREST 기본 1000행 상한을 넘기므로 페이지네이션으로 전량 로드한다.
    """
    charts: dict[tuple[str, str], dict] = {}
    db = get_supabase_iidx()
    start = 0
    while True:
        rows = (
            db.table("charts")
            .select("id, song_id, difficulty, level")
            .eq("play_style", play_style)
            .range(start, start + _PAGE - 1)
            .execute()
            .data
            or []
        )
        for row in rows:
            charts[(row["song_id"], row["difficulty"])] = row
        if len(rows) < _PAGE:
            break
        start += _PAGE
    return charts


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
    exact, norm, alias, loose = _load_songs()
    charts = _load_charts(play_style)

    result: list[EnrichedScore] = []
    for s in scores:
        # 완전일치 → NFKC 정규화 → 수동 alias → loose(구두점/공백/대소문자 무시) 순 폴백.
        # alias(수동 등록)를 loose(퍼지)보다 우선해 명시적 매핑이 항상 이긴다.
        norm_title = _norm_title(s.title)
        song = (
            exact.get(s.title)
            or norm.get(norm_title)
            or alias.get(norm_title)
            or loose.get(_loose_title(s.title))
        )
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
