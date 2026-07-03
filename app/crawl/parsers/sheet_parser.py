"""IIDX Google Sheets pubhtml 파서.

파싱 좌표 기준:
  시리즈: 시리즈헤더행(y=0)의 x=1부터 x축으로 증가
  난이도: 데이터행의 x=0 (y=1부터 y축으로 증가)
  곡명:   각 데이터행의 x=1~ (셀 하나에 <br>로 여러 곡)

개인차 판별 우선순위:
  1. 텍스트 노드를 감싼 <span style="color:#ff0000;">
  2. td 클래스의 CSS color가 #ff0000 (style 태그에서 추출)
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup, NavigableString, Tag

logger = logging.getLogger(__name__)

# 전각 → 반각 등급 정규화
GRADE_NORM = {
    "S＋": "S+", "A＋": "A+", "B＋": "B+",
    "C＋": "C+", "D＋": "D+", "E＋": "E+",
}
# 등급 헤더/설명행 스킵
SKIP_TEXTS = {""}

# 시리즈 헤더행 마커. 곡 수(예: 650, 652)는 곡이 추가되면 변동되므로
# 값을 고정하지 않고 "NNN譜面" 형태를 패턴으로 매칭한다.
SERIES_HEADER_RE = re.compile(r"^[\d,]+\s*譜面$")

# [H] or [L] suffix 패턴
DIFF_RE = re.compile(r"\[([HL])\]")


def extract_class_colors(soup: BeautifulSoup) -> dict[str, str]:
    """
    <style> 블록에서 .waffle .sXX { ... color:#xxxxxx ... } 를 파싱해
    { 'sXX': '#xxxxxx' } 딕셔너리 반환.
    """
    class_color: dict[str, str] = {}
    style_tag = soup.find("style")
    if not style_tag:
        return class_color
    css = style_tag.get_text()
    for m in re.finditer(r"\.waffle\s+\.(\w+)\{([^}]*?)\}", css):
        cls, rules = m.group(1), m.group(2)
        cm = re.search(r"(?<![a-z-])color:([#\w]+)", rules)
        if cm:
            class_color[cls] = cm.group(1).lower()
    return class_color


def iter_lines(td: Tag, class_color: dict[str, str]) -> list[tuple[str, bool]]:
    """
    td 안의 내용을 <br> 기준으로 줄 단위로 분리.
    각 줄이 red(개인차)인지 여부를 함께 반환.

    td 기본색 판별 우선순위:
      1. td 인라인 style의 color (#ff0000 → red)
         — 셀에 직접 style="...color:#ff0000;..." 이 잡힌 경우
      2. td CSS 클래스 색상 (class_color 딕셔너리) → fallback

    텍스트 노드별 판별 우선순위:
      1. 텍스트를 감싼 <span style="color:#ff0000;"> → PERSONAL
      2. 텍스트를 감싼 <span style="color:#000000;"> 등 다른 명시적 색상 → STRENGTH
         — td가 red여도 span으로 #000 오버라이드 시 STRENGTH로 처리
      3. span 없으면 td 기본색 따름
    """
    # ── td 기본색: 인라인 style 우선, 없으면 CSS 클래스
    inline_style = td.get("style", "").replace(" ", "").lower()
    if "color:#ff0000" in inline_style:
        td_base_red = True
    else:
        td_classes = td.get("class", [])
        td_base_red = any(class_color.get(c, "") == "#ff0000" for c in td_classes)

    def span_color(span: Tag) -> str | None:
        """
        span의 최종 색상을 반환. 인라인 style 우선, 없으면 CSS 클래스.
        색상 정보가 없으면 None 반환.
        """
        # 1) 인라인 style 우선
        style = span.get("style", "").replace(" ", "")
        m = re.search(r"color:(#[0-9a-fA-F]{6})", style)
        if m:
            return m.group(1).lower()
        # 2) CSS 클래스 fallback (예: <span class="s15">)
        for cls in span.get("class", []):
            if cls in class_color:
                return class_color[cls]
        return None

    def node_is_red(node: NavigableString) -> bool:
        for parent in node.parents:
            if parent is td:
                return td_base_red
            if parent.name == "span":
                color = span_color(parent)
                if color == "#ff0000":
                    return True
                if color is not None:  # #000000 등 다른 명시적 색상 → STRENGTH
                    return False
        return td_base_red

    results: list[tuple[str, bool]] = []
    buf_parts: list[tuple[str, bool]] = []  # (text_piece, is_red)

    def flush() -> None:
        text = "".join(p[0] for p in buf_parts).strip()
        is_red = any(p[1] for p in buf_parts) if buf_parts else td_base_red
        buf_parts.clear()
        if text:
            results.append((text, is_red))

    for node in td.descendants:
        if isinstance(node, Tag):
            if node.name == "br":
                flush()
        elif isinstance(node, NavigableString):
            if node.parent.name == "br":
                continue
            buf_parts.append((str(node), node_is_red(node)))

    flush()
    return results


def parse_title_diff(text: str) -> tuple[str, str]:
    """
    'gigadelic[H]' → ('gigadelic', 'HYPER')
    'snow storm[L]' → ('snow storm', 'LEGGENDARIA')
    'Astra Blaze'   → ('Astra Blaze', 'ANOTHER')
    """
    m = DIFF_RE.search(text)
    if m:
        diff = "HYPER" if m.group(1) == "H" else "LEGGENDARIA"
        return text[: m.start()].strip(), diff
    return text.strip(), "ANOTHER"


def parse_sheet(html: str, level: int) -> dict[str, dict[str, list]]:
    """
    HTML을 파싱해 아래 구조로 반환:
    {
      "s_plus": { "strength": [...], "personal": [...] },
      "s":      { ... },
      ...
    }

    좌표 기준:
      - y=0 (시리즈 헤더행): x=0 스킵, x=1~ → series_map[x] = 시리즈명
      - y=1~ (데이터행):     x=0 = 난이도 등급, x=1~ = 곡명 셀
    """
    soup = BeautifulSoup(html, "html.parser")
    class_color = extract_class_colors(soup)

    table = soup.find("table")
    if not table:
        return {}

    all_rows = table.find_all("tr")

    # ── 시리즈 헤더행 탐색: 첫 td가 "NNN譜面"(곡 수, 변동 가능)인 행
    series_map: dict[int, str] = {}
    data_rows: list[Tag] = []
    series_row_found = False

    for row in all_rows:
        tds = row.find_all("td")
        if not tds:
            continue
        first_text = tds[0].get_text(strip=True)

        if SERIES_HEADER_RE.match(first_text):
            series_row_found = True
            # x=1~ → 시리즈명 (헤더 셀 자신인 "NNN譜面"은 제외)
            for x, td in enumerate(tds):
                if x == 0:
                    continue
                t = td.get_text(strip=True)
                if t and not SERIES_HEADER_RE.match(t):
                    series_map[x] = t
        elif series_row_found:
            data_rows.append(row)

    if not series_map:
        logger.warning("시리즈 헤더행을 찾지 못했습니다.")
        return {}

    # ── 데이터행 파싱
    result: dict[str, dict[str, list]] = {}

    for row in data_rows:
        tds = row.find_all("td")
        if not tds:
            continue

        # x=0: 난이도(등급)
        grade_raw = tds[0].get_text(strip=True)
        grade = GRADE_NORM.get(grade_raw, grade_raw)
        if not grade or grade in SKIP_TEXTS or SERIES_HEADER_RE.match(grade):
            continue

        gkey = grade.lower().replace("+", "_plus")
        if gkey not in result:
            result[gkey] = {"strength": [], "personal": []}

        # x=1~: 곡명 셀
        for x, td in enumerate(tds[1:], start=1):
            series = series_map.get(x)
            if not series:
                continue

            for text, is_red in iter_lines(td, class_color):
                title, diff = parse_title_diff(text)
                if not title:
                    continue

                table_type = "PERSONAL" if is_red else "STRENGTH"
                entry = {
                    "title": title,
                    "difficulty": {
                        "in_game": diff,
                        "table": grade,
                        "table_type": table_type,
                    },
                    "series": series,
                    "level": level,
                }
                result[gkey][table_type.lower()].append(entry)

    return result
