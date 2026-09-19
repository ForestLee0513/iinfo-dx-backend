"""성적 스냅샷 간 변경 채보 수 계산."""

from dataclasses import dataclass

_UPDATE_VALUE_FIELDS = (
    "ex_score",
    "pgreat",
    "great",
    "clear_type",
    "dj_level",
    "play_count",
    "miss_count",
    "last_played_at",
)


@dataclass(frozen=True)
class ChartScoreChanges:
    added: int = 0
    updated: int = 0

    @property
    def total(self) -> int:
        return self.added + self.updated


def classify_chart_score_changes(previous: list[dict], current: list[dict]) -> ChartScoreChanges:
    """직전 스냅샷 대비 신규 채보와 기존 성적 갱신 채보 수를 분리한다.

    제목·난이도로 동일 채보를 식별한다. 새로 등장한 채보는 ``added``, 기존
    채보의 성적 필드 변경은 ``updated``로 센다.
    이번 CSV에 없는 이전 채보는 CSV 형식/수록곡 변화로 빠질 수 있어 갱신으로
    세지 않는다. 최초 업로드는 모든 채보를 신규 기록으로 센다.
    """
    if not previous:
        return ChartScoreChanges(added=len(current))
    previous_by_chart = {(row["title"], row["difficulty"]): row for row in previous}
    added = 0
    updated = 0
    for row in current:
        old = previous_by_chart.get((row["title"], row["difficulty"]))
        if old is None:
            added += 1
        elif any(row.get(field) != old.get(field) for field in _UPDATE_VALUE_FIELDS):
            updated += 1
    return ChartScoreChanges(added=added, updated=updated)
