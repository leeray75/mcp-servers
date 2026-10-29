"""Intent gating and the fixed tool registry (R5-R8, D6, A1-R5, A1-R6, A1-R7).

Privileged intents are read from the application's ``flags`` integer (or ``flags_new``) at boot.
A tool whose intent is off is not registered; a tool with an intent the server cannot determine is
omitted with ``flags_missing`` unless forced. The ``DISCORD_FORCE_*`` settings register the tool
regardless of the detected state. ``DISCORD_ENABLE_*=false`` always wins. The tool set is fixed
for the process lifetime. The planner is pure so the boot log (R7) and the tests share one decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

from discord_mcp.config import Settings
from discord_mcp.discord_client import BootProbe

MESSAGE_CONTENT_BITS: Final = (1 << 18) | (1 << 19)
GUILD_MEMBERS_BITS: Final = (1 << 14) | (1 << 15)

Intent = Literal["message_content", "guild_members"]
OmitReason = Literal["intent_disabled", "flags_missing", "config_disabled", "probe_failed"]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Registration facts for one tool (R8: the order below is the registration order)."""

    name: str
    intent: Intent | None = None
    config_flag: str | None = None
    force_flag: str | None = None


TOOL_SPECS: Final[tuple[ToolSpec, ...]] = (
    ToolSpec("list_guilds"),
    ToolSpec("get_guild"),
    ToolSpec("list_channels"),
    ToolSpec("get_channel"),
    ToolSpec("list_messages"),
    ToolSpec("get_message"),
    ToolSpec(
        "search_messages",
        intent="message_content",
        config_flag="discord_enable_search",
        force_flag="discord_force_message_content",
    ),
    ToolSpec("list_reactions"),
    ToolSpec("list_pins"),
    ToolSpec("list_threads"),
    ToolSpec(
        "list_members",
        intent="guild_members",
        config_flag="discord_enable_member_list",
        force_flag="discord_force_guild_members",
    ),
    ToolSpec("get_member"),
    ToolSpec("search_members"),
    ToolSpec("get_audit_log"),
)

ALL_TOOL_NAMES: Final[tuple[str, ...]] = tuple(spec.name for spec in TOOL_SPECS)


def intents_from_flags(flags: int) -> dict[Intent, bool]:
    """Privileged intent state from application flags (R6)."""
    return {
        "message_content": bool(flags & MESSAGE_CONTENT_BITS),
        "guild_members": bool(flags & GUILD_MEMBERS_BITS),
    }


@dataclass(frozen=True, slots=True)
class ToolPlan:
    """Which tools are registered at boot, why others are not, and what was observed (A1-R7)."""

    registered: tuple[str, ...]
    omitted: tuple[tuple[str, OmitReason], ...] = field(default=())
    forced: tuple[str, ...] = field(default=())
    flags: int | None = None
    flags_source: str | None = None


def plan_tools(settings: Settings, probe: BootProbe) -> ToolPlan:
    """Decide registration for every tool (R5-R7, A1-R5..R7).

    - Config disable wins over everything (``config_disabled``).
    - Probe ``auth``: the intent state is unknown, so every remaining tool is registered and returns
      ``kind=auth`` (A1-R5).
    - A force flag registers the tool regardless of detection and marks it ``forced`` (A1-R6).
    - Probe ``unreachable``: intent tools are omitted (``probe_failed``).
    - Probe ``ok`` without usable flags: omitted (``flags_missing``); with flags:
      ``intent_disabled`` when the intent is off.
      when the intent is off.
    """
    intents = intents_from_flags(probe.flags) if probe.flags is not None else None
    registered: list[str] = []
    omitted: list[tuple[str, OmitReason]] = []
    forced: list[str] = []
    for spec in TOOL_SPECS:
        if spec.config_flag is not None and not getattr(settings, spec.config_flag):
            omitted.append((spec.name, "config_disabled"))
            continue
        if spec.intent is None or probe.outcome == "auth":
            registered.append(spec.name)
            continue
        force = spec.force_flag is not None and bool(getattr(settings, spec.force_flag))
        if force:
            registered.append(spec.name)
            if probe.outcome == "unreachable" or intents is None or not intents[spec.intent]:
                forced.append(spec.name)
            continue
        if probe.outcome == "unreachable":
            omitted.append((spec.name, "probe_failed"))
        elif intents is None:
            omitted.append((spec.name, "flags_missing"))
        elif not intents[spec.intent]:
            omitted.append((spec.name, "intent_disabled"))
        else:
            registered.append(spec.name)
    return ToolPlan(
        registered=tuple(registered),
        omitted=tuple(omitted),
        forced=tuple(forced),
        flags=probe.flags,
        flags_source=probe.flags_source,
    )
