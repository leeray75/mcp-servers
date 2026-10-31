"""Tool-level error model (R43/R44).

Every failure that a tool can report is a :class:`TwitchMcpError`. The server turns it into a
``CallToolResult`` with ``isError: true`` carrying exactly the seven fields of R43. Messages are
written by this package and never contain tokens, URLs, request arguments or Twitch response
bodies.

The ``kind`` taxonomy (R44: derived from the HTTP status plus Twitch's own numeric/error code,
never from message text, except that a 401/403 ``message`` is read to recognise a missing scope,
C1):

===================  ==============================================================  ==========
kind                 meaning                                                          retryable
===================  ==============================================================  ==========
``permission``       authorized token, but the scope/role Twitch asked for is absent  no
``forbidden_channel``  broadcaster id not on the allowlist, or a lookup that failed   no
``not_found``        a read for an object Twitch says does not exist (Helix 404)       no
``not_authorized``   write target is neither the operator's channel nor verified      no
``confirmation_required``  first turn of a write: plan + single-use token minted      no
``stale_confirmation``  token replayed, expired or bound to different arguments       no
``rate_limited``     bucket parked by a 429, or a wait beyond the in-call limit       yes
``unauthenticated``  the process-wide 401 circuit is open (R24)                       no
``too_large``        streamed body exceeded ``TWITCH_MAX_RESPONSE_BYTES`` (R14)       no
``unknown_outcome``  a write whose response was lost; never re-issued (R16)           no
``disabled``         the write group is off, or the tool is explicitly disabled       no
``invalid_arguments``  unknown tool, unknown argument or schema violation (R39)       no
``upstream``         Twitch returned an error this taxonomy does not special-case     usually
===================  ==============================================================  ==========
"""

from __future__ import annotations

from typing import Any, Final, Literal, get_args

ErrorKind = Literal[
    "permission",
    "forbidden_channel",
    "not_found",
    "not_authorized",
    "confirmation_required",
    "stale_confirmation",
    "rate_limited",
    "unauthenticated",
    "too_large",
    "unknown_outcome",
    "disabled",
    "invalid_arguments",
    "upstream",
]

ERROR_KINDS: Final[frozenset[str]] = frozenset(get_args(ErrorKind))

#: Kinds that guard/lookup failures may never be confused with (R27). Anything outside this set
#: is collapsed into the uniform denial so the response cannot probe allowlist membership.
PASS_THROUGH_KINDS: Final[frozenset[str]] = frozenset(
    {"rate_limited", "unauthenticated", "upstream"}
)

DEFAULT_HINTS: Final[dict[str, str]] = {
    "permission": "The Twitch token lacks a required scope; see the hint for the scope Twitch "
    "named and the scopes this token holds. Sign in again with connect_twitch_account to grant it.",
    "forbidden_channel": "Add the broadcaster id to TWITCH_ALLOWED_BROADCASTER_IDS in .env and "
    "run scripts/restart.sh --recreate.",
    "not_found": "Twitch has no such user, channel, video, tag or segment for this request. "
    "Check the id or login and, for a lookup by login, that the account still exists.",
    "not_authorized": "Writes are allowed on the operator's own channel, or on a channel where "
    "Twitch's own moderator/editor list shows the operator. Verify your role in that channel.",
    "confirmation_required": "Review the plan, then call the same tool again with the confirm "
    "token. The token is single-use and expires with the confirm TTL.",
    "stale_confirmation": "The confirm token was already used, has expired, or the arguments "
    "changed. Call the tool again without confirm to mint a fresh one.",
    "rate_limited": "Wait retry_after seconds before retrying; do not retry sooner.",
    "unauthenticated": "Twitch rejected the token and this process stopped sending traffic. "
    "Check the credentials in .env, re-authorize if needed, and restart the container.",
    "too_large": "Lower the 'first' argument so the page fits TWITCH_MAX_RESPONSE_BYTES.",
    "unknown_outcome": "Do not re-issue this call: it may already have taken effect. Check the "
    "channel in the Twitch console or chat, and re-issue only if the effect is absent.",
    "disabled": "Enable the tool: TWITCH_ENABLE_WRITES=true plus its group in "
    "TWITCH_WRITE_GROUPS, or remove its TWITCH_DISABLE_<TOOL> override.",
    "invalid_arguments": "Check the argument names, types and bounds in the tool's input schema.",
    "upstream": "Twitch returned an error; retry later if retryable is true.",
}


class TwitchMcpError(Exception):
    """A failure reported to the calling agent as an ``isError`` tool result (R43)."""

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
        self.hint: str = DEFAULT_HINTS[kind] if hint is None else hint

    def to_text(self) -> str:
        """The single text block of an error result: ``<kind>: <message> Hint: <hint>``."""
        return f"{self.kind}: {self.message} Hint: {self.hint}"

    def to_payload(self) -> dict[str, Any]:
        """The ``structuredContent`` of an error result (R43): seven keys under ``error``."""
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
