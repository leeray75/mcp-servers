from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from sports_mcp.dates import local_iso, parse_espn_time, resolve_date, utc_iso
from sports_mcp.errors import InvalidInputError

NY = ZoneInfo("America/New_York")
LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 4, 19, 0, tzinfo=UTC)


def test_relative_words() -> None:
    assert resolve_date("today", NY, NOW) == date(2026, 10, 4)
    assert resolve_date("Yesterday", NY, NOW) == date(2026, 10, 3)
    assert resolve_date(" tomorrow ", NY, NOW) == date(2026, 10, 5)


@pytest.mark.parametrize("text", ["2026-10-04", "20261004"])
def test_absolute_formats(text: str) -> None:
    assert resolve_date(text, NY, NOW) == date(2026, 10, 4)


def test_today_depends_on_the_configured_timezone() -> None:
    late_utc = datetime(2026, 10, 5, 2, 30, tzinfo=UTC)  # 10:30 pm Oct 4 in New York
    assert resolve_date("today", NY, late_utc) == date(2026, 10, 4)
    assert resolve_date("today", ZoneInfo("UTC"), late_utc) == date(2026, 10, 5)
    assert resolve_date("today", ZoneInfo("Asia/Tokyo"), late_utc) == date(2026, 10, 5)


def test_dst_edges_do_not_skip_or_repeat_days() -> None:
    # US spring-forward (2026-03-08) and fall-back (2026-11-01).
    spring = datetime(2026, 3, 8, 6, 59, tzinfo=UTC)  # 01:59 EST, an hour before the jump
    assert resolve_date("today", NY, spring) == date(2026, 3, 8)
    assert resolve_date("tomorrow", NY, spring) == date(2026, 3, 9)
    after = datetime(2026, 3, 8, 7, 1, tzinfo=UTC)  # 03:01 EDT
    assert resolve_date("today", NY, after) == date(2026, 3, 8)
    fall = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 01:30 EDT (first pass)
    assert resolve_date("today", NY, fall) == date(2026, 11, 1)
    fall2 = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # 01:30 EST (second pass)
    assert resolve_date("yesterday", NY, fall2) == date(2026, 10, 31)
    assert resolve_date("today", LA, datetime(2026, 11, 1, 8, 30, tzinfo=UTC)) == date(2026, 11, 1)


@pytest.mark.parametrize(
    "bad",
    ["", "now", "2026-13-01", "2026-02-30", "26-10-04", "2026/10/04", "2026-10-04x", "1850-01-01",
     "2200-01-01", "today\nx", "../x", "2026-10-04;"],
)  # fmt: skip
def test_invalid_dates(bad: str) -> None:
    with pytest.raises(InvalidInputError):
        resolve_date(bad, NY, NOW)


def test_non_string_date() -> None:
    with pytest.raises(InvalidInputError):
        resolve_date(None, NY, NOW)  # type: ignore[arg-type]


def test_default_now_is_used() -> None:
    assert isinstance(resolve_date("today", NY), date)


def test_parse_espn_time_formats() -> None:
    assert parse_espn_time("2026-10-04T17:00Z") == datetime(2026, 10, 4, 17, 0, tzinfo=UTC)
    assert parse_espn_time("2026-10-04T17:00:30Z") == datetime(2026, 10, 4, 17, 0, 30, tzinfo=UTC)
    assert parse_espn_time("2026-10-04T17:00:00") == datetime(2026, 10, 4, 17, 0, tzinfo=UTC)
    assert parse_espn_time("2026-10-04T13:00:00-04:00") == datetime(2026, 10, 4, 17, 0, tzinfo=UTC)
    assert parse_espn_time("garbage") is None
    assert parse_espn_time(None) is None
    assert parse_espn_time("") is None


def test_formatters() -> None:
    moment = datetime(2026, 10, 4, 17, 0, tzinfo=UTC)
    assert utc_iso(moment) == "2026-10-04T17:00:00Z"
    assert local_iso(moment, NY) == "2026-10-04T13:00:00-0400"
