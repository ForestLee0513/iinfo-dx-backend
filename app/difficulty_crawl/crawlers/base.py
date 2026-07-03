"""크롤러 공통 타입 + 레지스트리.

새 난이도표가 생기면:
  1) Crawler 하나 구현하고 @register("이름") 붙이기
  2) TABLE_CRAWL_TARGETS에 {"crawler": "이름", ...설정} 추가
  → 스키마/동기화 코드는 손댈 필요 없음
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

import httpx

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}


@dataclass
class TableDef:
    """difficulty_tables 한 행에 대응하는 표 정의."""

    slug: str
    name: str
    source: str
    play_style: Literal["SP", "DP"]
    rating_type: Literal["GRADE", "NUMERIC"]
    level: int | None = None
    grades: list[str] | None = None  # GRADE 방식일 때만 필수 (서열 순서)

    def __post_init__(self):
        if self.rating_type == "GRADE" and not self.grades:
            raise ValueError(f"{self.slug}: GRADE 표는 grades 배열이 필요합니다")
        if self.rating_type == "NUMERIC" and self.grades:
            raise ValueError(f"{self.slug}: NUMERIC 표는 grades를 갖지 않습니다")


@dataclass
class TableResult:
    """크롤러 출력 단위: 표 정의 + 그 표의 엔트리 목록.

    entries 항목 형식 (sync_table_result RPC 입력과 동일):
      GRADE  : {"title","series","play_style","difficulty","level","grade"}
      NUMERIC: {"title","series","play_style","difficulty","level","rating"}
    """

    table: TableDef
    entries: list[dict] = field(default_factory=list)


class Crawler(Protocol):
    """모든 크롤러가 구현해야 하는 인터페이스."""

    async def crawl(self, client: httpx.AsyncClient, target: dict) -> list[TableResult]:
        ...


CRAWLER_REGISTRY: dict[str, Crawler] = {}


def register(name: str):
    """크롤러 등록 데코레이터. TABLE_CRAWL_TARGETS의 'crawler' 값으로 선택됨."""

    def deco(cls):
        CRAWLER_REGISTRY[name] = cls()
        cls.crawler_name = name
        return cls

    return deco


def get_crawler(name: str) -> Crawler:
    if name not in CRAWLER_REGISTRY:
        raise KeyError(
            f"등록되지 않은 크롤러: {name} (사용 가능: {list(CRAWLER_REGISTRY)})"
        )
    return CRAWLER_REGISTRY[name]
