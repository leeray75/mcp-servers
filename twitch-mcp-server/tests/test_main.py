"""R1, R5, R7, R33, R52: the console entry point the container actually runs.

``python -m twitch_mcp`` is the whole of the Dockerfile's CMD, so a regression here means the
container either does not start, starts with the wrong bind, or leaks a credential through a
terminal. Nothing here needs a socket: ``uvicorn.run`` is replaced in-process, and
``create_app`` builds its collaborators without making a request (the boot probe belongs to the
lifespan, which a stubbed ``run`` never drives).
"""

from __future__ import annotations

from typing import Any

import pytest
import uvicorn

from tests.twitch_fakes import FAKE_CLIENT_SECRET, clear_env, set_env
from twitch_mcp.__main__ import LISTEN_PORT, USAGE, main


def _capture_run(calls: dict[str, Any]) -> Any:
    """Replace ``uvicorn.run`` so nothing listens, and record exactly how it was called."""

    def run(app: Any, **kwargs: Any) -> None:
        calls["app"] = app
        calls.update(kwargs)

    return run


@pytest.fixture(autouse=True)
def logged_levels(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub ``configure_logging`` for this module only, recording the level it was asked for.

    It installs handlers and redaction filters on the real root logger. Letting every test in this
    file run it would leave global logging mutated for the rest of the suite, which has nothing to
    do with the entry point under test. One test asserts the recorded level instead.
    """
    seen: list[str] = []
    monkeypatch.setattr("twitch_mcp.__main__.configure_logging", lambda level: seen.append(level))
    return seen


def test_stray_arguments_exit_2_and_start_nothing(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accidental ``twitch-mcp --help`` must not silently start a server."""

    def never(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a server must not start for a rejected invocation")

    monkeypatch.setattr(uvicorn, "run", never)

    assert main(["--help"]) == 2
    assert USAGE in capsys.readouterr().err


def test_no_arguments_is_the_only_accepted_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``argv`` is injectable purely so this path is testable without patching ``sys.argv``."""
    calls: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", _capture_run(calls))
    set_env(monkeypatch)

    assert main([]) == 0
    assert "app" in calls, "an argument-free start reaches uvicorn"


def test_a_bad_configuration_names_the_field_and_never_the_value(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R7: exit 2 with the offending field named — the secret's value cannot reach a terminal."""

    def never(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a server must not start on a broken configuration")

    monkeypatch.setattr(uvicorn, "run", never)
    set_env(monkeypatch, TWITCH_CLIENT_SECRET="replace-me-with-your-twitch-app-credential")

    assert main([]) == 2
    err = capsys.readouterr().err
    assert "configuration error" in err
    assert "twitch_client_secret" in err.lower(), "the field is named so the operator can fix it"
    assert FAKE_CLIENT_SECRET not in err, "a value that was configured never appears in the error"


def test_the_environment_is_read_only_through_config(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R6/R7 with an empty environment: the refusal is a field list, not a traceback."""
    calls: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", _capture_run(calls))
    clear_env(monkeypatch)

    assert main([]) == 2
    err = capsys.readouterr().err
    assert "configuration error" in err
    assert "Traceback" not in err, "a missing variable is a usage error, not a crash"


def test_the_app_is_handed_to_uvicorn_on_the_container_bind(
    monkeypatch: pytest.MonkeyPatch, logged_levels: list[str]
) -> None:
    """R1/R5/R52/R33: the exact uvicorn contract the container depends on.

    ``0.0.0.0`` here is the in-container address; compose publishes ``127.0.0.1:8770``. The flags
    are not cosmetics: trusting forwarded headers or emitting server/date headers, or letting
    uvicorn own logging, each breaks a stated invariant.
    """
    calls: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", _capture_run(calls))
    set_env(monkeypatch, LOG_LEVEL="DEBUG")

    assert main([]) == 0
    assert logged_levels == ["DEBUG"], "R52: the configured level reaches the logging setup"
    assert calls["host"] == "0.0.0.0", "in-container bind; loopback-only comes from compose (R1)"
    assert calls["port"] == LISTEN_PORT == 8000, "must match the container side of 8770:8000"
    assert calls["proxy_headers"] is False, (
        "loopback-only: a forged X-Forwarded-For is never trusted"
    )
    assert calls["server_header"] is False and calls["date_header"] is False, "no fingerprint"
    assert calls["log_config"] is None, "our redacting formatter owns stdout (R52)"
    assert calls["access_log"] is False, "request lines come from log_request with outcome only"
    assert calls["timeout_graceful_shutdown"] == 10, "TERM must reach the lifespan (R33)"
    assert calls["app"] is not None


def test_the_served_app_is_this_servers_own_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The entry point hands over the composed app, routes and all — not some default ASGI object."""
    calls: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", _capture_run(calls))
    set_env(monkeypatch)

    assert main([]) == 0
    app = calls["app"]
    assert callable(app), "uvicorn was handed an ASGI application"
    routes = {getattr(route, "path", None) for route in app.routes}
    assert "/healthz" in routes, "the health endpoint create_app added is on the served app"
    assert "/mcp" in routes, "the MCP endpoint is on the served app"
