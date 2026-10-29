"""Opt-in manual check against the REAL Discord API using the user's .env (never run by tests).

Usage (from the server directory, in a venv with the package installed):

    python tools/try_live.py list_guilds
    python tools/try_live.py list_channels <guild_id>

It reads the environment file named by ENV_FILE (default ``.env``) by parsing ``KEY=VALUE`` lines
(it never prints values), boots the same service the server uses, calls one tool and prints the
projected result size and a short summary. It sends real requests with the bot token: run it only
against guilds you own or administer.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from discord_mcp.config import ConfigError, Settings
from discord_mcp.discord_client import DiscordClient
from discord_mcp.service import DiscordService


def load_env_file(path: Path) -> None:
    """Export KEY=VALUE pairs from ``path`` without executing anything in it."""
    if not path.is_file():
        raise SystemExit(f"{path} not found; run scripts/setup.sh first")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


async def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python tools/try_live.py list_guilds | list_channels <guild_id>")
        return 2
    load_env_file(Path(os.environ.get("ENV_FILE", ".env")))
    try:
        settings = Settings()  # type: ignore[call-arg]
    except Exception as exc:  # pydantic reports field names only
        raise SystemExit(f"invalid configuration: {type(exc).__name__}") from None
    client = DiscordClient(settings)
    service = DiscordService(settings, client, now=lambda: datetime.now(UTC))
    try:
        outcome = await service.boot()
        print(f"boot probe: {outcome}")
        if argv[0] == "list_guilds":
            result = await service.list_guilds()
            payload = result.model_dump(mode="json")
        elif argv[0] == "list_channels" and len(argv) == 2:
            result = await service.list_channels(argv[1])
            payload = result.model_dump(mode="json")
        else:
            print("unknown tool or arguments")
            return 2
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        print(f"result: {len(text.encode())} bytes of JSON")
        print(json.dumps(payload.get("meta", {}), indent=2))
        return 0
    except Exception as exc:
        from discord_mcp.errors import DiscordMcpError

        if isinstance(exc, DiscordMcpError):
            print(exc.to_text())
            return 1
        raise
    finally:
        await service.aclose()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(sys.argv[1:])))
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
