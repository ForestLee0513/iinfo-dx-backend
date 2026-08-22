"""곡 마스터 채보(iidx.charts) 집계 조회.

클리어 램프 비율을 "사용자 CSV에 들어있는 채보"가 아니라 "게임에 존재하는
전체 채보"를 모수로 계산하기 위해, 스타일/레벨별 현역(in_ac) 채보 수를 센다.
값은 주 1회 곡 마스터 크롤 때만 바뀌므로 프로세스 내 TTL 캐시를 둔다.
"""

import time

from app.db.session import get_supabase_iidx

_LEVELS = range(1, 13)
_CACHE_TTL = 3600  # 초
_cache: dict[str, tuple[float, dict[int, int]]] = {}


def count_charts_by_level(play_style: str, *, use_cache: bool = True) -> dict[int, int]:
    """스타일별 {레벨: 현역 채보 수}를 반환한다 (채보가 0개인 레벨은 생략)."""
    now = time.monotonic()
    if use_cache:
        cached = _cache.get(play_style)
        if cached and now - cached[0] < _CACHE_TTL:
            return dict(cached[1])

    db = get_supabase_iidx()
    totals: dict[int, int] = {}
    for level in _LEVELS:
        result = (
            db.table("charts")
            .select("id", count="exact")
            .eq("play_style", play_style)
            .eq("level", level)
            .eq("in_ac", True)
            .limit(1)
            .execute()
        )
        count = result.count or 0
        if count:
            totals[level] = count

    _cache[play_style] = (now, dict(totals))
    return totals
