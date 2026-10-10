"""Errors whose messages are safe to show to the calling agent (no secrets, no file contents)."""

from __future__ import annotations


class VisionMcpError(Exception):
    """Base class: the message is returned to the agent as a tool error."""


class ImageAccessError(VisionMcpError):
    """The requested image path is invalid, outside the allowed folder, or unreadable."""


class ImageFormatError(VisionMcpError):
    """The file is not a supported, valid image or is too large."""


class BackendError(VisionMcpError):
    """The vision model backend could not be reached or returned an unusable answer."""
