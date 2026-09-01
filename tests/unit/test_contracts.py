"""Contract tests for the LLM-call seam.

These tests do not exercise any real LLM; they confirm that the
Protocols are satisfied, the stub selector produces sensible output,
and the registry resolves the development selector. The production
stubs (LLMJudgmentSelector, ReadingGenerator, ClosureJudge,
DialogueManager) are asserted to raise NotImplementedError so that
production paths fail loudly until their prompts are written.
"""

from __future__ import annotations

import inspect

import pytest

from predictive_review.content_sources import ContentSource, DiffSource
from predictive_review.domain.content import RegionContent
from predictive_review.domain.diff import parse_diff
from predictive_review.selectors.base import RegionSelector
from predictive_review.selectors.development import FirstNHunksSelector
from predictive_review.selectors.registry import default_registry
from predictive_review.sessions.service import SessionService


SAMPLE_DIFF = """\
diff --git a/foo.py b/foo.py
index 0000000..1111111 100644
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,5 @@
 def existing():
     pass
+
+def new():
+    return 42
@@ -10,2 +12,4 @@ def other():
     x = 1
+    # added comment
+    print("hello")
     return x
diff --git a/bar.py b/bar.py
index 2222222..3333333 100644
--- a/bar.py
+++ b/bar.py
@@ -5,3 +5,4 @@
 def thing():
     a = 1
+    b = 2
     return a
"""


# --- diff parsing -----------------------------------------------------------


def test_parse_diff_extracts_all_hunks() -> None:
    diff = parse_diff(SAMPLE_DIFF)
    assert len(diff.hunks) == 3
    files = {h.file_path for h in diff.hunks}
    assert files == {"foo.py", "bar.py"}


def test_hunk_ref_is_stable_identifier() -> None:
    diff = parse_diff(SAMPLE_DIFF)
    ref = diff.hunks[0].ref
    assert "foo.py" in ref
    assert "@" in ref


# --- content source contract ------------------------------------------------


def test_diff_source_satisfies_protocol() -> None:
    assert isinstance(DiffSource(SAMPLE_DIFF), ContentSource)


def test_diff_source_produces_one_code_hunk_per_hunk_in_diff_order() -> None:
    contents = DiffSource(SAMPLE_DIFF).produce()
    hunks = parse_diff(SAMPLE_DIFF).hunks

    assert len(contents) == len(hunks)
    assert all(isinstance(c, RegionContent) for c in contents)
    assert {c.kind for c in contents} == {"code_hunk"}
    assert [c.body for c in contents] == [h.text for h in hunks]


def test_diff_source_keeps_geometry_in_metadata_not_in_body() -> None:
    """The core reads `body`; everything diff-shaped rides in metadata.

    This is the property that lets a non-diff source reuse the pipeline.
    """
    content = DiffSource(SAMPLE_DIFF).produce()[0]
    assert content.metadata["file_path"] == "foo.py"
    assert "@" in content.metadata["ref"]
    assert "new_start" in content.metadata


def test_diff_source_preserves_raw_text_for_provenance() -> None:
    assert DiffSource(SAMPLE_DIFF).raw_text == SAMPLE_DIFF


def test_diff_source_on_empty_diff_produces_nothing() -> None:
    assert DiffSource("").produce() == []


def test_submit_takes_a_content_source_not_diff_text() -> None:
    """Pin the port at the orchestration seam.

    `submit` must not reacquire a diff-shaped parameter: the whole point
    of the port is that the service never learns where content came from.
    """
    params = inspect.signature(SessionService.submit).parameters
    assert "source" in params
    assert "diff_text" not in params
    assert params["source"].kind is inspect.Parameter.KEYWORD_ONLY


def test_selector_contract_consumes_content_not_a_diff() -> None:
    """A selector ranks candidates; it never parses the source material."""
    params = inspect.signature(FirstNHunksSelector.select).parameters
    assert "contents" in params
    assert "diff" not in params


# --- selector contract ------------------------------------------------------


def test_first_n_hunks_selector_satisfies_protocol() -> None:
    selector = FirstNHunksSelector()
    assert isinstance(selector, RegionSelector)
    assert selector.name == "first_n_hunks"
    assert selector.version == "v1"


def test_first_n_hunks_selector_returns_regions() -> None:
    selector = FirstNHunksSelector()
    regions = selector.select(DiffSource(SAMPLE_DIFF).produce())
    assert 2 <= len(regions) <= 4
    for r in regions:
        assert r.structural_label
        assert r.content.body
        assert r.selector_rationale["selector"] == "first_n_hunks"


def test_first_n_hunks_selector_caps_at_max() -> None:
    selector = FirstNHunksSelector(min_regions=2, max_regions=2)
    contents = DiffSource(SAMPLE_DIFF).produce()
    assert len(selector.select(contents)) == 2


# --- registry ---------------------------------------------------------------


def test_registry_resolves_development_selector() -> None:
    selector = default_registry.get("first_n_hunks")
    assert isinstance(selector, RegionSelector)
    assert selector.name == "first_n_hunks"


def test_registry_rejects_unknown_selector() -> None:
    with pytest.raises(KeyError):
        default_registry.get("nonexistent")


def test_registry_rejects_duplicate_registration() -> None:
    from predictive_review.selectors.registry import SelectorRegistry

    reg = SelectorRegistry()
    reg.register("a", FirstNHunksSelector)
    with pytest.raises(ValueError):
        reg.register("a", FirstNHunksSelector)


# Wiring tests for the four LLM-bearing components are in test_components.py.
# This file keeps tests that don't require any LLM at all: diff parsing,
# the dev selector, and the registry.
