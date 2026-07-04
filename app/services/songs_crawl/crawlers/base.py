"""곡 마스터 크롤러 공통 타입과 레지스트리.

difficulty_crawl의 TableResult 계열과는 완전히 분리된 별도 레지스트리.
곡 마스터 크롤러는 SongMasterResult를 반환하며 sync_song_master RPC로 반영된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx


@dataclass
class SongMasterResult:
    """곡 마스터 크롤 결과.

    songs 항목 형식:
      { "tag", "title", "genre", "artist", "version",
        "charts": [ { "play_style", "difficulty", "level" }, ... ] }
    """

    source: str
    versions: list[dict] = field(default_factory=list)
    songs: list[dict] = field(default_factory=list)


class SongCrawler(Protocol):
    """곡 마스터 크롤러 인터페이스."""

    async def crawl(
        self, client: httpx.AsyncClient, target: dict
    ) -> list[SongMasterResult]:
        ...


SONG_CRAWLER_REGISTRY: dict[str, SongCrawler] = {}


def register(name: str):
    """곡 마스터 크롤러 등록 데코레이터. SONG_CRAWL_TARGETS의 'crawler' 값으로 선택."""

    def deco(cls):
        SONG_CRAWLER_REGISTRY[name] = cls()
        cls.crawler_name = name
        return cls

    return deco


def get_crawler(name: str) -> SongCrawler:
    if name not in SONG_CRAWLER_REGISTRY:
        raise KeyError(
            f"등록되지 않은 곡 마스터 크롤러: {name} "
            f"(사용 가능: {list(SONG_CRAWLER_REGISTRY)})"
        )
    return SONG_CRAWLER_REGISTRY[name]
