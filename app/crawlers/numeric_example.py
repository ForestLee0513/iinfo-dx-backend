"""숫자형 표 크롤러 템플릿 (NUMERIC 방식).

실제 대상 사이트의 응답 형식에 맞춰 _parse 부분만 구현하면 됨.
"""

import httpx

from app.core.config import settings
from app.crawlers.base import DEFAULT_HEADERS, TableDef, TableResult, register


@register("numeric_json")
class NumericJsonCrawler:
    """
    숫자 서열값(예: 12.3)을 쓰는 표용 템플릿.
    SP/DP가 한 소스에 같이 있으면 target에서 play_style별로 분리 정의.

    target 예시:
      {"crawler": "numeric_json",
       "url": "https://example.com/table.json",
       "source": "cpi",
       "slug": "cpi-sp12", "name": "CPI SP☆12",
       "play_style": "SP", "level": 12}
    """

    async def crawl(self, client: httpx.AsyncClient, target: dict) -> list[TableResult]:
        resp = await client.get(
            target["url"],
            headers=DEFAULT_HEADERS,
            timeout=settings.REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()

        result = TableResult(TableDef(
            slug=target["slug"],
            name=target["name"],
            source=target["source"],
            play_style=target["play_style"],
            level=target.get("level"),
            rating_type="NUMERIC",
        ))

        for row in self._parse(resp):  # ← 대상 형식에 맞게 구현
            result.entries.append({
                "title": row["title"],
                "series": row.get("series"),
                "play_style": target["play_style"],
                "difficulty": row["difficulty"],  # NORMAL/HYPER/ANOTHER/LEGGENDARIA
                "level": row.get("level", target.get("level")),
                "rating": float(row["rating"]),  # 예: 12.3
            })

        return [result]

    def _parse(self, resp: httpx.Response) -> list[dict]:
        # TODO: 실제 대상(JSON/HTML)에 맞게 구현
        # 예) JSON이면: return resp.json()["charts"]
        raise NotImplementedError
