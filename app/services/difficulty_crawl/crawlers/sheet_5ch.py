"""5ch Google Sheets pubhtml 크롤러 (GRADE 방식)."""

import httpx

from app.core.config import settings
from app.services.difficulty_crawl.crawlers.base import DEFAULT_HEADERS, TableDef, TableResult, register
from app.services.difficulty_crawl.parsers.sheet_parser import parse_sheet

GRADES_5CH = ["F", "E", "D", "C", "B", "B+", "A", "A+", "S", "S+"]


@register("5ch_sheet")
class Sheet5chCrawler:
    """
    Google Sheets pubhtml 기반 5ch 표.
    시트 하나에서 지력/개인차 곡이 섞여 나오지만, 표를 나누지 않고
    하나의 표(TableResult)로 합친다. 각 곡 엔트리의 table_type으로
    지력(STRENGTH)/개인차(PERSONAL)를 구분한다.

    target 예시:
      {"crawler": "5ch_sheet",
       "url": "https://docs.google.com/.../pubhtml",
       "play_style": "SP", "level": 12}
    선택 키:
      "name": 표 이름 지정 (미지정 시 "5ch {style}☆{level}")
      "slug": 표 slug 지정 (미지정 시 "5ch-{style}{level}")
    """

    async def crawl(self, client: httpx.AsyncClient, target: dict) -> list[TableResult]:
        style, level = target["play_style"], target["level"]

        resp = await client.get(
            target["url"],
            headers=DEFAULT_HEADERS,
            timeout=settings.REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        # {grade: [ {title, series, difficulty, level, grade, table_type}, ... ]}
        parsed = parse_sheet(resp.text, level)

        result = TableResult(TableDef(
            slug=target.get("slug") or f"5ch-{style.lower()}{level}",
            name=target.get("name") or f"5ch {style}☆{level}",
            source="5ch", play_style=style, level=level,
            rating_type="GRADE", grades=GRADES_5CH,
        ))

        # 티어(등급) 순서대로 순회하며 하나의 엔트리 목록으로 합침
        for grade in GRADES_5CH:
            for item in parsed.get(grade, []):
                result.entries.append({
                    "title": item["title"],
                    "series": item.get("series"),
                    "play_style": style,
                    "difficulty": item["difficulty"],
                    "level": item["level"],
                    "grade": item["grade"],
                    "table_type": item["table_type"],
                })

        return [result]
