"""Manifest-ranked region selector.

Orders members by a score the target repo declared for them. Where
`LLMJudgmentSelector` asks a cold model which hunks look load-bearing,
this selector asks nothing: the agents working inside the repo already
decided, and the manifest carries their answer.

PROJECTION, NOT SYNTHESIS (contract invariant 2)
================================================

This selector picks an ordering. It never computes a score, blends
criteria, or applies `weights` — a composite score is used exactly as the
manifest recorded it, and a component score is used exactly as recorded.
The composite is authoritative *as the default*; a user whose work
responds to one load type selects that component instead, and gets a
genuinely different ordering rather than a re-weighting of the same one.

That override is only meaningful because P7 forces a member that scores
anything to score everything: composite and component orderings then
range over the same members and are honestly comparable.
"""

from __future__ import annotations

from ..domain.content import RegionContent
from ..domain.region import Region
from ..llm.threshold import selector_region_bounds
from ..storage.models import EngagementThreshold
from .base import SelectorContext


class UnknownCriterion(ValueError):
    """The requested criterion is not one this manifest declares."""

    def __init__(self, criterion: str, declared: tuple[str, ...]) -> None:
        self.criterion = criterion
        self.declared = declared
        listed = ", ".join(declared) if declared else "(none declared)"
        super().__init__(
            f"unknown criterion {criterion!r}; this manifest declares: {listed}"
        )


class ManifestSelector:
    """Ranks scored members by one criterion, descending.

    `criterion` given here wins. Otherwise the session's resolved
    criterion arrives on the SelectorContext, which is how the registry
    can build this selector with no arguments and still order by the
    manifest's `default_criterion`.
    """

    name = "manifest"
    version = "v1"

    def __init__(self, criterion: str | None = None) -> None:
        self._criterion = criterion

    def select(
        self,
        contents: list[RegionContent],
        *,
        context: SelectorContext | None = None,
    ) -> list[Region]:
        criterion = self._criterion or (context.criterion if context else None)
        if criterion is None:
            raise ValueError(
                "ManifestSelector needs a criterion: pass one to the "
                "constructor or set it on the SelectorContext"
            )

        # P7: members without scores are eligible under PREP's own
        # selectors, not this one. There is nothing here to rank them by.
        scored = [c for c in contents if c.metadata.get("scores")]
        if not scored:
            raise UnknownCriterion(criterion, ())

        declared = _declared_criteria(scored)
        if criterion not in declared:
            raise UnknownCriterion(criterion, declared)

        # Descending by score. `sorted` is stable, so members the manifest
        # scored equally stay in manifest order rather than being permuted.
        ordered = sorted(scored, key=lambda c: -c.metadata["scores"][criterion])

        low, high = selector_region_bounds(
            context.engagement_threshold if context else EngagementThreshold.DEFAULT
        )
        count = min(high, max(low, len(ordered)))
        return [
            Region(
                structural_label=str(content.metadata.get("member_id") or content.kind),
                content=content,
                selector_rationale={
                    "selector": self.name,
                    "criterion": criterion,
                    "score": content.metadata["scores"][criterion],
                },
            )
            for content in ordered[: min(count, len(ordered))]
        ]


def _declared_criteria(scored: list[RegionContent]) -> tuple[str, ...]:
    """The criterion ids this manifest declares, read off any scored member.

    P7 guarantees a member that scores anything scores every declared
    criterion, so one scored member's keys are the full declared set. The
    union across members is taken anyway so a validator regression shows
    up as a confusing ordering rather than a silent partial view.
    """
    seen: dict[str, None] = {}
    for content in scored:
        for cid in content.metadata["scores"]:
            seen.setdefault(cid, None)
    return tuple(seen)
