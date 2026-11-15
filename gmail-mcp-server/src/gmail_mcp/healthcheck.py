"""Container healthcheck: ``python -m gmail_mcp.healthcheck``.

Exits 0 only when the local ``/healthz`` answers 200 and the Google hosts resolve. Reads no
secrets, never imports ``config.Settings``, and prints at most one content-free line on failure.
The result is a label only: Docker does not restart an unhealthy container.
"""

from __future__ import annotations

import http.client
import os
import sys
import urllib.request
from typing import Final

from gmail_mcp.netcheck import resolve_hosts

HTTP_TIMEOUT_S: Final = 2.0
DNS_TIMEOUT_S: Final = 2.0
DEFAULT_PORT: Final = "8000"


def _http_ok(port: str) -> bool:
    url = f"http://127.0.0.1:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as response:
            return bool(response.status == 200)
    except (OSError, http.client.HTTPException, ValueError):
        return False


def main() -> int:
    """Return 0 when healthy, 1 otherwise."""
    port = os.environ.get("PORT", DEFAULT_PORT)
    http_ok = _http_ok(port)
    dns_ok = resolve_hosts(timeout=DNS_TIMEOUT_S).ok
    if http_ok and dns_ok:
        return 0
    print(
        f"healthcheck failed: http={'ok' if http_ok else 'fail'} dns={'ok' if dns_ok else 'fail'}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
