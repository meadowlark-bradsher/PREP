"""Development/test selectors.

These are not production selectors. They exist so the rest of the
application can be exercised end-to-end before the v1 LLM-judgment
prompt is ready, and so unit tests of orchestration code do not
depend on LLM calls. The PM doc's pluggable-selector architecture
treats this as the same shape as any future production selector.
"""

from __future__ import annotations

from ..domain.content import RegionContent
from ..domain.region import Region
from .base import SelectorContext


class FirstNHunksSelector:
    """Returns the first 2-4 candidates as regions, in source order.

    Selection is not load-bearing here; the structural labels are
    whatever the content already carries. This selector will never be
    used in production — it exists to make orchestration code runnable.
    """

    name = "first_n_hunks"
    version = "v1"

    def __init__(self, min_regions: int = 2, max_regions: int = 4) -> None:
        self._min = min_regions
        self._max = max_regions

    def select(
        self,
        contents: list[RegionContent],
        *,
        context: SelectorContext | None = None,
    ) -> list[Region]:
        n = min(self._max, max(self._min, len(contents)))
        selected = contents[: min(n, len(contents))]
        return [
            Region(
                structural_label=_structural_label(content),
                content=content,
                selector_rationale={"selector": self.name, "strategy": "first-N"},
            )
            for content in selected
        ]


def _structural_label(content: RegionContent) -> str:
    """Best available human-facing handle for a candidate.

    Diff hunks carry `ref` (``path@start-end``) from `Hunk.to_content`.
    Content kinds that carry no ref fall back to the kind itself rather
    than inventing geometry that isn't there.
    """
    return str(content.metadata.get("ref") or content.kind)
