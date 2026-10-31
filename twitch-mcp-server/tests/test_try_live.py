"""A1-D5: ``tools/try_live.py`` is opt-in, refuses without the exact word, and is unreachable from the suite.

The live smoke script is the only thing in this repository that is *allowed* to talk to the real
Twitch API, and ruling A1-D5 keeps it out of ``make check`` (acceptance A2/A3 stay UNVERIFIED until
the operator runs it). That ruling only holds if the refusal is real, so it is pinned here rather
than trusted: without ``TWITCH_LIVE_OK=yes`` the script returns before any client is constructed,
which the autouse network block in ``conftest.py`` would turn into a hard failure if it were ever
bypassed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "try_live.py"
GATE = "TWITCH_LIVE_OK"


def _load() -> Any:
    """Import the script by path: ``tools/`` is not a package and must not become one."""
    spec = importlib.util.spec_from_file_location("try_live_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None, f"{SCRIPT} is not importable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_lives_outside_the_collected_test_paths() -> None:
    """pytest collects ``tests/`` only (``testpaths``), so this file can never be run by the gate."""
    assert SCRIPT.is_file(), "the opt-in live smoke script must exist (A1-D5)"
    assert SCRIPT.parent.name == "tools"
    assert "tests" not in SCRIPT.parts


@pytest.mark.parametrize("gate", [None, "", "no", "YES", "1", "true"])
def test_refuses_without_the_exact_confirmation_word(
    monkeypatch: pytest.MonkeyPatch, gate: str | None
) -> None:
    """Anything but the literal ``yes`` is a refusal, and a refusal costs no network call."""
    module = _load()
    monkeypatch.delenv(GATE, raising=False)
    if gate is not None:
        monkeypatch.setenv(GATE, gate)
    assert module.main([]) == 2, f"{GATE}={gate!r} must not reach the real API"


def test_rejects_an_unknown_argument_before_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """No flag bypasses the gate: an unrecognised argument is a usage error, not a way through."""
    module = _load()
    monkeypatch.setenv(GATE, "yes")
    assert module.main(["--please-call-twitch"]) == 2


def test_the_only_accepted_argument_is_the_write_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--writes`` parses; with the gate absent it still refuses (the assertion above proves that)."""
    module = _load()
    monkeypatch.delenv(GATE, raising=False)
    assert module.main(["--writes"]) == 2, "the gate governs the optional write round-trip too"
