from datetime import date

from megan.routing.dates import parse_due


def test_today_and_tomorrow():
    today = date(2026, 6, 15)  # a Monday
    assert parse_due("today", today) == "2026-06-15"
    assert parse_due("tomorrow", today) == "2026-06-16"


def test_this_week_is_friday():
    today = date(2026, 6, 15)  # Monday
    assert parse_due("this week", today) == "2026-06-19"  # Friday


def test_weekday_picks_next_occurrence():
    today = date(2026, 6, 15)  # Monday
    # "mon" should be next Monday, not today.
    assert parse_due("mon", today) == "2026-06-22"
    assert parse_due("fri", today) == "2026-06-19"


def test_iso_passthrough():
    assert parse_due("2026-07-01") == "2026-07-01"


def test_none_values():
    assert parse_due("") is None
    assert parse_due("no date") is None
    assert parse_due(None) is None
