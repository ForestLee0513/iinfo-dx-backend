"""클리어 현황(램프 비율) 요약 집계.

user_chart_scores.clear_type(eagate 원문 그대로 저장된 문자열)을 8개 표준
클리어 램프로 정규화해 개수/비율을 계산한다.

모수(total)는 사용자가 올린 CSV 행 수가 아니라 **곡 마스터 기준 해당
스타일/레벨의 현역 채보 수**(iidx.charts, in_ac=true)다. 즉 CSV에 아예
없는 채보도 NO PLAY로 잡히므로, 비율은 "게임 전체 대비 클리어 현황"을
뜻한다. no_play = 전체 채보 수 - 램프가 찍힌 행 수.
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


def normalize_lamp(clear_type: str | None) -> str:
    if clear_type is None:
        return "no_play"
    key = " ".join(clear_type.strip().upper().split())
    return _RAW_TO_LAMP.get(key, "no_play")


def build_score_summary(
    rows: list[dict],
    *,
    play_style: str,
    level: int | None,
    chart_totals: dict[int, int],
) -> ScoreSummaryResponse:
    """(level, clear_type) 행 목록 + 레벨별 전체 채보 수에서 요약 응답을 만든다.

    level이 주어지면 해당 레벨만, 생략하면 스타일 전체를 집계한다.
    available_levels는 사용자 성적이 아니라 곡 마스터에 채보가 존재하는
    레벨 목록(선택 UI용)이다.
    """
    available_levels = sorted(chart_totals)

    filtered = rows if level is None else [r for r in rows if r.get("level") == level]

    counts = {k: 0 for k in LAMP_KEYS}
    for r in filtered:
        lamp = normalize_lamp(r.get("clear_type"))
        counts[lamp] += 1

    # 모수 = 곡 마스터의 현역 채보 수. 타이틀/레벨 불일치로 CSV 행이 더 많은
    # 경우(마스터에 없는 채보 등)를 대비해 실제 집계 행 수로 하한을 둔다.
    master_total = chart_totals.get(level, 0) if level is not None else sum(chart_totals.values())
    total = max(master_total, len(filtered))
    # CSV에 없는 채보 + clear_type이 비어 있는 행을 모두 NO PLAY로 합친다.
    counts["no_play"] = total - sum(counts[k] for k in LAMP_KEYS if k != "no_play")

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
