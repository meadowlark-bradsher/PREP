"""ManifestSelector: criterion projection, ordering, and the override.

The point of these tests is invariant 3. A composite is the maintainers'
default answer about what the software *is*; a component is an answer
about one load type. A user whose work responds to one load type must be
able to select it and get a genuinely different ordering — not a
re-weighting of the composite. So the central test is that two orderings
over the same members disagree.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from predictive_review.content_sources import ManifestSource
from predictive_review.selectors.base import SelectorContext
from predictive_review.selectors.manifest import ManifestSelector, UnknownCriterion
from predictive_review.selectors.registry import default_registry
from predictive_review.storage.models import EngagementThreshold

FIXTURE = Path(__file__).parent.parent / "fixtures" / "load_bearing_repo"


def _contents():
    return ManifestSource(FIXTURE).produce()


def _order(criterion: str | None, *, contents=None, context=None) -> list[str]:
    regions = ManifestSelector(criterion).select(
        contents if contents is not None else _contents(),
        context=context if context is not None else SelectorContext(),
    )
    return [r.structural_label for r in regions]


# --- invariant 3: the override is real --------------------------------------


def test_component_ordering_differs_from_the_composite() -> None:
    """The whole reason criteria stay labelled.

    `identity` is composed of `correctness` and `churn-90d`. Ordering by
    churn reverses the composite's answer: a user who responds to change
    load sees a different region first than the manifest's default.
    """
    composite = _order("identity")
    churn = _order("churn-90d")

    assert composite == ["auth/token-refresh", "cache/lru-read"]
    assert churn == ["cache/lru-read", "auth/token-refresh"]
    assert churn == list(reversed(composite))


def test_the_other_component_agrees_with_the_composite() -> None:
    """Not every component disagrees — that would make the composite
    useless rather than authoritative."""
    assert _order("correctness") == _order("identity")


def test_ordering_is_by_score_descending() -> None:
    regions = ManifestSelector("churn-90d").select(_contents(), context=SelectorContext())
    scores = [r.selector_rationale["score"] for r in regions]
    assert scores == sorted(scores, reverse=True)


def test_selector_rationale_records_the_criterion_and_score() -> None:
    region = ManifestSelector("correctness").select(
        _contents(), context=SelectorContext()
    )[0]
    assert region.selector_rationale["selector"] == "manifest"
    assert region.selector_rationale["criterion"] == "correctness"
    assert region.selector_rationale["score"] == 0.9


# --- projection, never synthesis (invariant 2) ------------------------------


def test_selector_uses_the_recorded_composite_verbatim() -> None:
    """PREP must not recompute `identity` from its components and weights.

    The fixture's identity scores are a 0.7/0.3 blend, so a selector that
    ignored the recorded value and re-derived it would land on the same
    order. Doctor the recorded composite so the two answers diverge, and
    assert PREP follows what was recorded.
    """
    contents = _contents()
    by_id = {c.metadata["member_id"]: c for c in contents}
    # auth outranks lru-read on identity as recorded; invert only the
    # recorded composite, leaving the components (and thus any re-derived
    # blend) untouched.
    by_id["auth/token-refresh"].metadata["scores"]["identity"] = 0.1

    assert _order("identity", contents=contents) == [
        "cache/lru-read",
        "auth/token-refresh",
    ]


def test_weights_are_never_applied(tmp_path: Path) -> None:
    """`weights` is informational and PREP never applies it.

    Doctored to a wild value that would reorder everything if it were
    read; the ordering must not move.
    """
    baseline = _order("identity")

    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    manifest_path = root / ".load-bearing" / "manifest.json"
    data = json.loads(manifest_path.read_text())
    composite = next(c for c in data["criteria"] if c["id"] == "identity")
    composite["weights"] = {"correctness": -99.0, "churn-90d": 99.0}
    manifest_path.write_text(json.dumps(data))

    assert _order("identity", contents=ManifestSource(root).produce()) == baseline


def test_weights_never_reach_the_selector() -> None:
    """It is not carried into content metadata at all, so there is nothing
    for a future selector to accidentally start reading."""
    for content in _contents():
        assert "weights" not in content.metadata
        assert "weights" not in (content.metadata.get("metadata") or {})


# --- criterion resolution ----------------------------------------------------


def test_criterion_from_context_when_constructor_has_none() -> None:
    source = ManifestSource(FIXTURE)
    regions = ManifestSelector().select(
        source.produce(),
        context=SelectorContext(criterion=source.default_criterion),
    )
    assert [r.structural_label for r in regions] == _order("identity")


def test_constructor_criterion_wins_over_context() -> None:
    regions = ManifestSelector("churn-90d").select(
        _contents(), context=SelectorContext(criterion="identity")
    )
    assert [r.structural_label for r in regions] == _order("churn-90d")


def test_no_criterion_anywhere_is_an_error() -> None:
    with pytest.raises(ValueError, match="needs a criterion"):
        ManifestSelector().select(_contents(), context=SelectorContext())


def test_unknown_criterion_lists_the_declared_ids() -> None:
    with pytest.raises(UnknownCriterion) as e:
        ManifestSelector("maintainability").select(_contents(), context=SelectorContext())
    message = str(e.value)
    assert "maintainability" in message
    for declared in ("correctness", "churn-90d", "identity"):
        assert declared in message


# --- P7: unscored members ----------------------------------------------------


def test_unscored_members_are_not_ranked_by_this_selector() -> None:
    """parser/tokenize carries no scores; it is eligible under PREP's own
    selectors but there is nothing here to rank it by."""
    assert "parser/tokenize" not in _order("identity")
    assert "parser/tokenize" in {
        c.metadata["member_id"] for c in _contents()
    }


def test_manifest_with_no_scored_members_at_all_is_an_error(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    manifest_path = root / ".load-bearing" / "manifest.json"
    data = json.loads(manifest_path.read_text())
    for member in data["members"]:
        member.pop("scores", None)
    manifest_path.write_text(json.dumps(data))

    with pytest.raises(UnknownCriterion):
        ManifestSelector("identity").select(
            ManifestSource(root).produce(), context=SelectorContext()
        )


# --- engagement threshold ----------------------------------------------------


@pytest.mark.parametrize(
    "threshold,expected_max",
    [
        (EngagementThreshold.LOAD_BEARING_ONLY, 2),
        (EngagementThreshold.DEFAULT, 4),
        (EngagementThreshold.THOROUGH, 4),
    ],
)
def test_threshold_bounds_the_region_count(threshold, expected_max) -> None:
    regions = ManifestSelector("identity").select(
        _contents(), context=SelectorContext(engagement_threshold=threshold)
    )
    assert len(regions) <= expected_max


def test_ties_keep_manifest_order() -> None:
    contents = _contents()
    for content in contents:
        if content.metadata.get("scores"):
            content.metadata["scores"]["identity"] = 0.5

    assert _order("identity", contents=contents) == [
        "auth/token-refresh",
        "cache/lru-read",
    ]


# --- registry ----------------------------------------------------------------


def test_manifest_selector_is_registered() -> None:
    assert "manifest" in default_registry.names()
    selector = default_registry.get("manifest")
    assert selector.name == "manifest"
    assert selector.version == "v1"
