"""Errors whose messages are safe to show to the calling agent (no secrets, no upstream bodies)."""

from __future__ import annotations


class SportsMcpError(Exception):
    """Base class: the message is returned to the agent as a tool error."""


class InvalidInputError(SportsMcpError):
    """A tool argument is outside what the server accepts (unknown league, bad date, ...)."""


class NotFoundError(SportsMcpError):
    """ESPN has no such object (unknown game, team, ...)."""


class UpstreamError(SportsMcpError):
    """ESPN could not be reached or returned something unusable."""
