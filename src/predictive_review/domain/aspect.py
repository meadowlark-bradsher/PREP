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


@dataclass(frozen=True)
class Aspect:
    id: str
    claim: str
    criteria: tuple[str, ...] = ()

    @property
    def is_universal(self) -> bool:
        """True when this aspect applies under every criterion."""
        return not self.criteria

    def applies_under(self, criterion: str | None) -> bool:
        """Contract invariant 7: judge scope follows the criterion.

        A universal aspect always applies. A scoped one applies only when
        the session is ordering by a criterion it serves — which is what
        makes a PASS meaningful *at that scope* rather than a claim about
        the whole member.
        """
        if self.is_universal:
            return True
        return criterion is not None and criterion in self.criteria
