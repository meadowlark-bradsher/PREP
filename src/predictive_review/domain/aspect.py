"""What a correct account of a region must include.

An aspect is one checkable claim about a member, named by an id that is
stable within that member. It exists so a FAIL can be *structured*: rather
than a sentence of prose the ledger cannot act on, the judge names which
declared claims the teach-back left uncovered.

`criteria` scopes the aspect to particular load types. An aspect that
serves `correctness` is not necessarily relevant when the session is
ordering by `churn-90d`, and asking the engineer to cover it anyway would
make a PASS mean something different from what the session claimed to be
about. An empty `criteria` means the aspect applies under every criterion.

Aspects are content, not state. They say what is true of the member, never
what any engineer has understood about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Aspect:
    id: str
    claim: str
    criteria: tuple[str, ...] = ()

    @property
    def is_universal(self) -> bool:
        """True when this aspect applies under every criterion."""
        return not self.criteria

    def applies_under(
        self,
        criterion: str | None,
        composition: Mapping[str, Sequence[str]] | None = None,
    ) -> bool:
        """Contract invariant 7: judge scope follows the criterion.

        A universal aspect always applies. A scoped one applies when the
        session's criterion is one it serves — which is what makes a PASS
        meaningful *at that scope* rather than a claim about the whole
        member.

        A **composite** criterion also carries its components. Invariant 3
        makes a composite the maintainers' account of what the software
        is, with each component a narrower view a user may override to; a
        composite that showed fewer aspects than its own parts would
        invert that, and would push authors to scope everything universal
        to work around it — turning invariant 7 into a no-op that still
        looks like it is working.
        """
        if self.is_universal:
            return True
        if criterion is None:
            return False
        return bool(set(self.criteria) & expand_criterion(criterion, composition))


def expand_criterion(
    criterion: str,
    composition: Mapping[str, Sequence[str]] | None = None,
) -> set[str]:
    """A criterion plus every criterion it is composed of, transitively.

    Cycle-guarded: the validator rejects a composite naming itself, but
    nothing there rules out a longer loop, and this must terminate on any
    manifest that reached it.
    """
    if not composition:
        return {criterion}
    seen: set[str] = set()
    pending = [criterion]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(composition.get(current) or ())
    return seen
