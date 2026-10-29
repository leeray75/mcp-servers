"""R25-R27 and R26 time arguments: snowflake validation, timestamps and datetime conversion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from discord_mcp.errors import DiscordMcpError
from discord_mcp.snowflake import (
    DISCORD_EPOCH_MS,
    datetime_to_snowflake,
    discord_timestamp,
    is_snowflake,
    parse_time_argument,
    snowflake_created_at,
    time_argument_to_datetime,
    time_argument_to_snowflake,
    to_iso,
    validate_snowflake,
)

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("123456789012345", True),  # 15 digits
        ("123456789012345678901", False),  # 21 digits but 1.2e20 > 2**64
        ("12345678901234567", True),
        ("12345678901234", False),  # 14 digits
        ("1234567890123456789012", False),  # 22 digits
        ("12345678901234567a", False),
        ("12345678901234567\n", False),
        ("", False),
        (12345678901234567, False),  # numbers are never accepted (R25)
    ],
)
def test_snowflake_pattern_and_range(value: object, expected: bool) -> None:
    assert is_snowflake(value) is expected


def test_validate_snowflake_names_the_field_only() -> None:
    assert validate_snowflake("123456789012345678", "channel_id") == "123456789012345678"
    with pytest.raises(DiscordMcpError) as info:
        validate_snowflake("nope", "channel_id")
    assert info.value.kind == "validation"
    assert "channel_id" in info.value.message
    assert "nope" not in info.value.message


def test_created_at_is_derived_from_the_snowflake() -> None:
    snowflake = str(((1_700_000_000_000 - DISCORD_EPOCH_MS) << 22) | 12345)
    assert to_iso(snowflake_created_at(snowflake)) == "2023-11-14T22:13:20.000Z"


def test_to_iso_requires_aware_datetimes() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_iso(datetime(2026, 1, 1))
    assert to_iso(datetime(2026, 1, 1, 1, tzinfo=timezone(timedelta(hours=1)))) == (
        "2026-01-01T00:00:00.000Z"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-19T08:30:00.000000+00:00", "2026-10-19T08:30:00.000Z"),
        ("2026-10-19T08:30:00Z", "2026-10-19T08:30:00.000Z"),
        ("2026-10-19T08:30:00", "2026-10-19T08:30:00.000Z"),
        ("not a time", None),
        ("", None),
        (None, None),
        (5, None),
    ],
)
def test_discord_timestamp_normalises(raw: object, expected: str | None) -> None:
    assert discord_timestamp(raw) == expected


def test_datetime_becomes_synthetic_snowflake() -> None:
    moment = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    ms = int(moment.timestamp() * 1000)
    assert datetime_to_snowflake(moment, NOW, "before") == str((ms - DISCORD_EPOCH_MS) << 22)


def test_datetime_before_epoch_or_future_or_naive_is_rejected() -> None:
    with pytest.raises(DiscordMcpError, match="before the Discord epoch"):
        datetime_to_snowflake(datetime(2014, 1, 1, tzinfo=UTC), NOW, "before")
    with pytest.raises(DiscordMcpError, match="in the future"):
        datetime_to_snowflake(NOW + timedelta(seconds=1), NOW, "after")
    with pytest.raises(DiscordMcpError, match="offset or Z"):
        datetime_to_snowflake(datetime(2026, 1, 1), NOW, "after")


@pytest.mark.parametrize(
    "value",
    ["2026-10-27T12:00:00", "2026-10-27", "yesterday", "2026-13-40T00:00:00Z"],
)
def test_time_arguments_reject_naive_and_malformed(value: str) -> None:
    with pytest.raises(DiscordMcpError) as info:
        parse_time_argument(value, "before")
    assert info.value.kind == "validation"
    assert "before" in info.value.message


def test_time_argument_snowflake_passes_through_unchanged() -> None:
    assert time_argument_to_snowflake("123456789012345678", "after", NOW) == "123456789012345678"
    assert time_argument_to_datetime("123456789012345678", "after", NOW).year >= 2015


def test_time_argument_iso_with_offset_converts() -> None:
    value = "2026-10-27T14:00:00+02:00"
    expected = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert time_argument_to_datetime(value, "before", NOW) == expected
    assert time_argument_to_snowflake(value, "before", NOW) == datetime_to_snowflake(
        expected, NOW, "before"
    )


def test_time_argument_datetime_bounds_are_checked() -> None:
    with pytest.raises(DiscordMcpError, match="in the future"):
        time_argument_to_datetime("2026-11-01T00:00:00Z", "before", NOW)
    with pytest.raises(DiscordMcpError, match="before the Discord epoch"):
        time_argument_to_datetime("2014-06-01T00:00:00Z", "before", NOW)
