"""Wiring tests for the four LLM-bearing components.

These verify each component:
  - constructs without touching real prompt files (prompt_template injection)
  - invokes the LLMClient with the expected system / messages / model shape
  - parses the canned response back into the right typed result
  - raises loudly on malformed responses

They do not exercise prompt content. Prompt content is week 4-5 iteration
territory; these tests guard the wiring around the prompts.
"""

from __future__ import annotations

import json

import pytest

from predictive_review.dialogue import (
    DialogueManager,
    DialogueMessage,
    TurnRole,
)
from predictive_review.content_sources import DiffSource
from predictive_review.judge import ClosureJudge, JudgeOutcome
from predictive_review.reading import ReadingGenerator
from predictive_review.selectors.development import FirstNHunksSelector
from predictive_review.selectors.llm_judgment import LLMJudgmentSelector
from tests.fakes import CapturingLLMClient


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
"""


# --- LLMJudgmentSelector --------------------------------------------------


def _sample_region() -> "Region":  # type: ignore[name-defined]
    contents = DiffSource(SAMPLE_DIFF).produce()
    return FirstNHunksSelector().select(contents)[0]


def test_selector_calls_client_with_prompt_and_numbered_hunks() -> None:
    llm = CapturingLLMClient(
        response_text=json.dumps(
            {
                "selections": [
                    {
                        "hunk_index": 1,
                        "structural_label": "the new() function",
                        "rationale": "introduces a new public API",
                    }
                ]
            }
        )
    )
    selector = LLMJudgmentSelector(
        llm=llm,
        model="claude-haiku-x",
        prompt_template="SELECTOR PROMPT",
    )
    contents = DiffSource(SAMPLE_DIFF).produce()

    regions = selector.select(contents)

    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["system"] == "SELECTOR PROMPT"
    assert call["model"] == "claude-haiku-x"
    user_content = call["messages"][0].content
    assert "Hunk 1:" in user_content
    assert "Hunk 2:" in user_content

    assert len(regions) == 1
    assert regions[0].structural_label == "the new() function"
    assert regions[0].content == contents[0]
    assert regions[0].selector_rationale["rationale"] == "introduces a new public API"
    assert regions[0].selector_rationale["model_id"] == "claude-test"


def test_selector_raises_on_out_of_range_hunk_index() -> None:
    llm = CapturingLLMClient(
        response_text=json.dumps(
            {
                "selections": [
                    {
                        "hunk_index": 99,
                        "structural_label": "x",
                        "rationale": "y",
                    }
                ]
            }
        )
    )
    selector = LLMJudgmentSelector(
        llm=llm, model="m", prompt_template="P"
    )
    with pytest.raises(ValueError, match="out-of-range"):
        selector.select(DiffSource(SAMPLE_DIFF).produce())


def test_selector_raises_on_empty_selections() -> None:
    llm = CapturingLLMClient(response_text=json.dumps({"selections": []}))
    selector = LLMJudgmentSelector(
        llm=llm, model="m", prompt_template="P"
    )
    with pytest.raises(ValueError, match="no selections"):
        selector.select(DiffSource(SAMPLE_DIFF).produce())


def test_selector_handles_json_in_markdown_fence() -> None:
    llm = CapturingLLMClient(
        response_text="```json\n"
        + json.dumps(
            {
                "selections": [
                    {
                        "hunk_index": 1,
                        "structural_label": "x",
                        "rationale": "y",
                    }
                ]
            }
        )
        + "\n```"
    )
    selector = LLMJudgmentSelector(
        llm=llm, model="m", prompt_template="P"
    )
    regions = selector.select(DiffSource(SAMPLE_DIFF).produce())
    assert len(regions) == 1


# --- ReadingGenerator -----------------------------------------------------


def test_reading_generator_passes_region_to_client_and_returns_result() -> None:
    llm = CapturingLLMClient(
        response_text="  This function retries on 429 only.  ",
        model_id="claude-sonnet-x",
    )
    gen = ReadingGenerator(
        llm=llm, model="claude-sonnet-x", prompt_template="READING PROMPT"
    )
    region = _sample_region()

    result = gen.generate(region)

    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["system"] == "READING PROMPT"
    assert call["model"] == "claude-sonnet-x"
    user_content = call["messages"][0].content
    assert region.structural_label in user_content
    assert region.content.body in user_content

    assert result.body == "This function retries on 429 only."
    assert result.model_id == "claude-sonnet-x"
    assert result.prompt_version == "v1"


# --- ClosureJudge ---------------------------------------------------------


def test_judge_parses_pass_verdict() -> None:
    llm = CapturingLLMClient(
        response_text=json.dumps({"verdict": "PASS", "missing_aspects": None})
    )
    judge = ClosureJudge(llm=llm, model="m", prompt_template="JUDGE PROMPT")
    verdict = judge.judge(
        reading_body="the reading", teach_back_statement="the teach-back"
    )
    assert verdict.outcome is JudgeOutcome.PASS
    assert verdict.missing_aspects is None

    user_content = llm.calls[0]["messages"][0].content
    assert "the reading" in user_content
    assert "the teach-back" in user_content


def test_judge_parses_fail_verdict_with_missing_aspects() -> None:
    llm = CapturingLLMClient(
        response_text=json.dumps(
            {"verdict": "FAIL", "missing_aspects": "did not address retries"}
        )
    )
    judge = ClosureJudge(llm=llm, model="m", prompt_template="P")
    verdict = judge.judge(reading_body="r", teach_back_statement="t")
    assert verdict.outcome is JudgeOutcome.FAIL
    assert verdict.missing_aspects == "did not address retries"


def test_judge_raises_on_fail_without_missing_aspects() -> None:
    llm = CapturingLLMClient(
        response_text=json.dumps({"verdict": "FAIL", "missing_aspects": None})
    )
    judge = ClosureJudge(llm=llm, model="m", prompt_template="P")
    with pytest.raises(ValueError, match="missing_aspects"):
        judge.judge(reading_body="r", teach_back_statement="t")


def test_judge_raises_on_unknown_verdict() -> None:
    llm = CapturingLLMClient(
        response_text=json.dumps({"verdict": "MAYBE", "missing_aspects": None})
    )
    judge = ClosureJudge(llm=llm, model="m", prompt_template="P")
    with pytest.raises(ValueError, match="unrecognized verdict"):
        judge.judge(reading_body="r", teach_back_statement="t")


# --- DialogueManager ------------------------------------------------------


def test_dialogue_appends_engineer_message_and_returns_response() -> None:
    llm = CapturingLLMClient(
        response_text="  The retry is bounded by maxAttempts.  "
    )
    mgr = DialogueManager(llm=llm, model="m", prompt_template="DIALOGUE BASE")
    region = _sample_region()
    prior = [
        DialogueMessage(role=TurnRole.ENGINEER, body="why does it retry?"),
        DialogueMessage(role=TurnRole.MODEL, body="because of the policy."),
    ]
    response = mgr.respond(
        region=region,
        reading_body="THE READING",
        prior_turns=prior,
        engineer_message="is the retry bounded?",
    )

    assert response.body == "The retry is bounded by maxAttempts."
    assert response.prompt_version == "v1"

    call = llm.calls[0]
    assert call["system"].startswith("DIALOGUE BASE")
    assert region.structural_label in call["system"]
    assert "THE READING" in call["system"]

    msgs = call["messages"]
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0].content == "why does it retry?"
    assert msgs[1].content == "because of the policy."
    assert msgs[2].content == "is the retry bounded?"


# --- engagement threshold substitution -------------------------------------


def test_selector_substitutes_threshold_guidance_into_prompt() -> None:
    from predictive_review.selectors.base import SelectorContext
    from predictive_review.storage.models import EngagementThreshold

    llm = CapturingLLMClient(
        response_text=json.dumps(
            {
                "selections": [
                    {
                        "hunk_index": 1,
                        "structural_label": "x",
                        "rationale": "y",
                    }
                ]
            }
        )
    )
    selector = LLMJudgmentSelector(
        llm=llm,
        model="claude-haiku-x",
        prompt_template="header\n$engagement_threshold\nfooter",
    )
    contents = DiffSource(SAMPLE_DIFF).produce()

    selector.select(
        contents,
        context=SelectorContext(
            engagement_threshold=EngagementThreshold.LOAD_BEARING_ONLY
        ),
    )
    system = llm.calls[0]["system"]
    assert "LOAD-BEARING ONLY" in system
    assert "$engagement_threshold" not in system


def test_judge_substitutes_threshold_guidance_into_prompt() -> None:
    from predictive_review.storage.models import EngagementThreshold

    llm = CapturingLLMClient(
        response_text=json.dumps({"verdict": "PASS", "missing_aspects": None})
    )
    judge = ClosureJudge(
        llm=llm,
        model="claude-sonnet-x",
        prompt_template="header\n$engagement_threshold\nfooter",
    )
    judge.judge(
        reading_body="r",
        teach_back_statement="t",
        engagement_threshold=EngagementThreshold.THOROUGH,
    )
    system = llm.calls[0]["system"]
    assert "THOROUGH" in system
    assert "$engagement_threshold" not in system


def test_judge_defaults_threshold_to_default_when_unset() -> None:
    """Backward compatibility: callers that don't pass engagement_threshold
    still work, with the DEFAULT bar applied."""
    llm = CapturingLLMClient(
        response_text=json.dumps({"verdict": "PASS", "missing_aspects": None})
    )
    judge = ClosureJudge(
        llm=llm,
        model="claude-sonnet-x",
        prompt_template="$engagement_threshold",
    )
    judge.judge(reading_body="r", teach_back_statement="t")
    assert "DEFAULT" in llm.calls[0]["system"]
