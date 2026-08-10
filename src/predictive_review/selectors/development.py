"""Development/test selectors.

These are not production selectors. They exist so the rest of the
application can be exercised end-to-end before the v1 LLM-judgment
prompt is ready, and so unit tests of orchestration code do not
depend on LLM calls. The PM doc's pluggable-selector architecture
treats this as the same shape as any future production selector.
"""

from __future__ import annotations

from ..domain.diff import Diff
from ..domain.region import Region
from .base import SelectorContext


class FirstNHunksSelector:
    """Returns the first 2-4 hunks of the diff as candidate regions.

    Selection is not load-bearing here; the structural labels are
    file:line synthetic. This selector will never be used in
    production — it exists to make orchestration code runnable.
    """

    name = "first_n_hunks"
    version = "v1"

    def __init__(self, min_regions: int = 2, max_regions: int = 4) -> None:
        self._min = min_regions
        self._max = max_regions

    def select(
        self,
        diff: Diff,
        *,
        context: SelectorContext | None = None,
    ) -> list[Region]:
        n = min(self._max, max(self._min, len(diff.hunks)))
        selected = diff.hunks[: min(n, len(diff.hunks))]
        return [
            Region(
                structural_label=hunk.ref,
                content=hunk.to_content(),
                selector_rationale={"selector": self.name, "strategy": "first-N"},
            )
            for hunk in selected
        ]
