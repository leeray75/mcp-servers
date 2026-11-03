#!/usr/bin/env python3
"""Opt-in live smoke against the real Twitch API — acceptance criteria A2 and A3.

**Never run by pytest and never run by ``make check``.** The whole test suite is network-free by
design (R10), which is the right trade for tests but means nothing in it has ever seen Helix's real
answers. This is the one place that does, using the operator's own ``.env`` and the same composition
the container boots (``create_app`` + its lifespan), so what is exercised here is what ships.

Every call costs a real rate-limit token, and ``--writes`` changes state on a live channel, so the
script refuses to act unless ``TWITCH_LIVE_OK=yes`` is set in the environment. That gate is
deliberate: there is no argument that runs it, only an environment the operator sets on purpose.

    TWITCH_LIVE_OK=yes .venv/bin/python tools/try_live.py             # A2: the read smoke
    TWITCH_LIVE_OK=yes .venv/bin/python tools/try_live.py --writes    # A3: also create + end a poll

Exit codes: 0 every step answered, 1 a step failed (its outcome kind is printed), 2 refused or
misconfigured. Nothing here prints a client secret, a token or any bearer — only statuses, counts
and the ids Twitch itself returned (R52).
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

# Repo-root fallback so the script also runs straight from a checkout without an editable install.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from twitch_mcp.config import ConfigError, load_settings  # noqa: E402
from twitch_mcp.errors import TwitchMcpError  # noqa: E402
from twitch_mcp.logging_setup import configure_logging  # noqa: E402
from twitch_mcp.server import create_app  # noqa: E402

GATE = "TWITCH_LIVE_OK"
POLL_TITLE = "goose twitch-mcp live smoke"
POLL_CHOICES = ("yes", "no")


def _say(ok: bool, label: str, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    return ok


def _ids(settings: Any) -> list[str]:
    """The allowlisted broadcaster ids this server is allowed to touch."""
    return [str(value) for value in settings.twitch_allowed_broadcaster_ids if str(value)]


async def _reads(service: Any, settings: Any, operator_id: str | None) -> list[bool]:
    """A2: the four reads that prove the credential, the guard, pagination and projection all work."""
    results: list[bool] = []
    ids = _ids(settings)
    target = operator_id or (ids[0] if ids else None)
    if target is None:
        results.append(_say(False, "get_users", "no TWITCH_ALLOWED_BROADCASTER_IDS to ask about"))
        return results

    try:
        users = await service.get_users(ids=[target])
        rows = users.users or []
        results.append(_say(bool(rows), "get_users", f"{len(rows)} record(s)"))
    except TwitchMcpError as exc:
        results.append(_say(False, "get_users", f"kind={exc.kind}"))

    try:
        channels = await service.get_channels(broadcaster_ids=[target])
        rows = channels.channels or []
        results.append(_say(bool(rows), "get_channels", f"{len(rows)} record(s)"))
    except TwitchMcpError as exc:
        results.append(_say(False, "get_channels", f"kind={exc.kind}"))

    try:
        streams = await service.get_streams(user_ids=[target], first=1)
        # A live-0 answer is a success: it proves the call and the envelope, not that anyone streams.
        rows = streams.streams or []
        results.append(_say(True, "get_streams", f"{len(rows)} live stream(s) right now"))
    except TwitchMcpError as exc:
        results.append(_say(False, "get_streams", f"kind={exc.kind}"))

    return results


async def _poll_round_trip(service: Any, target: str) -> list[bool]:
    """A3: create then end a poll, exercising the whole R31/R33 confirmation flow for real."""
    results: list[bool] = []
    try:
        first = await service.create_poll(
            broadcaster_id=target, title=POLL_TITLE, choices=list(POLL_CHOICES)
        )
        if not first.confirmation_required or first.confirmation is None:
            return [_say(False, "create_poll", f"expected a confirmation turn, got {first!r:.80}")]
        token = first.confirmation.token
        results.append(_say(bool(token), "create_poll confirmation minted (R31)"))
        if not token:
            return results

        created = await service.create_poll(
            broadcaster_id=target,
            title=POLL_TITLE,
            choices=list(POLL_CHOICES),
            confirm=token,
        )
        records = created.data or []
        poll_id = str(records[0].get("id", "")) if records else ""
        results.append(
            _say(bool(created.dispatched) and bool(poll_id), "create_poll dispatched (R16)")
        )
        if not poll_id:
            return results

        # Ending it is a second, independent confirmation — and leaves the channel as it was found.
        closing = await service.end_poll(broadcaster_id=target, poll_id=poll_id)
        if closing.confirmation is None or not closing.confirmation.token:
            return results + [_say(False, "end_poll", "no confirmation turn was minted")]
        ended = await service.end_poll(
            broadcaster_id=target,
            poll_id=poll_id,
            status="TERMINATED",
            confirm=closing.confirmation.token,
        )
        results.append(_say(bool(ended.dispatched), "end_poll dispatched — poll cleaned up"))
    except TwitchMcpError as exc:
        # A lost write answer is reported, never retried: the poll may or may not exist. Report it.
        results.append(_say(False, "write", f"kind={exc.kind}; do not assume nothing happened"))
    return results


async def _run(writes: bool) -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    app = create_app(settings)
    results: list[bool] = []

    # The lifespan is what uvicorn drives, and it runs the R50 boot probe before anything is served.
    async with app.router.lifespan_context(app):
        wiring = app.state.twitch_wiring
        service = app.state.twitch_service

        results.append(
            _say(
                wiring.twitch_reachable,
                "app credential (boot probe)",
                f"outcome={wiring.probe_outcome}, tools={wiring.tool_count}",
            )
        )
        operator_id: str | None = wiring.operator.broadcaster_id
        results.append(
            _say(
                bool(operator_id),
                "user credential",
                "connected" if operator_id else "not connected (writes and own-channel need it)",
            )
        )
        results += await _reads(service, settings, operator_id)

        if writes:
            if not operator_id:
                results.append(
                    _say(
                        False,
                        "--writes",
                        "needs a connected account: sign in with connect_twitch_account first",
                    )
                )
            else:
                results += await _poll_round_trip(service, operator_id)
        else:
            print("SKIP  write smoke (pass --writes to create and end a poll on your own channel)")

    await wiring.aclose()
    return 0 if all(results) else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    unknown = [arg for arg in args if arg not in {"--writes"}]
    if unknown:
        print(f"unknown argument(s): {', '.join(unknown)}; this script takes only --writes")
        return 2

    if os.environ.get(GATE) != "yes":
        print(f"refusing to call the real Twitch API: set {GATE}=yes to confirm you mean it")
        return 2

    try:
        return asyncio.run(_run("--writes" in args))
    except ConfigError as exc:
        # ConfigError names fields, never values (R7), so this is safe to print.
        print(f"configuration error: {exc}")
        return 2
    except KeyboardInterrupt:
        print("interrupted")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
