"""5ch Google Sheets pubhtml 크롤러 (GRADE 방식)."""

import httpx

from app.core.config import settings
from app.difficulty_crawl.crawlers.base import DEFAULT_HEADERS, TableDef, TableResult, register
from app.difficulty_crawl.parsers.sheet_parser import parse_sheet

GRADES_5CH = ["F", "E", "D", "C", "B", "A", "S", "S+"]


@register("5ch_sheet")
class Sheet5chCrawler:
    """
    Google Sheets pubhtml 기반 5ch 표.
    시트 하나에서 지력표(STRENGTH)/개인차표(PERSONAL) 두 표가 나옴.

    target 예시:
      {"crawler": "5ch_sheet",
       "url": "https://docs.google.com/.../pubhtml",
       "play_style": "SP", "level": 12}
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
        parsed = parse_sheet(resp.text, level)  # {grade_key: {strength: [], personal: []}}

        results = {
            "strength": TableResult(TableDef(
                slug=f"5ch-{style.lower()}{level}-strength",
                name=f"5ch {style}☆{level} 지력표",
                source="5ch", play_style=style, level=level,
                rating_type="GRADE", grades=GRADES_5CH,
            )),
            "personal": TableResult(TableDef(
                slug=f"5ch-{style.lower()}{level}-personal",
                name=f"5ch {style}☆{level} 개인차표",
                source="5ch", play_style=style, level=level,
                rating_type="GRADE", grades=GRADES_5CH,
            )),
        }

        for groups in parsed.values():
            for ttype in ("strength", "personal"):
                for item in groups.get(ttype, []):
                    results[ttype].entries.append({
                        "title": item["title"],
                        "series": item.get("series"),
                        "play_style": style,
                        "difficulty": item["difficulty"]["in_game"],
                        "level": item["level"],
                        "grade": item["difficulty"]["table"],
                    })

        return list(results.values())
