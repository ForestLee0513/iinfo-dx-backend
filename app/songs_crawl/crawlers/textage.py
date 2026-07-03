"""textage.cc 곡 마스터 크롤러.

titletbl.js / actbl.js 두 JS 데이터 파일을 받아 곡/채보 마스터를 구성한다.

데이터 형식 (확인됨):
  titletbl = { '태그': [버전, 독자ID, 옵션, "장르", "아티스트", "곡명", ("부제")], ... }
    - VERINDEX=0, GENREINDEX=3, ARTISTINDEX=4, TITLEINDEX=5, SUBTITLEINDEX=6
    - substream은 버전 번호 35 (SS=35)
  actbl = { '태그': [상태플래그, (슬롯별 플래그, 레벨) ...], ... }
    - DP ANOTHER 존재 플래그 = index 19, DP LEGGENDARIA = index 21 (확인됨)
    - 나머지 슬롯 배치는 SLOTS 상수 참고. 반드시 /songs-crawl/preview로
      실데이터와 대조해 확정한 뒤 SONG_CRAWL_TARGETS에 투입할 것.

주의: 파일이 Shift-JIS(cp932) 인코딩이며, 곡명에 HTML 태그가 섞여 있다.
      textage는 개인 운영 사이트 — 주 1회 파일 2개 fetch 이상으로 부하를 주지 말 것.
"""

from __future__ import annotations

import html
import re

import httpx

from app.core.config import settings
from app.songs_crawl.crawlers.base import SongMasterResult, register

BASE_URL = "https://textage.cc/score"

# textage VERINDEX -> 시리즈 이름
VERSION_NAMES: dict[int, str] = {
    1: "1st style", 2: "2nd style", 3: "3rd style", 4: "4th style",
    5: "5th style", 6: "6th style", 7: "7th style", 8: "8th style",
    9: "9th style", 10: "10th style", 11: "IIDX RED", 12: "HAPPY SKY",
    13: "DistorteD", 14: "GOLD", 15: "DJ TROOPERS", 16: "EMPRESS",
    17: "SIRIUS", 18: "Resort Anthem", 19: "Lincle", 20: "tricoro",
    21: "SPADA", 22: "PENDUAL", 23: "copula", 24: "SINOBUZ",
    25: "CANNON BALLERS", 26: "Rootage", 27: "HEROIC VERSE", 28: "BISTROVER",
    29: "CastHour", 30: "RESIDENT", 31: "EPOLIS", 32: "Pinky Crush",
    35: "substream",
}

# actbl 슬롯 배치: (플래그 index, 레벨 index, play_style, difficulty)
# ※ DP A(19)/DP L(21)만 외부 자료로 확인됨. 나머지는 잠정 배치이므로
#   /songs-crawl/preview로 기존 곡 레벨과 대조해 확정할 것.
SLOTS: list[tuple[int, int, str, str]] = [
    (1, 2, "SP", "BEGINNER"),
    (3, 4, "SP", "NORMAL"),
    (5, 6, "SP", "HYPER"),
    (7, 8, "SP", "ANOTHER"),
    (9, 10, "SP", "LEGGENDARIA"),
    (13, 14, "DP", "NORMAL"),
    (15, 16, "DP", "HYPER"),
    (19, 20, "DP", "ANOTHER"),
    (21, 22, "DP", "LEGGENDARIA"),
]

# JS 오브젝트 리터럴에서 '태그':[...] 엔트리 추출
_ENTRY_RE = re.compile(r"'(?P<tag>[^']+)'\s*:\s*\[(?P<body>[^\]]*)\]")
# 배열 본문에서 문자열("..." / '...')과 정수 토큰 추출
_TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|(-?\d+)')
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _parse_js_table(source: str, var_name: str) -> dict[str, list]:
    """`var_name={...}` 블록에서 태그별 배열을 파싱한다."""
    start = source.find(f"{var_name}=")
    if start < 0:
        raise ValueError(f"{var_name} 블록을 찾을 수 없습니다")

    result: dict[str, list] = {}
    for m in _ENTRY_RE.finditer(source, start):
        values: list = []
        for sm in _TOKEN_RE.finditer(m.group("body")):
            if sm.group(3) is not None:
                values.append(int(sm.group(3)))
            else:
                values.append(sm.group(1) if sm.group(1) is not None else sm.group(2))
        result[m.group("tag")] = values
    return result


def _clean_text(raw: str) -> str:
    """곡명/아티스트의 HTML 태그와 이스케이프를 정리한다."""
    text = _TAG_STRIP_RE.sub("", raw)
    text = html.unescape(text)
    return text.replace("\\'", "'").replace('\\"', '"').strip()


@register("textage")
class TextageCrawler:
    """textage 곡 마스터 크롤러.

    target 예시 (SONG_CRAWL_TARGETS): {"crawler": "textage"}
    """

    async def crawl(
        self, client: httpx.AsyncClient, target: dict
    ) -> list[SongMasterResult]:
        title_tbl = await self._fetch_table(client, "titletbl.js", "titletbl")
        act_tbl = await self._fetch_table(client, "actbl.js", "actbl")

        songs: list[dict] = []
        seen_versions: set[int] = set()

        for tag, row in title_tbl.items():
            # [버전, 독자ID, 옵션, 장르, 아티스트, 곡명, (부제)]
            if len(row) < 6:
                continue
            version = row[0] if isinstance(row[0], int) else None
            title = _clean_text(str(row[5]))
            if len(row) >= 7 and isinstance(row[6], str) and row[6]:
                title = f"{title} {_clean_text(row[6])}".strip()
            if not title:
                continue
            if version is not None:
                seen_versions.add(version)

            songs.append({
                "tag": tag,
                "title": title,
                "genre": _clean_text(str(row[3])) if isinstance(row[3], str) else None,
                "artist": _clean_text(str(row[4])) if isinstance(row[4], str) else None,
                "version": version,
                "charts": self._parse_charts(act_tbl.get(tag)),
            })

        versions = [
            {"id": n, "name": VERSION_NAMES.get(n, f"IIDX {n}")}
            for n in sorted(seen_versions)
        ]

        return [SongMasterResult(source="textage", versions=versions, songs=songs)]

    async def _fetch_table(
        self, client: httpx.AsyncClient, filename: str, var_name: str
    ) -> dict[str, list]:
        resp = await client.get(
            f"{BASE_URL}/{filename}",
            timeout=settings.REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        # textage는 Shift-JIS 인코딩
        return _parse_js_table(resp.content.decode("cp932", errors="replace"), var_name)

    def _parse_charts(self, act_row: list | None) -> list[dict]:
        """actbl 행에서 존재하는 채보(플래그>=1, 1<=레벨<=12)만 추출한다."""
        if not act_row:
            return []
        charts: list[dict] = []
        for flag_idx, level_idx, style, diff in SLOTS:
            if level_idx >= len(act_row):
                continue
            flag, level = act_row[flag_idx], act_row[level_idx]
            if not isinstance(flag, int) or not isinstance(level, int):
                continue
            if flag < 1 or not (1 <= level <= 12):
                continue
            charts.append({"play_style": style, "difficulty": diff, "level": level})
        return charts
