"""Date normalisation utilities for due-date text → concrete date."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional


_WEEKDAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_RELATIVE_MAP = {
    "today": 0,
    "tomorrow": 1,
    "eod": 0,
    "end of day": 0,
    "end of week": None,  # handled specially
    "end of month": None,  # handled specially
}


def normalize_due_date(due_text: str, reference_date: Optional[date] = None) -> Optional[date]:
    """Convert a natural-language due string into a concrete date.

    Parameters
    ----------
    due_text:
        Raw text extracted from transcript (e.g. "by next Friday", "in 2 weeks").
    reference_date:
        The date the meeting occurred on. Defaults to today if omitted.

    Returns
    -------
    Concrete :class:`datetime.date` or *None* when the string is empty / unparseable.
    """
    if not due_text or not due_text.strip():
        return None

    ref = reference_date or date.today()
    text = due_text.strip().lower()

    # -- simple relative --------------------------------------------------
    if "today" in text or "eod" in text or "end of day" in text:
        return ref

    if "tomorrow" in text:
        return ref + timedelta(days=1)

    # -- "end of week" → coming Sunday ------------------------------------
    if "end of week" in text or "eow" in text:
        days_ahead = 6 - ref.weekday()  # Sunday = 6
        if days_ahead <= 0:
            days_ahead += 7
        return ref + timedelta(days=days_ahead)

    # -- "end of month" ---------------------------------------------------
    if "end of month" in text or "eom" in text:
        # last day of the current month
        if ref.month == 12:
            return ref.replace(year=ref.year + 1, month=1, day=1) - timedelta(days=1)
        return ref.replace(month=ref.month + 1, day=1) - timedelta(days=1)

    # -- "in N days / weeks / months" ------------------------------------
    in_days_match = re.search(r"in\s+(\d+)\s+day", text)
    if in_days_match:
        return ref + timedelta(days=int(in_days_match.group(1)))

    in_weeks_match = re.search(r"in\s+(\d+)\s+week", text)
    if in_weeks_match:
        return ref + timedelta(weeks=int(in_weeks_match.group(1)))

    in_months_match = re.search(r"in\s+(\d+)\s+month", text)
    if in_months_match:
        # rough approximation: 30 days per month
        return ref + timedelta(days=30 * int(in_months_match.group(1)))

    # -- "next <weekday>" / "this <weekday>" / "<weekday>" ---------------
    for day_name, weekday_num in _WEEKDAY_MAP.items():
        if day_name in text:
            is_next = "next" in text
            days_ahead = weekday_num - ref.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            if is_next and days_ahead <= 7:
                days_ahead += 7
            return ref + timedelta(days=days_ahead)

    # -- ISO / US date format: YYYY-MM-DD or MM/DD/YYYY ------------------
    iso_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            pass

    us_match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if us_match:
        try:
            return date(int(us_match.group(3)), int(us_match.group(1)), int(us_match.group(2)))
        except ValueError:
            pass

    # -- "N weeks" (without "in") ----------------------------------------
    weeks_match = re.search(r"(\d+)\s+week", text)
    if weeks_match:
        return ref + timedelta(weeks=int(weeks_match.group(1)))

    return None
