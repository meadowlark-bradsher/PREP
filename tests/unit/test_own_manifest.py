"""PREP's own `.load-bearing/` manifest must stay valid and fresh.

This is the dogfood. PREP declares three of its own regions as members, so
the format is exercised against a repo it was not designed around rather
than only against the fixture, which was built to fit it.

**When this fails, it is usually telling the truth.** A stale member means
an anchored range moved and the body may now describe code that is gone.
The fix is to read the slice, update the body, and only then re-stamp:

    python .claude/skills/load-bearing/scripts/lb_manifest.py check
    python .claude/skills/load-bearing/scripts/lb_manifest.py slice <path>:<start>-<end>
    python .claude/skills/load-bearing/scripts/lb_manifest.py restamp <member-id>

Re-stamping without reading the slice makes this test pass while making the
manifest lie, which is worse than a red test.
"""

from __future__ import annotations

import json
from pathlib import Path

from predictive_review.content_sources import ManifestSource

REPO_ROOT = Path(__file__).parent.parent.parent
MANIFEST = REPO_ROOT / ".load-bearing" / "manifest.json"


def test_manifest_exists_and_validates() -> None:
    assert MANIFEST.is_file(), "PREP's own manifest is missing"
    ManifestSource(REPO_ROOT)  # raises ManifestError on any violation


def test_no_member_is_stale() -> None:
    source = ManifestSource(REPO_ROOT)
    assert source.stale_member_ids == (), (
        f"stale members: {', '.join(source.stale_member_ids)}. "
        "An anchored range moved — read the slice and update the body before "
        "re-stamping. See this module's docstring."
    )


def test_every_member_is_produced() -> None:
    declared = {m["id"] for m in json.loads(MANIFEST.read_text())["members"]}
    produced = {c.metadata["member_id"] for c in ManifestSource(REPO_ROOT).produce()}
    assert produced == declared


def test_criteria_are_repo_specific_not_the_example_set() -> None:
    """The skill's own advice, enforced on the skill's first output.

    Shipping the contract's illustrative criteria unchanged is the tell that
    nobody asked what load means in this repo.
    """
    declared = set(ManifestSource(REPO_ROOT).criterion_ids)
    assert declared != {"correctness", "churn-90d", "identity"}


def test_the_composite_is_overridable_by_a_component() -> None:
    """Invariant 3 has to bite on real data, not just on the fixture.

    A composite whose ordering no component ever disagrees with is a black
    box: there would be nothing for a user to override.
    """
    from predictive_review.selectors.base import SelectorContext
    from predictive_review.selectors.manifest import ManifestSelector

    source = ManifestSource(REPO_ROOT)
    contents = source.produce()

    def order(criterion: str) -> list[str]:
        return [
            r.structural_label
            for r in ManifestSelector(criterion).select(
                contents, context=SelectorContext()
            )
        ]

    default = order(source.default_criterion)
    components = [c for c in source.criterion_ids if c != source.default_criterion]
    assert any(order(c) != default for c in components), (
        "no component ordering differs from the composite; the override "
        "invariant 3 exists for would be meaningless here"
    )
