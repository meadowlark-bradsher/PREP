"""Region selector contract.

The PM doc treats selection as the load-bearing pluggable component:
swap implementations without touching the rest of the application. The
contract is intentionally narrow.

CONTRACT:
  - Input: the candidate content set from a ContentSource, and an
    optional SelectorContext.
  - Output: an ordered list of Regions, each with a structural label,
    the underlying content, and a selector-specific rationale payload.
  - Side-effect free. The selector does not persist anything.

Selectors take `list[RegionContent]` rather than a parsed `Diff` because
the diff is now one producer among several (see `content_sources`). A
selector that needs diff geometry reads it from `RegionContent.metadata`,
which keeps the seam honest: whatever a selector can see, it can see for
every kind of content, or it must handle its absence.

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

from ..domain.content import RegionContent
from ..domain.region import Region
from ..storage.models import EngagementThreshold


@dataclass(frozen=True)
class SelectorContext:
    engineer_identifier: str | None = None
    engagement_threshold: EngagementThreshold = EngagementThreshold.DEFAULT
    # The load type this session is ordering by, resolved at launch from
    # the user's choice or the manifest's default. None for sources that
    # declare no criteria, which is every diff session.
    criterion: str | None = None


@runtime_checkable
class RegionSelector(Protocol):
    name: str
    version: str

    def select(
        self,
        contents: list[RegionContent],
        *,
        context: SelectorContext | None = None,
    ) -> list[Region]: ...
