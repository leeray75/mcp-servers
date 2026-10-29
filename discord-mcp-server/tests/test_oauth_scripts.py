"""A3 fix round (m9): the compose selection helper, the override file and .gitignore (U30-U31).

The shell helper is exercised for real: ``scripts/lib/common.sh`` is copied into a temporary project
with its own ``.env``, and a stub ``docker`` on PATH prints the arguments it receives. The repository's
real ``.env`` is never read. Skipped where ``bash`` is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMMON = REPO / "scripts" / "lib" / "common.sh"
OVERRIDE = REPO / "docker-compose.oauth.yml"
needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def compose_args(tmp_path: Path, env_text: str | None) -> list[str]:
    """The docker arguments that ``compose config`` produces for a temporary project."""
    project = tmp_path / "project"
    (project / "scripts" / "lib").mkdir(parents=True)
    shutil.copy(COMMON, project / "scripts" / "lib" / "common.sh")
    if env_text is not None:
        (project / ".env").write_text(env_text, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    stub.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    script = 'source "$1"; compose config'
    result = subprocess.run(  # noqa: S603, S607 - fixed argv for the repo helper under test
        ["bash", "-c", script, "bash", str(project / "scripts" / "lib" / "common.sh")],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout.split()


@needs_bash
@pytest.mark.parametrize("flag", ["true", "TRUE", "True"])
def test_user_oauth_on_adds_both_compose_files(tmp_path: Path, flag: str) -> None:
    args = compose_args(tmp_path, f"DISCORD_USER_OAUTH={flag}\n")
    assert args.count("-f") == 2
    assert any(arg.endswith("docker-compose.yml") for arg in args)
    assert any(arg.endswith("docker-compose.oauth.yml") for arg in args)
    assert args[-1] == "config"


@needs_bash
@pytest.mark.parametrize("env_text", ["DISCORD_USER_OAUTH=false\n", "MCP_AUTH_TOKEN=x\n", None])
def test_user_oauth_off_or_missing_uses_the_base_file_only(
    tmp_path: Path, env_text: str | None
) -> None:
    args = compose_args(tmp_path, env_text)
    assert "-f" not in args
    assert args[-1] == "config"


def test_the_override_mounts_only_the_token_folder_and_sets_the_token_path() -> None:
    """U30: exactly one mount (./data) and the token file variable, nothing else."""
    text = OVERRIDE.read_text(encoding="utf-8")
    assert "- ./data:/data" in text
    assert "DISCORD_OAUTH_TOKEN_FILE: /data/discord_user_token.json" in text
    assert text.count("- ./") == 1


def test_gitignore_excludes_the_token_folder() -> None:
    """U31: the token folder is git-ignored."""
    lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/" in lines


def test_the_base_compose_file_is_unchanged_by_the_feature() -> None:
    """U30: docker-compose.yml mounts no host folders."""
    assert "volumes:" not in (REPO / "docker-compose.yml").read_text(encoding="utf-8")
