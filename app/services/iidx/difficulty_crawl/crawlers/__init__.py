from app.services.iidx.difficulty_crawl.crawlers.base import (
    CRAWLER_REGISTRY,
    Crawler,
    TableDef,
    TableResult,
    get_crawler,
)

# import 시점에 @register 데코레이터가 실행되어 레지스트리에 등록된다
from app.services.iidx.difficulty_crawl.crawlers import numeric_example, sheet_5ch  # noqa: E402, F401

__all__ = [
    "CRAWLER_REGISTRY",
    "Crawler",
    "TableDef",
    "TableResult",
    "get_crawler",
]
