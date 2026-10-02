"""Entrypoint: ``python -m gmail_mcp``."""

from __future__ import annotations

import logging
import sys

import uvicorn

from gmail_mcp.config import ConfigError, load_settings
from gmail_mcp.logging_setup import configure_logging
from gmail_mcp.server import create_app

logger = logging.getLogger("gmail_mcp")


def main() -> int:
    """Validate configuration, then serve until terminated. Returns an exit code."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    configure_logging(settings.log_level)
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
