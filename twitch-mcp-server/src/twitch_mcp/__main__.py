"""Console entry point: ``python -m twitch_mcp`` / ``twitch-mcp``.

The process is deliberately thin. ``config.py`` is the only module that reads the environment (R6),
``server.create_app`` owns every collaborator, and the lifespan that ``uvicorn`` drives runs the
boot probe before the listener serves a byte (SPEC §6.2). So this module has exactly three jobs:

1. load settings, and on a :class:`~twitch_mcp.config.ConfigError` name the offending **field** and
   exit 2 — the message never carries a value, so a bad secret cannot leak through a terminal (R7);
2. configure redacting JSON logging (R52) at the configured level;
3. hand the app to uvicorn on ``0.0.0.0:8000`` — the container-internal bind. Docker publishes it as
   ``127.0.0.1:8770:8000`` (R1), so loopback-only is a compose concern, and the bind here is the
   address inside the container's own network namespace. When TLS is configured (T1) the same call
   gets ``ssl_certfile``/``ssl_keyfile``, so the listener speaks HTTPS on that one port (T3).

Shutdown is clean on SIGINT/SIGTERM: uvicorn runs the lifespan's ``finally``, which closes the
connection pool and **clears every outstanding confirmation token** (R33). No argument parsing:
configuration is environment-only, and an accidental ``twitch-mcp --help`` should not silently
start a server either, so stray arguments exit 2 with a usage line.
"""

import sys

import uvicorn

from twitch_mcp.config import ConfigError, load_settings
from twitch_mcp.logging_setup import configure_logging
from twitch_mcp.server import create_app

#: The container-internal listen port. Docker maps it to ``127.0.0.1:8770`` on the host (R1).
LISTEN_PORT = 8000

USAGE = "twitch-mcp takes no arguments; configure it through the environment (see README.md)."


def main(argv: list[str] | None = None) -> int:
    """Run the server. Returns the process exit code.

    ``argv`` exists purely so tests can exercise the usage path without patching ``sys.argv``.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print(USAGE, file=sys.stderr)
        return 2

    try:
        settings = load_settings()
    except ConfigError as exc:
        # ConfigError already names fields, never values (R7).
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings.log_level)
    app = create_app(settings)

    # ``host="0.0.0.0"`` is the in-container bind; the published port is bound to 127.0.0.1 by
    # docker-compose.yml (R1) and the Host/Origin middleware refuses anything but loopback anyway
    # (R5). uvicorn owns the signal handlers, so a TERM walks the lifespan's finally block and
    # drops confirmation tokens (R33) before the process exits.
    #
    # TLS is likewise all-or-nothing (T1, T3): both paths are ``None`` unless configuration supplied
    # both, and uvicorn then builds the listener's SSL context from them. ``config.py`` has already
    # proved the pair parses and matches, so a broken certificate stops the process with a
    # field-naming message rather than leaving a half-listening socket. The key is not mode-checked
    # (S5): keeping it owner-only is the operator's responsibility.
    certfile: str | None = settings.twitch_tls_cert_file or None
    keyfile: str | None = settings.twitch_tls_key_file or None
    uvicorn.run(  # noqa: S104 - container-internal bind, published as 127.0.0.1:8770 by compose
        app,
        host="0.0.0.0",  # noqa: S104 - container bind; compose publishes on 127.0.0.1 only
        port=LISTEN_PORT,
        ssl_certfile=certfile,
        ssl_keyfile=keyfile,
        log_config=None,  # our own redacting formatter owns stdout (R52)
        access_log=False,  # request lines come from logging_setup.log_request with outcome only
        server_header=False,  # no version fingerprint in responses
        date_header=False,
        proxy_headers=False,  # loopback-only: never trust a forged X-Forwarded-For
        timeout_graceful_shutdown=10,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
