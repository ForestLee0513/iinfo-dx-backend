"""클리어 현황(램프 비율) 요약 집계.

user_chart_scores.clear_type(eagate 원문 그대로 저장된 문자열)을 8개 표준
클리어 램프로 정규화해 개수/비율을 계산한다. clear_type이 None인 행은
"해당 스타일/레벨 채보는 있지만 플레이 기록 없음"(NO PLAY)을 의미한다.
"""

from app.schemas.iidx.scores import (
    ClearLampCounts,
    ClearLampPercentages,
    ScoreSummaryResponse,
)

LAMP_KEYS = (
    "no_play",
    "failed",
    "assist_clear",
    "easy_clear",
    "clear",
    "hard_clear",
    "ex_hard_clear",
    "full_combo",
)

# eagate クリアタイプ 원문(대문자, 공백 정규화 후) → 표준 램프 키.
# 표기 변형(FULLCOMBO/FULL COMBO 등)을 모두 흡수한다.
_RAW_TO_LAMP = {
    "FAILED": "failed",
    "ASSIST CLEAR": "assist_clear",
    "EASY CLEAR": "easy_clear",
    "CLEAR": "clear",
    "HARD CLEAR": "hard_clear",
    "EX HARD CLEAR": "ex_hard_clear",
    "EXHARD CLEAR": "ex_hard_clear",
    "FULLCOMBO CLEAR": "full_combo",
    "FULL COMBO CLEAR": "full_combo",
}


def _normalize_lamp(clear_type: str | None) -> str:
    if clear_type is None:
        return "no_play"
    key = " ".join(clear_type.strip().upper().split())
    return _RAW_TO_LAMP.get(key, "no_play")


def build_score_summary(
    rows: list[dict],
    *,
    play_style: str,
    level: int | None,
) -> ScoreSummaryResponse:
    """(level, clear_type) 행 목록에서 요약 응답을 만든다.

    level이 주어지면 해당 레벨로 필터링 후 집계하고, available_levels는
    필터와 무관하게 rows 전체에서 뽑은 값(선택 UI용)을 반환한다.
    """
    available_levels = sorted({r["level"] for r in rows if r.get("level") is not None})

    filtered = rows if level is None else [r for r in rows if r.get("level") == level]

    counts = {k: 0 for k in LAMP_KEYS}
    for r in filtered:
        lamp = _normalize_lamp(r.get("clear_type"))
        counts[lamp] += 1

    total = len(filtered)
    percentages = {
        k: round(v / total * 100, 1) if total else 0.0 for k, v in counts.items()
    }

    return ScoreSummaryResponse(
        play_style=play_style,
        level=level,
        total=total,
        counts=ClearLampCounts(**counts),
        percentages=ClearLampPercentages(**percentages),
        available_levels=available_levels,
    )
