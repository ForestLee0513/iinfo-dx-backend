"""업로드 기여도(컨트리뷰션) 그래프용 날짜별 집계.

score_uploads.uploaded_at(UTC)을 요청자가 지정한 IANA 타임존(tz) 기준
날짜로 변환해 날짜별 업로드 횟수를 센다. zoneinfo는 해당 타임존의
서머타임 규칙을 자동 반영하므로 일본/미국 등에서도 로컬 캘린더와 맞는
결과를 낸다. 업로드가 없는 날짜도 count=0으로 채워 FE가 별도
gap-filling 없이 캘린더를 그릴 수 있게 한다.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.schemas.iidx.scores import ScoreChangeCounts, ScoreUpdateCalendarResponse, UploadCalendarResponse


def build_upload_calendar(
    rows: list[dict],
    *,
    style: str | None,
    tz: str,
    since: date,
    until: date,
) -> UploadCalendarResponse:
    """(uploaded_at,) 행 목록에서 tz 기준 [since, until] 구간의 날짜별 업로드 횟수를 만든다."""
    zone = ZoneInfo(tz)

    counts: dict[date, int] = {}
    for r in rows:
        d = datetime.fromisoformat(r["uploaded_at"]).astimezone(zone).date()
        if since <= d <= until:
            counts[d] = counts.get(d, 0) + 1

    days: dict[date, int] = {}
    cur = since
    while cur <= until:
        days[cur] = counts.get(cur, 0)
        cur += timedelta(days=1)

    return UploadCalendarResponse(
        style=style,
        tz=tz,
        since=since,
        until=until,
        total=sum(counts.values()),
        days=days,
    )


def build_score_update_calendar(
    rows: list[dict],
    *,
    style: str | None,
    tz: str,
    since: date,
    until: date,
    year_rows: list[dict] | None = None,
) -> ScoreUpdateCalendarResponse:
    """(uploaded_at, added/updated_chart_count) 행을 날짜별 성적 변경 수로 합산한다."""
    zone = ZoneInfo(tz)
    counts: dict[date, ScoreChangeCounts] = {}
    for row in rows:
        d = datetime.fromisoformat(row["uploaded_at"]).astimezone(zone).date()
        if since <= d <= until:
            existing = counts.get(d, ScoreChangeCounts())
            added = existing.added + int(row.get("added_chart_count") or 0)
            updated = existing.updated + int(row.get("updated_chart_count") or 0)
            counts[d] = ScoreChangeCounts(added=added, updated=updated, total=added + updated)

    days: dict[date, ScoreChangeCounts] = {}
    cur = since
    while cur <= until:
        days[cur] = counts.get(cur, ScoreChangeCounts())
        cur += timedelta(days=1)

    added_total = sum(count.added for count in counts.values())
    updated_total = sum(count.updated for count in counts.values())
    available_years = sorted(
        {
            datetime.fromisoformat(row["uploaded_at"]).astimezone(zone).year
            for row in (year_rows if year_rows is not None else rows)
        },
        reverse=True,
    )

    return ScoreUpdateCalendarResponse(
        style=style,
        tz=tz,
        available_years=available_years,
        since=since,
        until=until,
        total=added_total + updated_total,
        added_total=added_total,
        updated_total=updated_total,
        days=days,
    )
