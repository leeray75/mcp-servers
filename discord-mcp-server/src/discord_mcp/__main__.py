"""Entrypoint: ``python -m discord_mcp`` (Streamable HTTP on 0.0.0.0:8000 inside the container)."""

from __future__ import annotations

import logging
import sys

import uvicorn

from discord_mcp.config import ConfigError, load_settings
from discord_mcp.logging_setup import configure_logging
from discord_mcp.server import create_app

logger = logging.getLogger("discord_mcp")

USAGE = "usage: python -m discord_mcp"


def main(argv: list[str] | None = None) -> int:
    """Validate configuration, then serve until terminated. Returns an exit code."""
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(USAGE, file=sys.stderr)
        return 2

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    configure_logging(settings.log_level)
    logger.info("starting", extra={"outcome": "listening:8000"})
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 - container bind; compose publishes on 127.0.0.1 only
        port=8000,
        log_config=None,
        access_log=False,
        server_header=False,
        date_header=False,
        proxy_headers=False,
        timeout_graceful_shutdown=10,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
