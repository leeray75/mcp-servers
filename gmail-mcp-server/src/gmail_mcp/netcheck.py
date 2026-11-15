"""DNS resolution probe for the container healthcheck (stdlib plus httplib2 for error types).

Only name resolution is checked: no HTTPS request and no TCP connect, so a Google-side
outage never changes the container's health. Results carry booleans and exception class
names only; resolved addresses are discarded. :func:`resolve_hosts` never raises.
:func:`is_dns_failure` classifies an exception from a Gmail call as a name-resolution failure.
Stdlib plus ``httplib2``, whose error types it must recognise.
"""

from __future__ import annotations

import socket
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Final

import httplib2

PROBE_HOSTS: Final[tuple[str, ...]] = ("gmail.googleapis.com", "oauth2.googleapis.com")
DEFAULT_TIMEOUT_S: Final = 2.0
_PROBE_PORT: Final = 443
_CHAIN_LIMIT: Final = 8


@dataclass(frozen=True)
class HostResult:
    """Outcome for one hostname. ``error`` is an exception class name, never a message."""

    host: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class DnsResult:
    """Overall result: ``ok`` only when every probed host resolved."""

    ok: bool
    hosts: tuple[HostResult, ...]


def _lookup(host: str) -> HostResult:
    try:
        socket.getaddrinfo(host, _PROBE_PORT, type=socket.SOCK_STREAM)
    except Exception as exc:  # any resolver failure is a negative result
        return HostResult(host=host, ok=False, error=type(exc).__name__)
    return HostResult(host=host, ok=True)


def resolve_hosts(
    hosts: Sequence[str] = PROBE_HOSTS,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> DnsResult:
    """Resolve ``hosts`` concurrently within one overall wall-clock ``timeout`` (seconds).

    A host still unresolved at the deadline counts as failed with error ``TimeoutError``.
    The lookups run in worker threads, so a hung resolver is abandoned rather than awaited.
    """
    names = tuple(hosts)
    try:
        executor = ThreadPoolExecutor(max_workers=max(1, len(names)), thread_name_prefix="dns")
        try:
            futures = [(name, executor.submit(_lookup, name)) for name in names]
            done, _pending = wait([future for _, future in futures], timeout=timeout)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        results: list[HostResult] = []
        for name, future in futures:
            if future in done:
                results.append(future.result())
            else:
                results.append(HostResult(host=name, ok=False, error="TimeoutError"))
    except Exception as exc:  # defensive: this function must never raise
        results = [HostResult(host=name, ok=False, error=type(exc).__name__) for name in names]
    return DnsResult(ok=bool(names) and all(item.ok for item in results), hosts=tuple(results))


def is_dns_failure(exc: BaseException) -> bool:
    """True when ``exc`` or an exception it chains to is a name-resolution failure.

    Checks ``socket.gaierror`` and ``httplib2.ServerNotFoundError`` on ``exc`` and on its
    ``__cause__``, ``__context__`` and exception ``args`` entries. google_auth_httplib2 raises
    ``TransportError(exc)`` inside its ``except`` block, so the httplib2 error is found via
    ``__context__`` and ``args``. At most 8 exceptions are inspected, and a cycle ends the walk.
    """
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending and len(seen) < _CHAIN_LIMIT:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (socket.gaierror, httplib2.ServerNotFoundError)):
            return True
        for linked in (current.__cause__, current.__context__, *current.args):
            if isinstance(linked, BaseException):
                pending.append(linked)
    return False
