"""Tests for the CLI's editor-template build/parse roundtrips.

The CLI uses templated files passed to $EDITOR for multi-line input.
The marker syntax is brittle by nature — if the regex and the template
drift apart, the CLI fails silently or with confusing errors. These
tests guard the roundtrip without invoking Click.
"""

from __future__ import annotations

import click
import pytest

from predictive_review.cli import (
    _build_hypothesis_template,
    _build_reconciliation_template,
    _parse_hypotheses,
    _parse_reconciliation,
)
from predictive_review.sessions.service import RegionSnapshot
from predictive_review.storage.models import (
    ClosureMode,
    ReconciliationLayout,
    RegionStatus,
)


def _region(ordinal: int, label: str = "x", hunk: str = "@@ hunk @@\n+a\n") -> RegionSnapshot:
    return RegionSnapshot(
        id=f"id-{ordinal}",
        ordinal=ordinal,
        structural_label=label,
        hunk_text=hunk,
        status=RegionStatus.AWAITING_RECONCILIATION,
        closure_mode=None,
    )


def test_hypothesis_roundtrip_extracts_user_text() -> None:
    regions = [_region(0, "first"), _region(1, "second")]
    template = _build_hypothesis_template(regions)
    # Simulate user filling in both placeholders.
    filled = (
        template
        .replace("(write your hypothesis here)", "my first hypothesis", 1)
        .replace("(write your hypothesis here)", "my second hypothesis", 1)
    )
    result = _parse_hypotheses(filled, 2)
    assert result == ["my first hypothesis", "my second hypothesis"]


def test_hypothesis_parse_rejects_empty_placeholder() -> None:
    regions = [_region(0)]
    template = _build_hypothesis_template(regions)
    # User saved without editing → placeholder still there
    with pytest.raises(click.UsageError, match="empty"):
        _parse_hypotheses(template, 1)


def test_hypothesis_parse_rejects_missing_markers() -> None:
    with pytest.raises(click.UsageError, match="markers"):
        _parse_hypotheses("no markers here", 1)


def test_reconciliation_roundtrip_strips_template_and_comments() -> None:
    region = _region(0, "the retry semantics")
    template = _build_reconciliation_template(
        region, "my hypothesis text", "the reading text", ReconciliationLayout.INLINE_HUNK
    )
    filled = template.replace(
        "(write your reconciliation here)",
        "I missed the retry bound but caught the error path.",
    )
    body = _parse_reconciliation(filled)
    assert body == "I missed the retry bound but caught the error path."


def test_reconciliation_parse_rejects_missing_separator() -> None:
    with pytest.raises(click.UsageError, match="separator"):
        _parse_reconciliation("# no separator anywhere\nsome text")


def test_reconciliation_parse_returns_empty_for_untouched_placeholder() -> None:
    region = _region(0)
    template = _build_reconciliation_template(
        region, "hyp", "read", ReconciliationLayout.NO_HUNK
    )
    assert _parse_reconciliation(template) == ""


def test_reconciliation_layout_inline_includes_hunk_section() -> None:
    region = _region(0, "x", hunk="@@ A @@\n+line\n")
    inline = _build_reconciliation_template(
        region, "h", "r", ReconciliationLayout.INLINE_HUNK
    )
    no_hunk = _build_reconciliation_template(
        region, "h", "r", ReconciliationLayout.NO_HUNK
    )
    assert "## Hunk" in inline
    assert "## Hunk" not in no_hunk


def test_closure_mode_import_compatibility() -> None:
    # Just verify ClosureMode imports work for RegionSnapshot typing — the
    # CLI file constructs RegionSnapshot objects via the service, not directly.
    assert ClosureMode.JUDGE_PASSED.value == "judge_passed"
