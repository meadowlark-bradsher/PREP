"""Contract tests for the LLM-call seam.

These tests do not exercise any real LLM; they confirm that the
Protocols are satisfied, the stub selector produces sensible output,
and the registry resolves the development selector. The production
stubs (LLMJudgmentSelector, ReadingGenerator, ClosureJudge,
DialogueManager) are asserted to raise NotImplementedError so that
production paths fail loudly until their prompts are written.
"""

from __future__ import annotations

import pytest

from predictive_review.domain.diff import parse_diff
from predictive_review.selectors.base import RegionSelector
from predictive_review.selectors.development import FirstNHunksSelector
from predictive_review.selectors.registry import default_registry


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


# --- selector contract ------------------------------------------------------


def test_first_n_hunks_selector_satisfies_protocol() -> None:
    selector = FirstNHunksSelector()
    assert isinstance(selector, RegionSelector)
    assert selector.name == "first_n_hunks"
    assert selector.version == "v1"


def test_first_n_hunks_selector_returns_regions() -> None:
    selector = FirstNHunksSelector()
    diff = parse_diff(SAMPLE_DIFF)
    regions = selector.select(diff)
    assert 2 <= len(regions) <= 4
    for r in regions:
        assert r.structural_label
        assert r.hunk.text
        assert r.selector_rationale["selector"] == "first_n_hunks"


def test_first_n_hunks_selector_caps_at_max() -> None:
    selector = FirstNHunksSelector(min_regions=2, max_regions=2)
    diff = parse_diff(SAMPLE_DIFF)
    assert len(selector.select(diff)) == 2


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
