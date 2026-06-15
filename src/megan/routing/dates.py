"""Best-effort natural-language due-date parsing for Linear.

Handles the common quick answers ('today', 'this week', 'Fri', ISO dates).
Returns an ISO `YYYY-MM-DD` string or None.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}


def parse_due(text: str | None, today: date | None = None) -> str | None:
    if not text:
        return None
    today = today or date.today()
    t = text.strip().lower()

    if t in ("", "none", "no date", "whenever", "someday"):
        return None
    if t == "today":
        return today.isoformat()
    if t == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if t in ("this week", "end of week", "eow"):
        # Friday of the current week.
        return (today + timedelta(days=(4 - today.weekday()) % 7)).isoformat()
    if t in ("next week",):
        return (today + timedelta(days=7 - today.weekday())).isoformat()

    if t in _WEEKDAYS:
        target = _WEEKDAYS[t]
        delta = (target - today.weekday()) % 7
        delta = delta or 7  # next occurrence, not today
        return (today + timedelta(days=delta)).isoformat()

    # ISO-ish.
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d"):
        try:
            parsed = datetime.strptime(t, fmt).date()
            if fmt == "%m/%d":
                parsed = parsed.replace(year=today.year)
            return parsed.isoformat()
        except ValueError:
            continue

    try:
        from dateutil import parser as dateutil_parser

        return dateutil_parser.parse(t, default=datetime(today.year, today.month, today.day)).date().isoformat()
    except Exception:  # noqa: BLE001
        return None
