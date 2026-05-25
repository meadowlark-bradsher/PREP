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
    _resolve_diff_text,
    _strip_to_diff,
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


# --- diff sourcing -------------------------------------------------------


def test_resolve_diff_rejects_multiple_sources() -> None:
    with pytest.raises(click.UsageError, match="mutually exclusive"):
        _resolve_diff_text(None, "HEAD", "main..HEAD")


def test_resolve_diff_rejects_diff_and_commit() -> None:
    # diff_source is "truthy" if it's not None; pass a stub object.
    class _StubFile:
        def read(self):
            return ""

    with pytest.raises(click.UsageError, match="mutually exclusive"):
        _resolve_diff_text(_StubFile(), "HEAD", None)


def test_strip_to_diff_drops_git_show_preamble() -> None:
    raw = (
        "commit 2ea9187abc\n"
        "Author: Meadowlark\n"
        "Date:   2026-05-24\n"
        "\n"
        "    a subject line\n"
        "\n"
        "    a body paragraph mentioning diff --git in passing\n"
        "\n"
        "diff --git a/foo.py b/foo.py\n"
        "index 0..1\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    stripped = _strip_to_diff(raw)
    assert stripped.startswith("diff --git a/foo.py b/foo.py\n")
    assert "Author:" not in stripped
    assert "diff --git in passing" not in stripped


def test_strip_to_diff_passes_through_clean_git_diff_output() -> None:
    raw = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
    assert _strip_to_diff(raw) == raw


def test_strip_to_diff_raises_when_no_diff_present() -> None:
    with pytest.raises(click.ClickException, match="no diff"):
        _strip_to_diff("commit abc\nAuthor: x\n\nempty commit\n")
