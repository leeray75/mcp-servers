"""Date argument parsing and time formatting.

ESPN groups scoreboard events into days using US Eastern time (a 10 pm ET start still
belongs to that ET date although it is the next day in UTC). The server resolves
``today``/``yesterday``/``tomorrow`` in ``DEFAULT_TIMEZONE`` and passes the resulting
calendar date to ESPN unchanged; for the default zone the two day boundaries coincide.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sports_mcp.errors import InvalidInputError

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$", re.ASCII)
_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$", re.ASCII)
DATE_HELP = "Use today, yesterday, tomorrow, YYYY-MM-DD or YYYYMMDD."


def resolve_date(value: str, tz: ZoneInfo, now: datetime | None = None) -> date:
    """Turn the ``date`` tool argument into a calendar date in ``tz``."""
    text = value.strip().lower() if isinstance(value, str) else ""
    today = (now or datetime.now(UTC)).astimezone(tz).date()
    if text == "today":
        return today
    if text == "yesterday":
        return today - timedelta(days=1)
    if text == "tomorrow":
        return today + timedelta(days=1)
    match = _ISO.fullmatch(text) or _COMPACT.fullmatch(text)
    if match is None:
        raise InvalidInputError(f"Invalid date. {DATE_HELP}")
    try:
        parsed = date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        raise InvalidInputError(f"Invalid date. {DATE_HELP}") from None
    if not 1990 <= parsed.year <= 2100:
        raise InvalidInputError("Date out of range (1990-2100).")
    return parsed


def parse_espn_time(value: object) -> datetime | None:
    """Parse ESPN timestamps such as ``2026-10-04T17:00Z`` or ``2026-10-04T17:00:00Z``."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_iso(moment: datetime, tz: ZoneInfo) -> str:
    return moment.astimezone(tz).strftime("%Y-%m-%dT%H:%M:%S%z")
