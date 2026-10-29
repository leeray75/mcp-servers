"""Tool-level error model (R30-R31).

Every failure that a tool can report is a :class:`DiscordMcpError`. The server turns it into a
``CallToolResult`` with ``isError: true`` and the structured shape defined in R30. Messages are
written by this package and never contain tokens, URLs, request arguments or Discord response
bodies.
"""

from __future__ import annotations

from typing import Any, Final, Literal, get_args

ErrorKind = Literal[
    "validation",
    "forbidden_guild",
    "permission",
    "not_found",
    "rate_limited",
    "index_pending",
    "auth",
    "upstream",
    "unavailable",
]

ERROR_KINDS: Final[frozenset[str]] = frozenset(get_args(ErrorKind))

DEFAULT_HINTS: Final[dict[str, str]] = {
    "validation": "Check the argument names, types and bounds in the tool's input schema.",
    "forbidden_guild": "Use list_guilds to see the guilds this server may access.",
    "permission": "Grant the missing permission to the bot's role in that channel or guild, "
    "or choose a different channel.",
    "not_found": "Check the id; call list_channels or list_guilds to find valid ids.",
    "rate_limited": "Wait retry_after seconds before retrying; do not retry sooner.",
    "index_pending": "The Discord search index is still building; retry later.",
    "auth": "The bot token was rejected; rotate it in the Developer Portal, update .env and "
    "run scripts/restart.sh --recreate.",
    "upstream": "Discord returned an error; retry later if retryable is true.",
    "unavailable": "Discord could not be reached; retry later.",
}


class DiscordMcpError(Exception):
    """A failure reported to the calling agent as a tool execution error."""

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        code: int | None = None,
        http_status: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.message = message
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.retry_after = retry_after
        self.hint = hint if hint is not None else DEFAULT_HINTS[kind]

    def to_text(self) -> str:
        """The single text block of an error result: ``<kind>: <message> Hint: <hint>``."""
        return f"{self.kind}: {self.message} Hint: {self.hint}"

    def to_payload(self) -> dict[str, Any]:
        """The ``structuredContent`` of an error result (R30)."""
        return {
            "error": {
                "kind": self.kind,
                "message": self.message,
                "code": self.code,
                "http_status": self.http_status,
                "retryable": self.retryable,
                "retry_after": self.retry_after,
                "hint": self.hint,
            }
        }
