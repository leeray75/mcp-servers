"""Entrypoint: ``python -m sports_mcp`` (HTTP, for Docker) or ``python -m sports_mcp --stdio``."""

from __future__ import annotations

import logging
import sys

import uvicorn

from sports_mcp.config import ConfigError, load_settings
from sports_mcp.logging_setup import configure_logging
from sports_mcp.server import create_app, create_server

logger = logging.getLogger("sports_mcp")

USAGE = "usage: python -m sports_mcp [--stdio]"


def main(argv: list[str] | None = None) -> int:
    """Validate configuration, then serve until terminated. Returns an exit code."""
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], ["--stdio"]):
        print(USAGE, file=sys.stderr)
        return 2
    stdio = args == ["--stdio"]

    try:
        settings = load_settings(require_auth_token=not stdio)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    # In stdio mode stdout carries the protocol, so logs must go to stderr.
    configure_logging(settings.log_level, stream=sys.stderr if stdio else None)
    if stdio:
        logger.info("starting", extra={"outcome": "stdio"})
        create_server(settings).run(transport="stdio")
        return 0

    logger.info("starting", extra={"outcome": f"listening:{settings.port}"})
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
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
