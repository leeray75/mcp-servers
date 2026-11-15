"""R2: DNS probe. Never touches the network: getaddrinfo is monkeypatched."""

from __future__ import annotations

import errno
import socket
import threading
import time
from typing import Any

import httplib2
import pytest
from google.auth.exceptions import TransportError

from gmail_mcp import netcheck
from gmail_mcp.netcheck import PROBE_HOSTS, resolve_hosts


def test_probe_hosts_are_the_two_google_hosts_the_server_uses() -> None:
    assert PROBE_HOSTS == ("gmail.googleapis.com", "oauth2.googleapis.com")


def test_all_hosts_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake(host: str, port: Any, **_kwargs: Any) -> list[Any]:
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    result = resolve_hosts()
    assert result.ok is True
    assert all(item.ok and item.error is None for item in result.hosts)
    assert sorted(calls) == sorted(PROBE_HOSTS)


def test_gaierror_reports_class_name_only(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_like = "10.255.255.254 internal detail"

    def fake(host: str, port: Any, **_kwargs: Any) -> list[Any]:
        if host == "oauth2.googleapis.com":
            raise socket.gaierror(-3, secret_like)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    result = resolve_hosts()
    assert result.ok is False
    failed = {item.host: item for item in result.hosts}["oauth2.googleapis.com"]
    assert failed.ok is False
    assert failed.error == "gaierror"
    rendered = repr(result)
    assert secret_like not in rendered
    assert "192.0.2.1" not in rendered


def test_no_addresses_are_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(host: str, port: Any, **_kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.7", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    rendered = repr(resolve_hosts())
    assert "198.51.100.7" not in rendered


def test_hanging_resolver_returns_within_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    def hang(host: str, port: Any, **_kwargs: Any) -> list[Any]:
        release.wait(5)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", hang)
    try:
        started = time.monotonic()
        result = resolve_hosts(timeout=0.2)
        elapsed = time.monotonic() - started
    finally:
        release.set()
    assert result.ok is False
    assert elapsed < 1.5
    assert all(item.error == "TimeoutError" for item in result.hosts)


def test_never_raises_on_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(host: str, port: Any, **_kwargs: Any) -> list[Any]:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    result = resolve_hosts()
    assert result.ok is False
    assert {item.error for item in result.hosts} == {"RuntimeError"}


def test_empty_host_list_is_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(netcheck, "PROBE_HOSTS", ())
    result = resolve_hosts(hosts=())
    assert result.ok is False
    assert result.hosts == ()


def test_module_is_stdlib_only() -> None:
    import ast
    from pathlib import Path

    source = Path(netcheck.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            assert node.module is not None
            assert not node.module.startswith("gmail_mcp")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("gmail_mcp")


# --- is_dns_failure (network-failure-diagnostics R1): classification only, no I/O ---------------


def _chain(length: int, dns_at: int | None) -> BaseException:
    """A ``__context__`` chain of ``length`` exceptions; the DNS error sits at index ``dns_at``."""
    head: BaseException | None = None
    for index in reversed(range(length)):
        if dns_at is not None and index == dns_at:
            current: BaseException = socket.gaierror(-2, "Name or service not known")
        else:
            current = RuntimeError(f"link {index}")
        current.__context__ = head
        head = current
    assert head is not None
    return head


@pytest.mark.parametrize(
    "exc",
    [
        socket.gaierror(-2, "Name or service not known"),
        httplib2.ServerNotFoundError("Unable to find the server"),
    ],
)
def test_dns_error_types_are_dns_failures(exc: BaseException) -> None:
    assert netcheck.is_dns_failure(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        httplib2.HttpLib2Error("generic"),
        TimeoutError("slow"),
        OSError(errno.ECONNRESET, "reset"),
        RuntimeError("bug"),
    ],
)
def test_other_failures_are_not_dns_failures(exc: BaseException) -> None:
    assert netcheck.is_dns_failure(exc) is False


def test_dns_error_found_through_cause_context_and_args() -> None:
    dns = socket.gaierror(-2, "x")
    via_cause = RuntimeError("outer")
    via_cause.__cause__ = dns
    via_context = RuntimeError("outer")
    via_context.__context__ = dns
    via_args = TransportError(dns)
    assert netcheck.is_dns_failure(via_cause) is True
    assert netcheck.is_dns_failure(via_context) is True
    assert netcheck.is_dns_failure(via_args) is True


def test_chain_inspection_is_bounded_to_eight_links() -> None:
    assert netcheck.is_dns_failure(_chain(8, dns_at=7)) is True
    assert netcheck.is_dns_failure(_chain(10, dns_at=8)) is False


def test_cyclic_chain_terminates() -> None:
    first = RuntimeError("a")
    second = RuntimeError("b")
    first.__context__ = second
    second.__context__ = first
    assert netcheck.is_dns_failure(first) is False


def test_cyclic_chain_still_finds_a_dns_error() -> None:
    first = RuntimeError("a")
    dns = socket.gaierror(-2, "x")
    first.__context__ = dns
    dns.__context__ = first
    assert netcheck.is_dns_failure(first) is True
