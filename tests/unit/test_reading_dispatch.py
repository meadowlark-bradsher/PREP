"""Kind-dispatched reading prompts.

A member's body is prose about part of a system and may contain no code
at all. Describing it with a prompt that says "Code:" and "the snippet"
would produce a reading about an implementation the model never saw, and
the engineer would reconcile their hypothesis against that invention.

The second concern here is narrower and sharper: the member prompt must
see the body and nothing else. Everything a manifest knows about a member
travels in `RegionContent.metadata` on the very object this component
receives, so the guarantee is one careless template edit away at all
times.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from predictive_review.content_sources import DiffSource, ManifestSource
from predictive_review.domain.content import RegionContent
from predictive_review.domain.region import Region
from predictive_review.reading import (
    ReadingGenerator,
    UnknownContentKind,
)
from tests.fakes import CapturingLLMClient

FIXTURE = Path(__file__).parent.parent / "fixtures" / "load_bearing_repo"

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
"""


def _member_region(member_id: str = "auth/token-refresh") -> Region:
    content = next(
        c for c in ManifestSource(FIXTURE).produce()
        if c.metadata["member_id"] == member_id
    )
    return Region(structural_label=member_id, content=content, selector_rationale={})


def _hunk_region() -> Region:
    content = DiffSource(SAMPLE_DIFF).produce()[0]
    return Region(
        structural_label=content.metadata["ref"], content=content, selector_rationale={}
    )


def _gen(llm: CapturingLLMClient) -> ReadingGenerator:
    return ReadingGenerator(
        llm=llm, model="m", prompt_template="CODE", member_prompt_template="MEMBER"
    )


# --- dispatch ---------------------------------------------------------------


def test_code_hunk_gets_the_code_prompt() -> None:
    llm = CapturingLLMClient(response_text="a reading")
    _gen(llm).generate(_hunk_region())
    assert llm.calls[0]["system"] == "CODE"


def test_member_gets_the_member_prompt() -> None:
    llm = CapturingLLMClient(response_text="a reading")
    _gen(llm).generate(_member_region())
    assert llm.calls[0]["system"] == "MEMBER"


def test_unknown_kind_is_an_error_not_a_fallback() -> None:
    """Silently describing an unknown kind as code is the failure this
    dispatch exists to prevent."""
    region = Region(
        structural_label="glossary/idempotence",
        content=RegionContent(kind="concept", body="..."),
        selector_rationale={},
    )
    llm = CapturingLLMClient(response_text="a reading")

    with pytest.raises(UnknownContentKind) as e:
        _gen(llm).generate(region)

    assert "concept" in str(e.value)
    assert "code_hunk" in str(e.value) and "member" in str(e.value)
    assert llm.calls == [], "no LLM call may be made for an unknown kind"


def test_prompt_version_distinguishes_the_two_prompts() -> None:
    llm = CapturingLLMClient(response_text="a reading")
    gen = _gen(llm)
    assert gen.generate(_hunk_region()).prompt_version == "v1"
    assert gen.generate(_member_region()).prompt_version == "member_v1"


# --- framing ----------------------------------------------------------------


def test_member_framing_does_not_call_the_body_code() -> None:
    llm = CapturingLLMClient(response_text="a reading")
    _gen(llm).generate(_member_region())

    user = llm.calls[0]["messages"][0].content
    assert "Code:" not in user
    assert "```" not in user, "a prose body must not be fenced as code"
    assert "Material:" in user


def test_code_framing_is_unchanged() -> None:
    llm = CapturingLLMClient(response_text="a reading")
    _gen(llm).generate(_hunk_region())

    user = llm.calls[0]["messages"][0].content
    assert "Code:" in user and "```" in user


def test_member_prompt_file_does_not_assume_source_code() -> None:
    from predictive_review.llm.prompts import load_prompt

    prompt = load_prompt("reading_member_v1")
    assert "3 to 6 sentences" in prompt, "length constraint must survive"
    assert "not necessarily source code" in prompt


# --- the body, and nothing but the body -------------------------------------


def test_no_metadata_reaches_the_member_prompt() -> None:
    """Everything the manifest knows rides in metadata on this same object.

    Named individually rather than as a loop so a failure says which
    field leaked.
    """
    llm = CapturingLLMClient(response_text="a reading")
    region = _member_region()
    _gen(llm).generate(region)

    prompt = llm.calls[0]["system"] + llm.calls[0]["messages"][0].content
    meta = region.content.metadata

    assert meta["rationale"] not in prompt, "the agent's argument must not leak"
    for aspect in meta["aspects"]:
        assert aspect["id"] not in prompt, f"aspect id {aspect['id']} leaked"
        assert aspect["claim"] not in prompt, f"aspect claim {aspect['id']} leaked"
    for criterion, score in meta["scores"].items():
        assert str(score) not in prompt, f"score for {criterion} leaked"
    for anchor in meta["anchors"]:
        assert anchor["path"] not in prompt, "anchor path leaked"
        assert anchor["range_hash"] not in prompt, "anchor hash leaked"


def test_the_body_does_reach_the_prompt() -> None:
    """The complement of the test above — blindness is not achieved by
    sending nothing."""
    llm = CapturingLLMClient(response_text="a reading")
    region = _member_region()
    _gen(llm).generate(region)

    assert region.content.body in llm.calls[0]["messages"][0].content


def test_generate_still_takes_only_a_region() -> None:
    import inspect

    params = set(inspect.signature(ReadingGenerator.generate).parameters)
    assert params == {"self", "region"}
