"""Errors raised by session orchestration.

These are distinct from generic value errors so callers (eventually the
web layer) can map them to HTTP statuses without inspecting messages.
"""

from __future__ import annotations


class SessionError(Exception):
    """Base class for orchestration errors."""


class SessionNotFound(SessionError):
    pass


class RegionNotFound(SessionError):
    pass


class InvalidPhaseTransition(SessionError):
    """The session is not in the phase required for this operation."""


class InvalidRegionStatus(SessionError):
    """The region is not in the status required for this operation."""


class HypothesisAlreadyLocked(SessionError):
    """Attempted to edit a hypothesis after the reveal."""
