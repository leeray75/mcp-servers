"""R1, R2: the container hardening the security posture depends on.

These requirements are about *how the process is run*, so they cannot be exercised by importing
Python — and a one-character edit to ``docker-compose.yml`` (dropping ``127.0.0.1:`` from the port
mapping) would silently expose a token-bearing MCP server to the LAN while every unit test stayed
green. So the deployment files are read and asserted literally. Nothing here shells out to Docker:
the gate must run on a laptop with no daemon.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
OAUTH_COMPOSE = ROOT / "docker-compose.oauth.yml"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose() -> str:
    return COMPOSE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def oauth_compose() -> str:
    return OAUTH_COMPOSE.read_text(encoding="utf-8")


def _lines(text: str) -> list[str]:
    """Non-blank, non-comment lines, with a trailing `` #...`` comment removed. Indentation is kept."""
    out: list[str] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        out.append(re.sub(r"\s+#.*$", "", raw.rstrip()))
    return out


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block(lines: list[str], header: str, indent: int) -> list[str]:
    """The lines nested under the first ``header`` line sitting at ``indent`` (deeper lines only)."""
    for i, line in enumerate(lines):
        if line == " " * indent + header:
            base = _indent(line)
            nested: list[str] = []
            for child in lines[i + 1 :]:
                if _indent(child) <= base:
                    break
                nested.append(child)
            return nested
    return []


def _top_keys(lines: list[str]) -> list[str]:
    return [line[:-1] for line in lines if _indent(line) == 0 and line.endswith(":")]


def _service_volumes(lines: list[str]) -> list[str]:
    """Items directly under the twitch-mcp service's ``volumes:``; nested long-form fields are skipped."""
    volumes = _block(_block(_block(lines, "services:", 0), "twitch-mcp:", 2), "volumes:", 4)
    if not volumes:
        return []
    item_indent = _indent(volumes[0])
    return [
        line.strip()[2:]
        for line in volumes
        if _indent(line) == item_indent and line.strip().startswith("- ")
    ]


def test_the_container_runs_as_the_documented_unprivileged_uid(dockerfile: str) -> None:
    """R2: UID/GID 10001, created system-only, with no home and no login shell."""
    assert re.search(r"groupadd --system --gid 10001", dockerfile), "the group is created first"
    assert re.search(r"useradd --system --uid 10001 --gid 10001", dockerfile)
    assert "--no-create-home" in dockerfile and "--home-dir /nonexistent" in dockerfile
    assert re.search(r"^USER 10001:10001$", dockerfile, re.MULTILINE), (
        "the process drops privileges"
    )
    assert not re.search(r"^USER root$", dockerfile, re.MULTILINE), "nothing runs as root"


def test_the_data_directory_belongs_to_the_service_user(dockerfile: str) -> None:
    """R48: the OAuth token store is the one thing on disk, so its owner has to be the service uid."""
    assert re.search(r"chown 10001:10001 /data", dockerfile)
    assert re.search(r"EXPOSE 8000", dockerfile), "the container-internal port __main__ binds"


def test_the_image_starts_the_same_entry_point_the_tests_cover(dockerfile: str) -> None:
    """The container runs ``python -m twitch_mcp`` — the module tests/test_main.py pins down."""
    assert 'CMD ["python", "-m", "twitch_mcp"]' in dockerfile
    assert "healthz" in dockerfile, "R3/R50: the image declares its own healthcheck"


def test_compose_publishes_on_host_loopback_only(compose: str) -> None:
    """R1: the single most important line in the repository. Never a wildcard bind.

    The MCP bearer token authorises writes; a ``0.0.0.0`` or bare ``8770:8000`` mapping would put it
    on the LAN, and the Host/Origin guard (R5) is a second line of defence, not a substitute.
    """
    mapping = re.search(r'"(?P<bind>[^"]*:8000)"', compose)
    assert mapping is not None, "the port mapping is missing"
    bind = mapping.group("bind")
    assert bind.startswith("127.0.0.1:"), f"R1 requires a loopback bind, found {bind!r}"
    assert "0.0.0.0" not in bind and not bind.startswith("8"), "no wildcard or bare mapping"
    assert "8770" in bind, "the documented host port"


def test_compose_applies_every_hardening_switch(compose: str) -> None:
    """R2: read-only rootfs, all capabilities dropped, no new privileges, and bounded resources."""
    assert re.search(r"read_only:\s*true", compose)
    assert re.search(r"cap_drop:\s*\[ALL\]", compose), "no Linux capabilities at all"
    assert re.search(r"no-new-privileges:true", compose)
    assert re.search(r"init:\s*true", compose), "PID 1 reaping, so SIGTERM reaches the app (R33)"
    assert re.search(r"pids_limit:\s*\d+", compose)
    assert re.search(r"mem_limit:\s*\d+m", compose)
    assert re.search(r"tmpfs:\s*\n\s*-\s*/tmp:size=", compose), "writable only where it must be"


def test_compose_mounts_no_host_directory_by_default(compose: str) -> None:
    """R48: the base compose file declares no volumes and mounts nothing. Only the opt-in oauth file
    adds the named volume that holds the token store."""
    lines = _lines(compose)
    assert "volumes" not in _top_keys(lines), "the base file declares no top-level volumes"
    assert not any(re.match(r"\s*volumes:\s*\S", line) for line in lines), (
        "a flow-style volumes value would hide a mount from the block parser"
    )
    assert _service_volumes(lines) == [], "the default deployment must mount nothing"


def test_oauth_override_declares_the_named_volume(oauth_compose: str) -> None:
    """V1: the override declares the top-level named volume ``twitch-mcp-data`` with that exact name."""
    lines = _lines(oauth_compose)
    assert "volumes" in _top_keys(lines), "the override declares a top-level volumes key"
    top_volumes = _block(lines, "volumes:", 0)
    named = _block(top_volumes, "twitch-mcp-data:", 2)
    assert "    name: twitch-mcp-data" in named, (
        "the name is set directly under the twitch-mcp-data key, so it does not depend on the project name"
    )


def test_oauth_override_mounts_only_the_named_volume(oauth_compose: str) -> None:
    """V1, V2: the service mounts exactly the named volume at /data. A host path, a ./ or / source, or a
    long-form ``type: bind`` entry makes the list differ."""
    assert _service_volumes(_lines(oauth_compose)) == ["twitch-mcp-data:/data"]
