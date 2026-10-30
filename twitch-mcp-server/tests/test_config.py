"""R6-R10: sole env reader, field-named secret-free errors, base-URL pinning, ranges.

Also asserts the sibling invariants that belong to the config layer: the only two hosts ever
contacted (R12 host pinning) and the redirects-never-followed client construction (R12).
"""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from tests.twitch_fakes import (
    BROADCASTER,
    FAKE_AUTH_TOKEN,
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    OTHER_BROADCASTER,
    make_settings,
    set_env,
)
from twitch_mcp.config import (
    DEFAULT_API_BASE,
    DEFAULT_AUTH_BASE,
    DEFAULT_HOST_PORT,
    ConfigError,
    load_settings,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "twitch_mcp"


# ------------------------------------------------------------------ R6: sole env reader


ENV_READ_ATTRS = frozenset({"environ", "getenv"})


def _env_reads_in(tree: ast.AST) -> list[str]:
    """Precise R6 violations in one module's syntax tree.

    Flags ``import os`` / ``from os import ...`` (os is imported only to read the env), plus
    any ``os.environ`` / ``os.getenv`` / ``sys.environ`` / ``sys.getenv`` access. A bare
    ``import sys`` is legitimate (``__main__.py`` uses ``sys.exit``) and never fires.
    """
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "os" for alias in node.names):
                hits.append(f"import os (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "os":
                hits.append(f"from {node.module} import ... (line {node.lineno})")
        elif isinstance(node, ast.Attribute):
            base = node.value
            if (
                isinstance(base, ast.Name)
                and base.id in {"os", "sys"}
                and node.attr in ENV_READ_ATTRS
            ):
                hits.append(f"{base.id}.{node.attr} (line {node.lineno})")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            # No hiding behind a call: os.getenv("X").lower() still counts as an env read.
            base = node.func.value
            if (
                isinstance(base, ast.Name)
                and base.id in {"os", "sys"}
                and node.func.attr in ENV_READ_ATTRS
            ):
                hits.append(f"{base.id}.{node.func.attr}() (line {node.lineno})")
    return hits


def test_only_config_reads_the_environment() -> None:
    """R6: config.py is the only module that touches the process environment.

    Detected precisely with the ast module — imports of ``os``, ``os.environ``/``os.getenv``
    and direct ``sys.environ``/``sys.getenv`` access — never by a substring scan: the English
    word "environment" legitimately appears in docstrings of ``__main__.py`` and ``server.py``,
    which is not a violation.
    """
    offenders: dict[str, list[str]] = {}
    for path in sorted(SRC.glob("*.py")):
        if path.name == "config.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits = _env_reads_in(tree)
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"R6 violation — modules reading the environment: {offenders}"


def test_r6_detector_catches_a_real_environ_read() -> None:
    """The detector is proven to fire: a real os.environ read in a non-config module is caught."""
    caught = _env_reads_in(
        ast.parse("import os\n\nVALUE = os.environ['TWITCH_CLIENT_ID']\nOTHER = os.getenv('X')\n")
    )
    # The detector reports a call expression twice: once as the bare ``os.getenv`` Attribute and
    # once as the ``os.getenv()`` Call (``ast.walk`` reaches the Attribute before the Call, so the
    # () hit leads). The R6 guarantee is that the read is *caught*, not that it is reported once.
    assert len(caught) == 4, caught  # import os + os.environ + os.getenv as attr + os.getenv()
    # `import sys` alone is legitimate; sys.environ is still caught.
    assert _env_reads_in(ast.parse("import sys\n")) == []
    caught_sys = _env_reads_in(ast.parse("import sys\n\nVALUE = sys.environ['X']\n"))
    assert caught_sys == ["sys.environ (line 3)"], caught_sys
    # A bare `import os` is itself an R6 violation: os is imported only to read the env.
    assert _env_reads_in(ast.parse("import os\n")) == ["import os (line 1)"]
    assert _env_reads_in(ast.parse("from os import getenv\n")) == ["from os import ... (line 1)"]
    # Ordinary English and unrelated code do NOT fire — the old substring scan did.
    clean = _env_reads_in(
        ast.parse(
            '"""The process environment is configured in config.py only (R6). """\n'
            "import sys\n\n"
            "def usage() -> str:\n"
            "    return 'configure it through the environment'\n"
            "sys.argv = []\n"
        )
    )
    assert clean == []
    # The call form nested in an expression is caught too (no hiding behind .lower()).
    assert _env_reads_in(ast.parse("import os\n\nX = os.getenv('Y').lower()\n")) == [
        "import os (line 1)",
        "os.getenv() (line 3)",
        "os.getenv (line 3)",
    ]


def test_r6_detector_fires_on_an_injected_module(tmp_path: Path) -> None:
    """End-to-end: a planted module with a real os.environ read is caught by the same scan.

    The guard globs ``src/twitch_mcp/*.py``; here the real modules are copied to a temp tree,
    a violator is planted beside them, and the identical per-module scan must flag exactly it.
    """
    fake_src = tmp_path / "twitch_mcp"
    fake_src.mkdir()
    for module in SRC.glob("*.py"):
        (fake_src / module.name).write_text(module.read_text(encoding="utf-8"), encoding="utf-8")
    (fake_src / "leaky.py").write_text(
        "import os\n\nSECRET = os.environ['TWITCH_CLIENT_SECRET']\n", encoding="utf-8"
    )
    offenders: dict[str, list[str]] = {}
    for path in sorted(fake_src.glob("*.py")):
        if path.name == "config.py":
            continue
        hits = _env_reads_in(ast.parse(path.read_text(encoding="utf-8")))
        if hits:
            offenders[path.name] = hits
    assert offenders == {"leaky.py": ["import os (line 1)", "os.environ (line 3)"]}, offenders


def test_no_module_outside_config_names_environ_in_code() -> None:
    """Extra guard: the sentinel-substring check restricted to CODE (not docstrings/comments).

    Comments and docstrings may legitimately say "environment"; a line of code outside
    config.py may not name ``environ`` at all.
    """
    offenders: list[str] = []
    for path in sorted(SRC.glob("*.py")):
        if path.name == "config.py":
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        string_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                end_lineno = node.end_lineno if node.end_lineno is not None else node.lineno
                string_lines.update(range(node.lineno, end_lineno + 1))
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or lineno in string_lines:
                continue
            if "environ" in line:
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders


def test_config_imports_pydantic_settings() -> None:
    """R6: config.py uses pydantic-settings, as the spec requires."""
    source = (SRC / "config.py").read_text(encoding="utf-8")
    assert "pydantic_settings" in source
    assert "BaseSettings" in source


def test_valid_environment_loads_with_documented_defaults() -> None:
    settings = make_settings()
    assert settings.twitch_api_base == DEFAULT_API_BASE
    assert settings.twitch_auth_base == DEFAULT_AUTH_BASE
    assert settings.host_port == DEFAULT_HOST_PORT == 8770
    assert settings.twitch_user_oauth is False  # R46
    assert settings.twitch_enable_writes is False  # R28
    assert settings.twitch_write_groups == ""
    assert settings.writes_enabled is False
    assert settings.twitch_timeout_seconds == 10.0
    assert settings.twitch_max_5xx_retries == 2
    assert settings.twitch_confirm_token_ttl_seconds == 300
    assert settings.twitch_identity_ttl_seconds == 300
    assert settings.log_level == "INFO"
    assert settings.cache_max_bytes == settings.cache_max_mb * 1_048_576


# ------------------------------------------------------------------ R7: required + placeholder


@pytest.mark.parametrize(
    "field",
    [
        "MCP_AUTH_TOKEN",
        "TWITCH_CLIENT_ID",
        "TWITCH_CLIENT_SECRET",
        "TWITCH_ALLOWED_BROADCASTER_IDS",
    ],
)
def test_required_fields_are_named_when_missing(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    set_env(monkeypatch)
    monkeypatch.delenv(field)
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert field in str(info.value)


@pytest.mark.parametrize("field", ["TWITCH_CLIENT_SECRET", "TWITCH_CLIENT_ID", "MCP_AUTH_TOKEN"])
def test_replace_me_placeholders_abort_startup(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    """R7: a `replace-me*` value is a startup failure, not a warning."""
    set_env(monkeypatch, **{field: "replace-me-with-your-twitch-app-credential"})
    with pytest.raises(ConfigError) as info:
        load_settings()
    message = str(info.value)
    assert field in message
    assert "replace-me" not in message  # R6: the placeholder value is not echoed back


@pytest.mark.parametrize("placeholder", ["replace-me", "REPLACE-ME-XYZ", " replace-me-abc "])
def test_placeholder_check_is_case_and_space_tolerant(placeholder: str) -> None:
    with pytest.raises(Exception) as info:
        make_settings(twitch_client_secret=placeholder)
    assert "TWITCH_CLIENT_SECRET" in str(info.value)


# ------------------------------------------------------------------ R6: never echo a value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("MCP_AUTH_TOKEN", "short-but-still-secret-token"),
        ("MCP_AUTH_TOKEN", "   "),
        ("TWITCH_CLIENT_SECRET", "   "),
        ("TWITCH_ALLOWED_BROADCASTER_IDS", "12a456789"),
        ("TWITCH_ALLOWED_BROADCASTER_IDS", f"{BROADCASTER},,"),
        ("TWITCH_ALLOWED_BROADCASTER_IDS", f"{BROADCASTER},{BROADCASTER}"),
        ("TWITCH_ALLOWED_BROADCASTER_IDS", "123456789012345678901"),  # 21 digits
        ("TWITCH_ALLOWED_BROADCASTER_IDS", ",".join(str(10**8 + i) for i in range(101))),
        ("TWITCH_API_BASE", "https://evil.example.com/helix"),
        ("TWITCH_API_BASE", "http://api.twitch.tv/helix"),
        ("TWITCH_API_BASE", "https://api.twitch.tv:8443/helix"),
        ("TWITCH_API_BASE", "https://api.twitch.tv/v2"),
        ("TWITCH_API_BASE", "https://user:pw@api.twitch.tv/helix"),
        ("TWITCH_API_BASE", "https://api.twitch.tv/helix?token=secretpair"),
        ("TWITCH_AUTH_BASE", "https://evil.example.com"),
        ("TWITCH_AUTH_BASE", "https://id.twitch.tv.evil.example/"),
        ("TWITCH_AUTH_BASE", "http://id.twitch.tv"),
        ("TWITCH_TIMEOUT_SECONDS", "0"),
        ("TWITCH_TIMEOUT_SECONDS", "61"),
        ("TWITCH_MAX_RESPONSE_BYTES", "65535"),
        ("TWITCH_MAX_RESPONSE_BYTES", "16777217"),
        ("TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS", "-1"),
        ("TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS", "121"),
        ("TWITCH_MAX_429_RETRIES", "11"),
        ("TWITCH_MAX_5XX_RETRIES", "99"),
        ("TWITCH_CONFIRM_TOKEN_TTL_SECONDS", "29"),
        ("TWITCH_CONFIRM_TOKEN_TTL_SECONDS", "3601"),
        ("TWITCH_IDENTITY_TTL_SECONDS", "-1"),
        ("TTL_METADATA_SECONDS", "3601"),
        ("CACHE_MAX_ENTRIES", "7"),
        ("CACHE_MAX_MB", "0"),
        ("HOST_PORT", "0"),
        ("LOG_LEVEL", "LOUD"),
        ("TWITCH_WRITE_GROUPS", "Polls,,chat"),
        ("TWITCH_WRITE_GROUPS", "not a group name"),
        ("TWITCH_OAUTH_SCOPES", "channel:manage:broadcast;curl evil"),
        ("TWITCH_OAUTH_TOKEN_FILE", "relative/path.json"),
    ],
)
def test_invalid_values_are_rejected_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    """R6 + R10: the field is named, the value is never echoed — secrets and allowlist included."""
    set_env(monkeypatch, **{field: value})
    with pytest.raises(ConfigError) as info:
        load_settings()
    message = str(info.value)
    assert field in message
    if value.strip():
        assert value not in message


def test_allowlist_value_is_never_echoed_even_when_it_looks_harmless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6 explicitly covers the allowlist: a rejected id must not come back in the message."""
    secret_id = "999999999999999999999"  # 21 digits, so it is a value, not a category
    set_env(monkeypatch, TWITCH_ALLOWED_BROADCASTER_IDS=secret_id)
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert secret_id not in str(info.value)
    assert "TWITCH_ALLOWED_BROADCASTER_IDS" in str(info.value)


# ------------------------------------------------------------------ R8: allowlist


def test_empty_allowlist_aborts_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """R8: the server cannot run with an empty allowlist."""
    set_env(monkeypatch, TWITCH_ALLOWED_BROADCASTER_IDS="")
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert "TWITCH_ALLOWED_BROADCASTER_IDS" in str(info.value)


@pytest.mark.parametrize("blank", [" ", ",,,", " , ", f"{BROADCASTER}, "])
def test_blank_entries_are_not_an_allowlist(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    set_env(monkeypatch, TWITCH_ALLOWED_BROADCASTER_IDS=blank)
    with pytest.raises(ConfigError):
        load_settings()


def test_allowlist_is_normalised_and_split() -> None:
    settings = make_settings(
        twitch_allowed_broadcaster_ids=f" {BROADCASTER} , {OTHER_BROADCASTER} "
    )
    assert settings.twitch_allowed_broadcaster_ids == f"{BROADCASTER},{OTHER_BROADCASTER}"
    assert settings.broadcaster_ids == frozenset({BROADCASTER, OTHER_BROADCASTER})


def test_allowlist_accepts_one_and_a_hundred_ids() -> None:
    assert len(make_settings(twitch_allowed_broadcaster_ids=BROADCASTER).broadcaster_ids) == 1
    hundred = ",".join(str(10**8 + i) for i in range(100))
    assert len(make_settings(twitch_allowed_broadcaster_ids=hundred).broadcaster_ids) == 100


@pytest.mark.parametrize("digits", ["1", "12345678901234567890"])
def test_allowlist_accepts_one_to_twenty_digits(digits: str) -> None:
    assert make_settings(twitch_allowed_broadcaster_ids=digits).broadcaster_ids == {digits}


# ------------------------------------------------------------------ R9: base URL pinning


def test_bases_default_to_exactly_the_twitch_hosts() -> None:
    """R9: both bases are pinned to the documented HTTPS Twitch origins, and the hosts derive from them."""
    settings = make_settings()
    assert settings.twitch_api_base == "https://api.twitch.tv/helix"
    assert settings.twitch_auth_base == "https://id.twitch.tv"
    assert (settings.api_host, settings.auth_host) == ("api.twitch.tv", "id.twitch.tv")


@pytest.mark.parametrize(
    "trailing", ["https://api.twitch.tv/helix/", "https://api.twitch.tv/helix//"]
)
def test_trailing_slash_is_normalised(trailing: str) -> None:
    assert make_settings(twitch_api_base=trailing).twitch_api_base == DEFAULT_API_BASE


def test_authority_case_is_normalised_and_query_free() -> None:
    settings = make_settings(twitch_api_base="https://API.Twitch.TV/helix")
    assert settings.twitch_api_base == DEFAULT_API_BASE
    assert "?" not in settings.twitch_api_base


# ------------------------------------------------------------------ R10: numeric ranges


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("TWITCH_TIMEOUT_SECONDS", "1", 1.0),
        ("TWITCH_TIMEOUT_SECONDS", "60", 60.0),
        ("TWITCH_MAX_RESPONSE_BYTES", "65536", 65_536),
        ("TWITCH_MAX_RESPONSE_BYTES", "16777216", 16_777_216),
        ("TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS", "0", 0.0),
        ("TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS", "120", 120.0),
        ("TWITCH_MAX_429_RETRIES", "0", 0),
        ("TWITCH_MAX_429_RETRIES", "10", 10),
        ("TWITCH_MAX_5XX_RETRIES", "10", 10),
        ("TWITCH_CONFIRM_TOKEN_TTL_SECONDS", "30", 30),
        ("TWITCH_CONFIRM_TOKEN_TTL_SECONDS", "3600", 3600),
        ("TWITCH_IDENTITY_TTL_SECONDS", "0", 0),
        ("TTL_METADATA_SECONDS", "0", 0),
        ("CACHE_MAX_ENTRIES", "10000", 10_000),
        ("CACHE_MAX_MB", "400", 400),
        ("HOST_PORT", "8770", 8770),
    ],
)
def test_numeric_boundaries_are_inclusive(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str, expected: object
) -> None:
    """R10: every documented bound is accepted at both ends; the case above rejects just outside."""
    set_env(monkeypatch, **{field: value})
    settings = load_settings()
    assert getattr(settings, field.lower()) == expected


def test_write_groups_and_writes_enabled_need_both_switches() -> None:
    """R28's two kill switches, as read by config (registration itself is capabilities')."""
    assert make_settings(twitch_enable_writes=True).writes_enabled is False
    assert make_settings(twitch_write_groups="polls").writes_enabled is False
    both = make_settings(twitch_enable_writes=True, twitch_write_groups="polls,chat")
    assert both.writes_enabled is True
    assert both.write_groups == frozenset({"polls", "chat"})


def test_oauth_scopes_are_split_and_default_minimal() -> None:
    assert make_settings().oauth_scope_list == ["user:read:email"]
    assert make_settings(
        twitch_oauth_scopes="channel:manage:broadcast  user:read:chat"
    ).oauth_scope_list == ["channel:manage:broadcast", "user:read:chat"]


def test_log_level_is_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, LOG_LEVEL="  debug ")
    assert load_settings().log_level == "DEBUG"


def test_derived_oauth_urls_use_the_host_port() -> None:
    settings = make_settings(host_port=8770)
    assert settings.redirect_uri == "http://127.0.0.1:8770/oauth/callback"
    assert settings.sign_in_url == "http://127.0.0.1:8770/oauth/start"


def test_secrets_are_wrapped_so_str_repr_hides_them() -> None:
    settings = make_settings()
    assert FAKE_AUTH_TOKEN not in str(settings)
    assert FAKE_CLIENT_SECRET not in repr(settings)
    assert FAKE_CLIENT_ID not in str(settings)
    assert settings.mcp_auth_token.get_secret_value() == FAKE_AUTH_TOKEN
    assert settings.twitch_client_id.get_secret_value() == FAKE_CLIENT_ID


# ------------------------------------------------------------------ R12: host pinning at build


async def test_redirect_guard_blocks_a_hopping_client() -> None:
    """R12: even a client that explicitly asks for redirects cannot hop under this test suite.

    ``conftest._no_silent_redirects`` patches httpx's redirect plumbing off, so the only reachable
    behaviour is "the 3xx is an error". The real client must additionally be built with
    ``follow_redirects=False`` — asserted against the concrete client in ``test_twitch_client``.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.twitch.tv",
        follow_redirects=True,
    ) as client:
        with pytest.raises(AssertionError, match="redirects must never be followed"):
            await client.get("/hop")
    # httpx refuses the redirect before the hop is ever sent, so exactly the original request ran.
    assert seen == []


# ------------------------------------------------------------------ S2: retired budget variables


def test_the_retired_budget_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """S2: the per-minute budget is gone. The variables still boot the process and are not read."""
    set_env(
        monkeypatch,
        TOOL_CALLS_PER_MINUTE="5",
        TOOL_CALLS_PER_MINUTE_PER_TOOL="2",
    )
    settings = load_settings()
    assert not hasattr(settings, "tool_calls_per_minute")
    assert not hasattr(settings, "tool_calls_per_minute_per_tool")
