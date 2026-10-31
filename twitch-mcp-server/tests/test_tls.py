"""T1-T12: HTTPS for the OAuth redirect (``docs/specs/tls-oauth-redirect``).

Nothing here reads, writes or trusts the operator's ``./certs``. The certificate material is
generated per test into pytest's ``tmp_path`` with ``cryptography``, so the module leaves no key
behind and cannot be affected by what is in the working tree.

No test here opens a socket either. uvicorn's TLS wiring is asserted from the arguments
:func:`twitch_mcp.__main__.main` hands ``uvicorn.run`` (T3), and the request-level behaviour is
asserted against the ASGI app, where the test client supplies the ``scheme`` value that uvicorn
would otherwise take from the TLS handshake. A plaintext request to a real TLS listener never reaches
the application: uvicorn refuses it at the handshake (S7), so the app has no plaintext refusal code.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import logging
import os
import re
import shutil
import ssl
import subprocess  # noqa: S404 - runs this repository's own script, never caller-supplied text
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from stat import S_IMODE
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tests.server_fakes import HOST, http_client, running_app, server_settings
from tests.twitch_fakes import (
    FAKE_AUTH_TOKEN,
    FAKE_CLIENT_SECRET,
    FakeClock,
    FakeTwitch,
    make_settings,
    set_env,
)
from twitch_mcp.__main__ import LISTEN_PORT, main
from twitch_mcp.config import ConfigError, load_settings

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
COMPOSE = ROOT / "docker-compose.yml"
COMPOSE_TLS = ROOT / "docker-compose.tls.yml"
DOCKERFILE = ROOT / "Dockerfile"
TLS_SPEC = ROOT / "docs" / "specs" / "tls-oauth-redirect" / "SPEC.md"
MAKE_CERT_SH = ROOT / "scripts" / "make-cert.sh"
MAKE_CERT_PS1 = ROOT / "scripts" / "make-cert.ps1"


# --------------------------------------------------------------------- throwaway certificate material


def _write_pair(directory: Path, *, key_mode: int = 0o600) -> tuple[Path, Path]:
    """A self-signed localhost leaf plus its key, for ``ssl.load_cert_chain`` to accept.

    EC rather than RSA because these are built per test and only need to satisfy a parser. The key
    is written owner-only by default; the key's mode is not checked by the server (S5).
    """
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "server.crt"
    key_path = directory / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.chmod(0o644)
    key_path.chmod(key_mode)
    return cert_path, key_path


def _read(path: str) -> str:
    """Read a file the way a *synchronous* helper may, so the async tests stay ASYNC240-clean."""
    return Path(path).read_text(encoding="utf-8")


#: What the stub ``openssl`` writes into the key. If it ever reaches the terminal, T10 is broken.
FAKE_KEY_MARKER = "FAKE-PRIVATE-KEY-MATERIAL-4c1f"
FAKE_CERT_MARKER = "FAKE-CERTIFICATE-BODY-8ab2"

_OPENSSL_STUB = """#!/usr/bin/env bash
# Enough of openssl for scripts/make-cert.sh, writing material a test can recognise.
set -uo pipefail
out=""
prev=""
for arg in "$@"; do
  if [ "${prev}" = "-out" ]; then out="${arg}"; fi
  prev="${arg}"
done
case "${1}" in
  genrsa)
    [ -n "${out}" ] && printf -- '-----BEGIN PRIVATE KEY-----\\n%s\\n-----END PRIVATE KEY-----\\n' \\
      "${FAKE_KEY_MARKER}" >"${out}"
    ;;
  req | x509)
    [ -n "${out}" ] && printf -- '-----BEGIN CERTIFICATE-----\\n%s\\n-----END CERTIFICATE-----\\n' \\
      "${FAKE_CERT_MARKER}" >"${out}"
    ;;
esac
if printf '%s\\n' "$@" | grep -q -- '-fingerprint'; then
  printf 'sha256 Fingerprint=AA:BB:CC\\n'
fi
exit 0
"""

#: Commands that read a file's bytes out into the open, and so must never meet a private key (T10).
_KEY_READING_COMMANDS = (
    "cat",
    "head",
    "tail",
    "tee",
    "strings",
    "less",
    "more",
    "od",
    "xxd",
    "base64",
    "Get-Content",
    "ReadAllText",
)


def _lines_that_could_print_a_key(script: str) -> list[str]:
    """Lines of a shell/PowerShell script that would put key *bytes* on the terminal.

    Single-quoted literals are blanked first, because ``printf '... TWITCH_TLS_KEY_FILE=/certs/\
server.key'`` is an instruction the operator has to be given and must stay, while ``cat "${LEAF_KEY}"``
    is the leak. The old assertion (``^\\s*cat\\s+.*\\.key``) caught neither form: it needed a literal
    ``.key`` at the end of a line starting with ``cat``, so the quoted instruction and the variable both
    slipped through (reviewer MAJOR 3). The executed test below is the real check; this is the net that
    catches it without running anything.
    """
    offenders: list[str] = []
    for raw in script.splitlines():
        line = re.sub(r"'[^']*'", "''", raw)
        if line.lstrip().startswith(("#", "<")):
            continue
        names_key = re.search(r"\.key\b|\$\{?[A-Za-z_]*KEY\b", line) or re.search(
            r"(ca|server|leaf)[.]key", line, re.IGNORECASE
        )
        reads_bytes = re.search(
            rf"\b({'|'.join(_KEY_READING_COMMANDS)})\b", line, re.IGNORECASE
        ) or re.search(r"\bopenssl\b[^\n]*\b(pkey|rsa|ec)\b[^\n]*-text", line)
        if names_key and reads_bytes:
            offenders.append(raw.strip())
    return offenders


@pytest.fixture
def tls_paths(tmp_path: Path) -> tuple[str, str]:
    """Absolute paths to a valid certificate and its key, outside the repository."""
    cert, key = _write_pair(tmp_path)
    return str(cert), str(key)


def https_client(app: Any) -> httpx.AsyncClient:
    """The same client as :func:`tests.server_fakes.http_client`, over the ``https`` scheme.

    The scheme is the whole point: it is what uvicorn puts in the scope once it has terminated TLS.
    """
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"https://{HOST}")


class _RecordCaptor(logging.Handler):
    """Every emitted record, fields included — what a secret would leak through is usually an extra."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.getMessage()} {record.__dict__}")


@pytest.fixture
def captured_logs() -> Iterator[list[str]]:
    root = logging.getLogger()
    handler = _RecordCaptor()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler.lines
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


# --------------------------------------------------------------------- T1, T2: opt-in and all-or-nothing


def test_the_server_without_tls_configuration_is_the_plain_http_server_of_before() -> None:
    """T1, T2: nothing set means nothing changes — same scheme, same two URLs, no new failure mode."""
    settings = make_settings()
    assert settings.tls_enabled is False
    assert settings.scheme == "http"
    assert settings.redirect_uri == "http://127.0.0.1:8770/oauth/callback"
    assert settings.sign_in_url == "http://127.0.0.1:8770/oauth/start"


@pytest.mark.parametrize(
    ("only", "names"),
    [
        pytest.param("cert", "TWITCH_TLS_KEY_FILE", id="cert-without-key"),
        pytest.param("key", "TWITCH_TLS_CERT_FILE", id="key-without-cert"),
    ],
)
def test_one_tls_variable_alone_stops_startup_and_names_the_missing_one(
    monkeypatch: pytest.MonkeyPatch, tls_paths: tuple[str, str], only: str, names: str
) -> None:
    """T1: half a TLS configuration is a mistake, and it is a startup failure, not a fallback.

    Falling back to plain HTTP here would be the dangerous direction: the operator believes they
    registered an https redirect and the server would be answering http.
    """
    cert, key = tls_paths
    if only == "cert":
        set_env(monkeypatch, TWITCH_TLS_CERT_FILE=cert)
    else:
        set_env(monkeypatch, TWITCH_TLS_KEY_FILE=key)

    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    assert names in str(excinfo.value)
    # T9: the message names the field and the rule. The path it was pointing at is not recited.
    assert cert not in str(excinfo.value)
    assert key not in str(excinfo.value)


def test_blank_tls_variables_are_the_same_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """T1: "``TWITCH_TLS_CERT_FILE=``" in ``.env`` is how an operator means "off"."""
    set_env(monkeypatch, TWITCH_TLS_CERT_FILE="   ", TWITCH_TLS_KEY_FILE="")
    settings = load_settings()
    assert settings.twitch_tls_cert_file == ""
    assert settings.tls_enabled is False
    assert settings.scheme == "http"


# --------------------------------------------------------------------- T3: https follows the certificate


def test_tls_material_derives_https_urls_from_the_host_port(
    tls_paths: tuple[str, str],
) -> None:
    """T3, H4: both derived URLs move to https together, and both follow ``HOST_PORT``.

    The scheme is not a literal at either call site, so there is no pair of places that can drift
    apart: an ``https`` redirect URI is reachable only through the same flag that enables TLS, which
    is what makes the registered URI and the sent one the same string.
    """
    cert, key = tls_paths
    settings = make_settings(twitch_tls_cert_file=cert, twitch_tls_key_file=key, host_port=9770)
    assert settings.tls_enabled is True
    assert settings.scheme == "https"
    assert settings.redirect_uri == "https://127.0.0.1:9770/oauth/callback"
    assert settings.sign_in_url == "https://127.0.0.1:9770/oauth/start"


@pytest.mark.parametrize(
    "tls_on",
    [pytest.param(False, id="tls-off"), pytest.param(True, id="tls-on")],
)
def test_uvicorn_gets_the_certificate_pair_or_explicit_nones(
    monkeypatch: pytest.MonkeyPatch, tls_paths: tuple[str, str], tls_on: bool
) -> None:
    """T3: one listener on one port; TLS is uvicorn's ``ssl_certfile``/``ssl_keyfile`` and nothing else.

    ``None`` twice is not cosmetic. Empty strings would have uvicorn try to build a context from a
    nonexistent file, which fails at a different layer than the one that validates the pair, with a
    message that names a path instead of a field.
    """
    calls: dict[str, Any] = {}

    def run(app: Any, **kwargs: Any) -> None:
        calls.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", run)
    if tls_on:
        cert, key = tls_paths
        set_env(monkeypatch, TWITCH_TLS_CERT_FILE=cert, TWITCH_TLS_KEY_FILE=key)
    else:
        cert, key = "", ""
        set_env(monkeypatch)

    assert main([]) == 0
    assert calls["port"] == LISTEN_PORT
    assert calls["ssl_certfile"] == (cert or None)
    assert calls["ssl_keyfile"] == (key or None)


# --------------------------------------------------------------------- T4, T9: bad material refuses boot


def test_a_missing_certificate_file_refuses_startup_without_reciting_the_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tls_paths: tuple[str, str]
) -> None:
    """T4, T9: a path that names no file is a startup failure that does not repeat the path."""
    _, key = tls_paths
    missing = tmp_path / "absent" / "server.crt"
    set_env(monkeypatch, TWITCH_TLS_CERT_FILE=str(missing), TWITCH_TLS_KEY_FILE=key)

    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    assert "TWITCH_TLS_CERT_FILE" in str(excinfo.value)
    assert "readable file" in str(excinfo.value)
    assert str(missing) not in str(excinfo.value)


def test_material_that_is_not_a_certificate_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T4: the pair is proven parseable before uvicorn is ever called, so boot cannot half-succeed."""
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    cert.write_text("-----BEGIN CERTIFICATE-----\nnot a certificate\n", encoding="utf-8")
    key.write_text("-----BEGIN PRIVATE KEY-----\nnot a key\n", encoding="utf-8")
    cert.chmod(0o644)
    key.chmod(0o600)
    set_env(monkeypatch, TWITCH_TLS_CERT_FILE=str(cert), TWITCH_TLS_KEY_FILE=str(key))

    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    message = str(excinfo.value)
    assert "TWITCH_TLS_CERT_FILE" in message and "parse" in message
    # T9: the *content* is the secret here; the reason is the exception type, never the bytes.
    assert "BEGIN" not in message


def test_an_unreadable_certificate_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T4: present but not readable is the same refusal as absent."""
    cert, key = _write_pair(tmp_path)
    cert.chmod(0o000)
    set_env(monkeypatch, TWITCH_TLS_CERT_FILE=str(cert), TWITCH_TLS_KEY_FILE=str(key))

    try:
        with pytest.raises(ConfigError) as excinfo:
            load_settings()
    finally:
        cert.chmod(0o644)
    assert "TWITCH_TLS_CERT_FILE" in str(excinfo.value)


def test_a_key_that_does_not_match_its_certificate_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T4: a stale ``server.key`` next to a fresh ``server.crt`` is caught here, not at handshake."""
    cert, _ = _write_pair(tmp_path)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    _, mismatched = _write_pair(other_dir)
    set_env(monkeypatch, TWITCH_TLS_CERT_FILE=str(cert), TWITCH_TLS_KEY_FILE=str(mismatched))

    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    assert "matching" in str(excinfo.value)
    assert str(mismatched) not in str(excinfo.value)


def test_a_key_that_is_not_owner_only_is_accepted_without_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, captured_logs: list[str]
) -> None:
    """S5: the key's mode is not checked. A world-readable key that parses and matches is accepted."""
    cert, key = _write_pair(tmp_path, key_mode=0o644)
    assert S_IMODE(Path(key).stat().st_mode) == 0o644
    set_env(monkeypatch, TWITCH_TLS_CERT_FILE=str(cert), TWITCH_TLS_KEY_FILE=str(key))

    assert load_settings().tls_enabled
    assert not any("tls_key_mode_rule_skipped" in line for line in captured_logs)


def test_a_key_and_certificate_inside_the_repository_outside_certs_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S6: the key's location is not checked. Material inside the tree, outside ``certs/``, loads.

    The pair is written to a scratch folder under the repository root for the duration of the test
    only, and removed in ``finally``; nothing is left in the working tree.
    """
    scratch = ROOT / f".s6-scratch-{os.getpid()}"
    scratch.mkdir()
    try:
        cert, key = _write_pair(scratch)
        set_env(monkeypatch, TWITCH_TLS_CERT_FILE=str(cert), TWITCH_TLS_KEY_FILE=str(key))
        assert load_settings().tls_enabled
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# --------------------------------------------------------------------- T6: the request surface


async def test_healthz_carries_the_tls_flag_and_never_needs_a_bearer(
    fake: FakeTwitch, fclock: FakeClock, tls_paths: tuple[str, str]
) -> None:
    """T6: one more key, still unauthenticated, and still no credential or path in the body."""
    cert, key = tls_paths
    settings = server_settings(twitch_tls_cert_file=cert, twitch_tls_key_file=key)
    async with running_app(fake, fclock, settings) as app, https_client(app) as http:
        response = await http.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "version", "tool_count", "twitch_reachable", "tls"}
    assert body["tls"] is True
    # T9: the flag is a boolean, not a description of the transport.
    assert cert not in response.text and key not in response.text
    assert (
        "certs" not in response.text and ".pem" not in response.text and ".key" not in response.text
    )


async def test_healthz_reports_tls_off_when_no_certificate_is_configured(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """T2, T6: the flag reads false for the default deployment, over plain http."""
    async with running_app(fake, fclock) as app, http_client(app) as http:
        body = (await http.get("/healthz")).json()
    assert body["tls"] is False


async def test_a_plaintext_scheme_request_is_answered_by_the_app_when_tls_is_configured(
    fake: FakeTwitch, fclock: FakeClock, tls_paths: tuple[str, str]
) -> None:
    """S7: with TLS configured the app has no plaintext refusal; an http-scheme request is served.

    Plaintext to a real TLS listener is refused by uvicorn at the handshake, which the app never
    sees. The app itself answers an ``http``-scheme scope like any other: a liveness answer with no
    redirect and no bearer requirement on ``/healthz``.
    """
    cert, key = tls_paths
    settings = server_settings(twitch_tls_cert_file=cert, twitch_tls_key_file=key)
    async with running_app(fake, fclock, settings) as app, http_client(app) as http:
        response = await http.get("/healthz")
    assert response.status_code == 200
    assert not 300 <= response.status_code < 400
    assert "location" not in response.headers


async def test_the_same_server_serves_plaintext_when_tls_is_off(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """T2: without TLS configured the plain-http server is unchanged."""
    async with running_app(fake, fclock) as app, http_client(app) as http:
        assert (await http.get("/healthz")).status_code == 200


async def test_tls_does_not_relax_bearer_auth_on_any_route(
    fake: FakeTwitch, fclock: FakeClock, tls_paths: tuple[str, str]
) -> None:
    """T6, T7: https is transport, not authorisation — every route but healthz still needs the token."""
    cert, key = tls_paths
    settings = server_settings(twitch_tls_cert_file=cert, twitch_tls_key_file=key)
    async with running_app(fake, fclock, settings) as app, https_client(app) as http:
        assert (await http.get("/healthz")).status_code == 200
        assert (await http.post("/mcp")).status_code == 401
        assert (await http.get("/mcp")).status_code == 401
        assert (
            await http.get("/healthz", headers={"Origin": "https://evil.example"})
        ).status_code == 403
        assert (await http.get("/healthz", headers={"Host": "example.com"})).status_code == 421


@pytest.mark.parametrize(
    "tls_on", [pytest.param(True, id="tls-on"), pytest.param(False, id="tls-off")]
)
async def test_an_https_origin_is_accepted_whether_or_not_tls_is_on(
    fake: FakeTwitch, fclock: FakeClock, tls_paths: tuple[str, str], tls_on: bool
) -> None:
    """T7: the Host/Origin guard was already scheme-agnostic, so enabling TLS changes nothing there.

    A loopback browser is loopback whichever scheme it arrived over; what the guard judges is the
    hostname (R5), and that judgement is untouched by this feature.
    """
    cert, key = tls_paths
    settings = (
        server_settings(twitch_tls_cert_file=cert, twitch_tls_key_file=key)
        if tls_on
        else server_settings()
    )
    async with running_app(fake, fclock, settings) as app:
        client = https_client(app) if tls_on else http_client(app)
        async with client as http:
            assert (
                await http.get("/healthz", headers={"Origin": f"https://{HOST}"})
            ).status_code == 200
            assert (
                await http.get("/healthz", headers={"Origin": f"http://{HOST}"})
            ).status_code == 200


# --------------------------------------------------------------------- T9: nothing about the key in a log


async def test_no_certificate_detail_reaches_the_log_or_the_health_response(
    fake: FakeTwitch,
    fclock: FakeClock,
    tls_paths: tuple[str, str],
    captured_logs: list[str],
) -> None:
    """T9: the key, its bytes and its path are secrets in the sense of R52, not just in the sense of T7."""
    cert, key = tls_paths
    key_bytes = _read(key)
    settings = server_settings(twitch_tls_cert_file=cert, twitch_tls_key_file=key)
    async with running_app(fake, fclock, settings) as app, https_client(app) as http:
        assert (await http.get("/healthz")).status_code == 200
    joined = "\n".join(captured_logs)
    assert key_bytes not in joined
    assert "PRIVATE KEY" not in joined
    assert key not in joined and cert not in joined
    assert FAKE_AUTH_TOKEN not in joined and FAKE_CLIENT_SECRET not in joined


# --------------------------------------------------------------------- T10-T12: what the operator runs


def test_the_certificate_script_builds_the_documented_identity_and_quiets_the_key() -> None:
    """T10, T-D5: the leaf is localhost with an IP SAN, 398 days, under a 10-year CA, key 0600.

    The leaf identity is what makes the browser accept a certificate for the literal address in the
    registered URI, so it is pinned here as text: a silently-edited ``-addext`` line would produce a
    certificate that every client rejects.
    """
    script = MAKE_CERT_SH.read_text(encoding="utf-8")
    assert "subjectAltName=DNS:localhost,IP:127.0.0.1" in script
    assert "LEAF_DAYS=398" in script
    assert "CA_DAYS=3650" in script
    assert "extendedKeyUsage=serverAuth" in script
    assert "chmod 600" in script
    assert "-fingerprint" in script
    # Never a line that could print key material (T10).
    assert _lines_that_could_print_a_key(script) == []
    assert "openssl genrsa" in script and ">/dev/null" in script


def test_running_the_certificate_script_never_shows_the_key_and_names_the_https_uri(
    tmp_path: Path,
) -> None:
    """T10, T12 (reviewer MAJORs 3 and 4): what the operator is actually shown, by running the script.

    Everything above reads ``make-cert.sh``; this executes it against a stub ``openssl`` that writes
    recognisable key bytes, which is the only way to settle both findings. Two things must be true of
    what it prints: no key byte reaches the terminal (T10), and the URI handed to the operator is
    ``https`` even though ``.env`` at that moment names no TLS variables at all (T3, T12) - exactly the
    state of the world when this script runs, and the reason it cannot ask ``redirect_uri()`` for the
    scheme. The port is still read from ``.env``, so the test also shows what *is* derived.
    """
    work = tmp_path / "repo"
    shutil.copytree(ROOT / "scripts", work / "scripts")
    (work / ".env").write_text("HOST_PORT=8771\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "openssl"
    stub.write_text(_OPENSSL_STUB, encoding="utf-8")
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_KEY_MARKER": FAKE_KEY_MARKER,
        "FAKE_CERT_MARKER": FAKE_CERT_MARKER,
    }

    bash = shutil.which("bash")
    assert bash is not None, "the shell scripts are part of the product, and bash is not installed"
    result = subprocess.run(  # noqa: S603 - this repository's own script, in a throwaway copy
        [bash, str(work / "scripts" / "make-cert.sh")],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    shown = result.stdout + result.stderr
    assert FAKE_KEY_MARKER not in shown, "a private-key byte reached the terminal (T10)"
    assert "PRIVATE KEY" not in shown
    leaf = work / "certs" / "server.key"
    assert FAKE_KEY_MARKER in leaf.read_text(encoding="utf-8"), "the run itself is not vacuous"
    assert "https://127.0.0.1:8771/oauth/callback" in shown, (
        "the URI to register has to be https before .env says anything about TLS"
    )
    assert "http://127.0.0.1:8771" not in shown, (
        "not one cleartext URL may be offered to the console"
    )


def test_the_powershell_twin_trusts_the_ca_for_the_current_user_only() -> None:
    """T10, T-D4: the trust step is user-scoped, which is what keeps it free of administrator rights."""
    ps1 = MAKE_CERT_PS1.read_text(encoding="utf-8")
    assert "-addstore" in ps1 and "-user" in ps1 and "Root" in ps1
    assert "make-cert.sh" in ps1, "the twin drives the same generator rather than a second one"
    assert _lines_that_could_print_a_key(ps1) == []


def test_every_shell_script_in_the_repository_still_parses() -> None:
    """T10 (reviewer minor 4): these scripts are the product's operator interface, so parse them.

    ``shellcheck`` is not installed on this host and is not made a prerequisite here; ``bash -n`` costs
    nothing and catches the kind of edit - an unbalanced quote, a stray ``fi`` - that would otherwise
    surface only when an operator runs ``scripts/restart.sh`` in front of a broken server. The
    PowerShell twins get the read-only review and the trust-command pin above, since ``pwsh`` is not
    installed in this environment either; that gap is recorded as a human follow-up in the fix report.
    """
    bash = shutil.which("bash")
    assert bash is not None, "the shell scripts are part of the product, and bash is not installed"
    scripts = sorted((ROOT / "scripts").rglob("*.sh"))
    assert len(scripts) >= 8, f"expected the whole scripts/ tree, found {[p.name for p in scripts]}"
    for script in scripts:
        check = subprocess.run(  # noqa: S603 - bash parsing a file from this repository's own tree
            [bash, "-n", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert check.returncode == 0, f"{script.relative_to(ROOT)} does not parse: {check.stderr}"


def test_the_tls_override_mounts_the_folder_read_only_and_nothing_else() -> None:
    """T11: ``./certs`` arrives read-only, so the process cannot rewrite the key it is serving."""
    override = COMPOSE_TLS.read_text(encoding="utf-8")
    assert re.search(r"-\s*\./certs:/certs:ro", override), "the mount must be :ro"
    assert re.search(r"-\s*\./certs:/certs\s*$", override, re.MULTILINE) is None, (
        "never a writable mount"
    )
    assert "read_only: true" not in override, "the base file owns the rootfs posture"
    base = COMPOSE.read_text(encoding="utf-8")
    assert "volumes:" not in base.split("services:", 1)[-1], (
        "with TLS off nothing is mounted at all (T2)"
    )


def test_the_tls_override_also_tells_the_container_where_the_material_is() -> None:
    """T11 (reviewer MAJOR 2): mounting the folder without naming the files is a silent downgrade.

    Deleting the two ``environment`` entries leaves the key mounted and unconfigured: ``config.py``
    sees no certificate, the server answers plain HTTP, and the bearer token goes out in the clear,
    while the compose file still looks like a TLS deployment. That silent downgrade is what the
    mount alone cannot prevent, and the suite stayed green without these entries once. So the two defaults are pinned
    here, and pinned *against the mount*: they must name files inside the folder that gets mounted,
    which is also exactly what ``make-cert.sh`` step 2 tells the operator to put in ``.env``.
    """
    override = COMPOSE_TLS.read_text(encoding="utf-8")
    mount = re.search(r"-\s*\./certs:(/[^\s:]+)(?::|\s*$)", override)
    assert mount, "the override must mount the folder somewhere absolute"
    mounted_at = mount.group(1)
    for variable, filename in (
        ("TWITCH_TLS_CERT_FILE", "server.crt"),
        ("TWITCH_TLS_KEY_FILE", "server.key"),
    ):
        pattern = (
            r"^\s*"
            + re.escape(variable)
            + r":\s*\$\{"
            + re.escape(variable)
            + r":-([^{}\s]+)"
            + r"\}\s*$"
        )
        line = re.search(pattern, override, re.MULTILINE)
        assert line, f"{variable} must default to the file inside the mounted folder"
        assert line.group(1) == f"{mounted_at}/{filename}", (
            f"{variable} must point at the folder this same file mounts"
        )
    script = MAKE_CERT_SH.read_text(encoding="utf-8")
    assert "TWITCH_TLS_CERT_FILE=/certs/server.crt" in script, (
        "the paths .env is told to use and the paths compose defaults to must be one decision"
    )
    assert "TWITCH_TLS_KEY_FILE=/certs/server.key" in script


_HEALTH_PROBE_RE = re.compile(r'"(import os,ssl,sys,urllib\.request as u;[^"]*)"')


def _health_probe_snippets() -> list[str]:
    """The ``python -c`` healthcheck bodies as the image and compose will actually run them (T-D8)."""
    found: list[str] = []
    for source in (DOCKERFILE.read_text(encoding="utf-8"), COMPOSE.read_text(encoding="utf-8")):
        match = _HEALTH_PROBE_RE.search(source)
        assert match, "the healthcheck must be a quoted python -c one-liner this test can run"
        found.append(match.group(1))
    assert found[0] == found[1], (
        "the image and compose must not drift apart on how liveness is asked"
    )
    return found


@pytest.mark.parametrize(
    ("cert", "scheme", "verifies"),
    [
        pytest.param(None, "http", True, id="no-tls"),
        pytest.param("", "http", True, id="blank-tls-var"),
        pytest.param("/certs/server.crt", "https", False, id="tls-on"),
    ],
)
def test_the_shipped_healthcheck_probes_the_scheme_the_container_is_serving(
    monkeypatch: pytest.MonkeyPatch, cert: str | None, scheme: str, verifies: bool
) -> None:
    """T-D8, reviewer minor 3: the healthcheck string, executed rather than string-matched.

    Substring assertions on a one-liner can be satisfied by a probe that would fail in the container
    (a swapped ternary, an ``http`` URL under TLS, a ``sys.exit`` on the wrong status). Here the exact
    text from the ``Dockerfile`` and ``docker-compose.yml`` is compiled and run, with only
    :func:`urllib.request.urlopen` replaced so nothing opens a socket, and the exit code and the URL
    that would have been requested are both checked.

    Verification is off for this one self-probe because the issuer is the operator's own local CA,
    which the image cannot and should not carry. It is not a general relaxation: nothing in
    :mod:`twitch_mcp.network` can reach this code path.
    """
    if cert is None:
        monkeypatch.delenv("TWITCH_TLS_CERT_FILE", raising=False)
    else:
        monkeypatch.setenv("TWITCH_TLS_CERT_FILE", cert)
    asked: dict[str, Any] = {}

    def fake_urlopen(
        url: str, timeout: float | None = None, context: ssl.SSLContext | None = None
    ) -> Any:
        asked.update(url=url, timeout=timeout, context=context)
        return SimpleNamespace(status=200)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    for snippet in _health_probe_snippets():
        asked.clear()
        code = _exit_code_of(snippet)
        assert code == 0, "a healthy container must exit 0"
        assert asked["url"] == f"{scheme}://127.0.0.1:8000/healthz"
        assert (asked["context"] is None) is verifies, (
            "cleartext gets no context; TLS gets the unverified one, and only for this self-probe"
        )


def test_the_shipped_healthcheck_exits_nonzero_when_healthy_is_a_lie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-D8: a 503 from /healthz has to fail the probe, or an unhealthy container stays "healthy"."""
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, **_: SimpleNamespace(status=503))
    assert _exit_code_of(_health_probe_snippets()[0]) == 1


def _exit_code_of(snippet: str) -> int:
    """Run the healthcheck one-liner and report the status it would have exited with."""
    try:
        exec(compile(snippet, "<healthcheck>", "exec"), {})  # noqa: S102 - our own Dockerfile line
    except SystemExit as exc:  # the probe always ends in sys.exit(...)
        return int(exc.code or 0)
    raise AssertionError("the healthcheck must end in sys.exit, whatever the answer was")


def test_the_readme_gives_the_uri_to_register_and_the_trust_command() -> None:
    """T12: the README is the operator's only source for the URI, so it must carry the exact string."""
    readme = README.read_text(encoding="utf-8")
    assert "https://127.0.0.1:8770/oauth/callback" in readme
    assert "certutil -addstore -user Root" in readme
    assert "make-cert" in readme
    assert "TWITCH_TLS_CERT_FILE" in readme and "TWITCH_TLS_KEY_FILE" in readme


# --------------------------------------------------------------------- the map itself


#: The requirements this suite was written against. Pinned as a literal set, because recomputing it
#: from the spec can only show a requirement that no test cites - deleting one from ``SPEC.md`` simply
#: shrinks both sides of the comparison and leaves the sweep green with one fewer rule covered.
EXPECTED_TLS_REQUIREMENTS = frozenset({"1", "2", "3", "4", "6", "7", "9", "10", "11", "12"})

#: Requirements removed by the simplification amendment (S9c): the repo-tree check and the plaintext
#: refusal. They stay in the TLS spec's text; no test here may cite them by number.
SUPERSEDED_TLS_REQUIREMENTS = frozenset({"5", "8"})


def test_every_tls_requirement_is_named_by_a_test_in_this_module() -> None:
    """R57's discipline applied to the amendment: a T requirement no test names is not implemented.

    The main spec's sweep in :mod:`tests.test_gate` walks ``docs/SPEC.md`` only, and this feature's
    requirements deliberately live in their own amendment file, so the same rule is re-applied here -
    in both directions, so the map fails if a requirement disappears from the spec as well as if a
    citation disappears from this module (reviewer minor 2, mirroring ``test_the_citation_map_is_not_stale``).
    """
    body = TLS_SPEC.read_text(encoding="utf-8")
    declared = frozenset(re.findall(r"^- \*\*T(\d+)\*\*", body, re.MULTILINE))
    required = declared - SUPERSEDED_TLS_REQUIREMENTS
    assert required == EXPECTED_TLS_REQUIREMENTS, (
        f"docs/specs/tls-oauth-redirect/SPEC.md declares "
        f"{', '.join('T' + t for t in sorted(required, key=int))} but this suite covers "
        f"{', '.join('T' + t for t in sorted(EXPECTED_TLS_REQUIREMENTS, key=int))}"
    )
    cited = frozenset(re.findall(r"\bT(\d+)\b", Path(__file__).read_text(encoding="utf-8")))
    missing = sorted(required - cited, key=int)
    assert not missing, f"no test cites {', '.join('T' + t for t in missing)}"
    orphan = sorted(cited - required, key=int)
    assert not orphan, (
        f"this module cites {', '.join('T' + t for t in orphan)}, which the TLS spec does not define"
    )


async def test_json_serialisation_of_the_health_body_is_still_small(
    fake: FakeTwitch, fclock: FakeClock, tls_paths: tuple[str, str]
) -> None:
    """T9: a health body is a liveness answer, not a place to describe the deployment."""
    cert, key = tls_paths
    settings = server_settings(twitch_tls_cert_file=cert, twitch_tls_key_file=key)
    async with running_app(fake, fclock, settings) as app, https_client(app) as http:
        body = (await http.get("/healthz")).json()
    assert len(json.dumps(body)) < 256
