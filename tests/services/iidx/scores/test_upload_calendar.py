from datetime import date

from app.services.iidx.scores.upload_calendar import build_upload_calendar


def test_upload_calendar_includes_play_style_for_each_upload_in_local_date():
    result = build_upload_calendar(
        [
            {"uploaded_at": "2025-12-31T15:30:00+00:00", "play_style": "SP"},
            {"uploaded_at": "2026-01-01T01:00:00+00:00", "play_style": "DP"},
            {"uploaded_at": "2026-01-01T16:00:00+00:00", "play_style": "SP"},
        ],
        style=None,
        tz="Asia/Seoul",
        since=date(2026, 1, 1),
        until=date(2026, 1, 3),
    )

    assert result.total == 3
    assert result.days == {
        date(2026, 1, 1): 2,
        date(2026, 1, 2): 1,
        date(2026, 1, 3): 0,
    }
    assert [item.play_style for item in result.uploads[date(2026, 1, 1)]] == ["SP", "DP"]
    assert [item.play_style for item in result.uploads[date(2026, 1, 2)]] == ["SP"]
    assert result.uploads[date(2026, 1, 3)] == []
