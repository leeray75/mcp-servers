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


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose() -> str:
    return COMPOSE.read_text(encoding="utf-8")


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
    """R48: the base compose file stores nothing. Only the opt-in oauth file may add ./data."""
    service = compose.split("services:", 1)[-1]
    assert "volumes:" not in service, (
        "the default deployment must mount nothing; the token store lives in docker-compose.oauth.yml"
    )
