"""textage.cc 곡 마스터 크롤러.

titletbl.js / actbl.js 두 JS 데이터 파일을 받아 곡/채보 마스터를 구성한다.

데이터 형식 (scrlist.js 렌더링 코드로 확인됨):
  titletbl = { '태그': [버전, 독자ID, 옵션, "장르", "아티스트", "곡명", ("부제")], ... }
    - VERINDEX=0, GENREINDEX=3, ARTISTINDEX=4, TITLEINDEX=5, SUBTITLEINDEX=6
    - substream은 버전 번호 35 (파일 상단에 SS=35 정의, 행에는 SS 식별자로 등장)
    - 곡명/부제에 "..." .fontcolor("#ff4080") 형태의 JS 메서드 호출로 색상이
      붙는다. 색상은 신곡 표시 등 장식일 뿐이므로 제거하되, 판별에 쓰지 않는다.
      단 곡명 자체가 색상코드처럼 생긴 곡(#CMFLG)이 있으므로 문자열 내용은
      건드리지 말고 .fontcolor("...") 호출 패턴만 제거할 것.
  actbl = { '태그': [상태플래그, (슬롯별 레벨, 옵션) ...], ... }
    - scrlist.js get_level 기준: type*2+1 = 레벨, type*2+2 = 옵션 (SLOTS 참고)
    - 옵션 bit2(&4) = AC 수록 (get_sdata의 acin)
    - 레벨 10~15는 A~F 식별자 (파일 상단에 A=10..F=15 정의)
    - 상태플래그 bit0 == 0 → 곡명 셀이 class=tt2(firebrick)로 렌더링되는
      AC 삭제곡 (get_tdata). 삭제곡 판별은 이 플래그로만 한다 — 곡명의
      인라인 색상(#ff4080 등)은 삭제 여부와 무관.

주의: 파일이 Shift-JIS(cp932) 인코딩이며, 곡명에 HTML 태그가 섞여 있다.
      주석 처리된(//) 행이 있으므로 파싱 전에 걸러야 한다.
      textage는 개인 운영 사이트 — 주 1회 파일 2개 fetch 이상으로 부하를 주지 말 것.
"""

from __future__ import annotations

import html
import re

import httpx

from app.core.config import settings
from app.services.iidx.songs_crawl.crawlers.base import SongMasterResult, register

BASE_URL = "https://textage.cc/score"

# textage VERINDEX -> 시리즈 이름
VERSION_NAMES: dict[int, str] = {
    0: "CS",  # 가정용 전용곡 (scrlist.js vertbl2[0]="CS")
    1: "1st style", 2: "2nd style", 3: "3rd style", 4: "4th style",
    5: "5th style", 6: "6th style", 7: "7th style", 8: "8th style",
    9: "9th style", 10: "10th style", 11: "IIDX RED", 12: "HAPPY SKY",
    13: "DistorteD", 14: "GOLD", 15: "DJ TROOPERS", 16: "EMPRESS",
    17: "SIRIUS", 18: "Resort Anthem", 19: "Lincle", 20: "tricoro",
    21: "SPADA", 22: "PENDUAL", 23: "copula", 24: "SINOBUZ",
    25: "CANNON BALLERS", 26: "Rootage", 27: "HEROIC VERSE", 28: "BISTROVER",
    29: "CastHour", 30: "RESIDENT", 31: "EPOLIS", 32: "Pinky Crush",
    33: "Sparkle Shower",
    35: "substream",
}

# actbl 슬롯 배치: (레벨 index, 옵션 index, play_style, difficulty)
# scrlist.js get_level(tag, type, num) = actbl[tag][type*2+num] 에서 도출.
# type 순서: SPBo(0) SPB(1) SPN(2) SPH(3) SPA(4) SPX(5) DPB(6) DPN(7) DPH(8) DPA(9) DPX(10)
SLOTS: list[tuple[int, int, str, str]] = [
    (3, 4, "SP", "BEGINNER"),
    (5, 6, "SP", "NORMAL"),
    (7, 8, "SP", "HYPER"),
    (9, 10, "SP", "ANOTHER"),
    (11, 12, "SP", "LEGGENDARIA"),
    (15, 16, "DP", "NORMAL"),
    (17, 18, "DP", "HYPER"),
    (19, 20, "DP", "ANOTHER"),
    (21, 22, "DP", "LEGGENDARIA"),
]

# 옵션 비트 (scrlist.js get_sdata): bit1(&2)=공식 12단계, bit2(&4)=AC 수록
_OPT_IN_AC = 4

# 행 배열 안의 식별자 토큰 값 (파일 상단 var 정의)
_IDENT_VALUES: dict[str, int] = {
    "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15,  # actbl 레벨 10~15
    "SS": 35,  # titletbl substream 버전
}

# JS 오브젝트 리터럴에서 '태그':[...] 엔트리 추출.
# body는 문자열 리터럴 또는 ]/따옴표가 아닌 문자들의 나열 — 곡명 안의 ']'
# ("Friction[!]Function", "[ ]DENTITY" 등)에서 행이 끊기지 않게 한다.
_ENTRY_RE = re.compile(
    r"'(?P<tag>[^']+)'\s*:\s*\["
    r"(?P<body>(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[^\]\"'])*)"
    r"\]"
)
# 배열 본문에서 문자열("..." / '...'), 정수, 식별자(A~F/SS 등) 토큰 추출.
# 문자열이 먼저 통째로 소비되므로 식별자 대안은 따옴표 밖에서만 매칭된다.
_TOKEN_RE = re.compile(
    r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|(-?\d+)|([A-Za-z_]\w*)'
)
# 곡명 장식용 색상 호출: "곡명".fontcolor("#ff4080") — 호출 패턴만 제거한다.
# 곡명 문자열 자체("#CMFLG" 등)는 배열 원소라서 이 패턴에 걸리지 않는다.
_FONTCOLOR_RE = re.compile(r'\.fontcolor\(\s*"[^"]*"\s*\)')
# 통째로 주석 처리된 행 (// 로 시작하는 라인)
_COMMENT_LINE_RE = re.compile(r"^\s*//.*$", re.MULTILINE)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _parse_js_table(source: str, var_name: str) -> dict[str, list]:
    """`var_name={...}` 블록에서 태그별 배열을 파싱한다."""
    source = _COMMENT_LINE_RE.sub("", source)
    start = source.find(f"{var_name}=")
    if start < 0:
        raise ValueError(f"{var_name} 블록을 찾을 수 없습니다")

    result: dict[str, list] = {}
    for m in _ENTRY_RE.finditer(source, start):
        body = _FONTCOLOR_RE.sub("", m.group("body"))
        values: list = []
        for sm in _TOKEN_RE.finditer(body):
            if sm.group(3) is not None:
                values.append(int(sm.group(3)))
            elif sm.group(4) is not None:
                # A~F(레벨 10~15), SS(35) 같은 식별자. 모르는 식별자는 버린다.
                if sm.group(4) in _IDENT_VALUES:
                    values.append(_IDENT_VALUES[sm.group(4)])
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

    등록 예시 (POST /api/v1/iidx/crawl/targets): {"kind":"song","id":"textage","label":"Textage","crawler":"textage"}
    """

    async def crawl(
        self, client: httpx.AsyncClient, target: dict
    ) -> list[SongMasterResult]:
        title_tbl = await self._fetch_table(client, "titletbl.js", "titletbl")
        act_tbl = await self._fetch_table(client, "actbl.js", "actbl")

        songs: list[dict] = []
        seen_versions: set[int] = set()

        for tag, row in title_tbl.items():
            # AC 곡 리스트의 기준은 actbl이다 (scrlist.js가 `for (tag in mt)`로
            # actbl 키를 순회). titletbl에만 있는 행(CS 전용곡, __dmy__ 더미,
            # 채보 뷰어 전용 변형 등)은 곡 마스터에 넣지 않는다.
            if tag not in act_tbl:
                continue
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

            act_row = act_tbl.get(tag)
            songs.append({
                "tag": tag,
                "title": title,
                "genre": _clean_text(str(row[3])) if isinstance(row[3], str) else None,
                "artist": _clean_text(str(row[4])) if isinstance(row[4], str) else None,
                "version": version,
                # 상태플래그 bit0 == 0 → tt2(firebrick)로 렌더링되는 AC 삭제곡
                "in_ac": bool(
                    act_row and isinstance(act_row[0], int) and act_row[0] & 1
                ),
                "charts": self._parse_charts(act_row),
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
        """actbl 행에서 AC에 수록된 채보(옵션 &4, 1<=레벨<=12)만 추출한다.

        AC 미수록 채보는 페이로드에서 빠지고, sync_song_master RPC가
        빠진 채보를 in_ac=false 처리한다 (삭제하지 않음).
        """
        if not act_row:
            return []
        charts: list[dict] = []
        for level_idx, opt_idx, style, diff in SLOTS:
            if opt_idx >= len(act_row):
                continue
            level, opt = act_row[level_idx], act_row[opt_idx]
            if not isinstance(level, int) or not isinstance(opt, int):
                continue
            if not (opt & _OPT_IN_AC) or not (1 <= level <= 12):
                continue
            charts.append({"play_style": style, "difficulty": diff, "level": level})
        return charts
