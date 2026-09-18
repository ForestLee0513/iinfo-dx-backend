from datetime import date

from app.services.iidx.scores.upload_calendar import build_score_update_calendar


def test_update_calendar_returns_only_years_with_upload_history_in_requested_timezone():
    result = build_score_update_calendar(
        [
            {
                "uploaded_at": "2025-12-31T15:30:00+00:00",
                "added_chart_count": 2,
                "updated_chart_count": 1,
            }
        ],
        style=None,
        tz="Asia/Seoul",
        since=date(2026, 1, 1),
        until=date(2026, 1, 1),
        year_rows=[
            {"uploaded_at": "2026-01-01T00:00:00+00:00"},
            {"uploaded_at": "2024-12-31T15:30:00+00:00"},
            {"uploaded_at": "2025-12-31T15:30:00+00:00"},
        ],
    )

    assert result.available_years == [2026, 2025]
    assert result.days[date(2026, 1, 1)].total == 3
