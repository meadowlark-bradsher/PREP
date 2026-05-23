"""Region selector contract.

The PM doc treats selection as the load-bearing pluggable component:
swap implementations without touching the rest of the application. The
contract is intentionally narrow.

CONTRACT:
  - Input: a parsed Diff and an optional SelectorContext.
  - Output: an ordered list of Regions, each with a structural label,
    the underlying hunk, and a selector-specific rationale payload.
  - Side-effect free. The selector does not persist anything.

The PM doc also calls for 2–4 regions per diff in production; the
Protocol itself does not enforce that bound — different selectors may
have reasons to return more or fewer, and the orchestration layer is
free to truncate. Keeping the Protocol unbounded preserves future
selectors that, e.g., rank an entire candidate set for downstream
filtering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..domain.diff import Diff
from ..domain.region import Region


@dataclass(frozen=True)
class SelectorContext:
    engineer_identifier: str | None = None


@runtime_checkable
class RegionSelector(Protocol):
    name: str
    version: str

    def select(
        self,
        diff: Diff,
        *,
        context: SelectorContext | None = None,
    ) -> list[Region]: ...
