"""Judge aspect mode: scoping, the structured verdict, and blindness.

Two things are being protected here. First, contract invariant 7 — the
judge sees only aspects that serve the session's criterion, so a PASS
means "covered at this scope" and the ledger cannot later read it as
whole-member evidence. Second, P5 — a structured verdict that names an
aspect nobody supplied is a parse failure, not a soft signal.
"""

from __future__ import annotations

import json

import pytest

from predictive_review.domain.aspect import Aspect
from predictive_review.judge import (
    ClosureJudge,
    JudgeOutcome,
    format_missing_aspects,
)
from tests.fakes import CapturingLLMClient

ASPECTS = [
    Aspect(id="retry-bound", claim="retries are capped", criteria=("correctness",)),
    Aspect(id="token-shape", claim="the token is opaque", criteria=()),
]


def _judge(response: dict, **kwargs) -> ClosureJudge:
    return ClosureJudge(
        llm=CapturingLLMClient(response_text=json.dumps(response)),
        model="m",
        prompt_template="PROSE",
        member_prompt_template="MEMBER",
        **kwargs,
    )


# --- mode selection ---------------------------------------------------------


def test_no_aspects_stays_in_prose_mode() -> None:
    judge = _judge({"verdict": "FAIL", "missing_aspects": "You skipped the retry cap."})
    verdict = judge.judge(reading_body="r", teach_back_statement="t")

    assert verdict.missing_aspects == "You skipped the retry cap."
    assert not verdict.is_structured
    assert verdict.prompt_version == "v1"


def test_aspects_switch_to_structured_mode() -> None:
    judge = _judge({"verdict": "FAIL", "missing_aspects": ["retry-bound"]})
    verdict = judge.judge(
        reading_body="r", teach_back_statement="t", aspects=ASPECTS
    )

    assert verdict.missing_aspects == ["retry-bound"]
    assert verdict.is_structured


def test_structured_mode_records_a_distinct_prompt_version() -> None:
    """Provenance must say which prompt judged the attempt; two prompts
    can judge the same region across its lifetime."""
    judge = _judge({"verdict": "PASS", "missing_aspects": []})
    prose = judge.judge(reading_body="r", teach_back_statement="t")
    structured = judge.judge(
        reading_body="r", teach_back_statement="t", aspects=ASPECTS
    )

    assert prose.prompt_version == "v1"
    assert structured.prompt_version == "member_v1"


def test_structured_mode_uses_the_member_prompt() -> None:
    llm = CapturingLLMClient(response_text=json.dumps({"verdict": "PASS"}))
    judge = ClosureJudge(
        llm=llm, model="m", prompt_template="PROSE", member_prompt_template="MEMBER"
    )
    judge.judge(reading_body="r", teach_back_statement="t", aspects=ASPECTS)

    assert llm.calls[0]["system"] == "MEMBER"


def test_aspect_claims_reach_the_prompt() -> None:
    llm = CapturingLLMClient(response_text=json.dumps({"verdict": "PASS"}))
    judge = ClosureJudge(
        llm=llm, model="m", prompt_template="P", member_prompt_template="M"
    )
    judge.judge(reading_body="r", teach_back_statement="t", aspects=ASPECTS)

    user = llm.calls[0]["messages"][0].content
    assert "retry-bound" in user and "retries are capped" in user
    assert "token-shape" in user


# --- P5: the structured verdict is trustworthy or it is a parse failure -----


def test_pass_returns_no_missing_aspects() -> None:
    judge = _judge({"verdict": "PASS", "missing_aspects": ["retry-bound"]})
    verdict = judge.judge(
        reading_body="r", teach_back_statement="t", aspects=ASPECTS
    )
    assert verdict.missing_aspects is None
    assert verdict.outcome is JudgeOutcome.PASS


def test_fail_with_an_empty_list_is_a_parse_failure() -> None:
    judge = _judge({"verdict": "FAIL", "missing_aspects": []})
    with pytest.raises(ValueError, match="without missing_aspects"):
        judge.judge(reading_body="r", teach_back_statement="t", aspects=ASPECTS)


def test_fail_with_prose_in_structured_mode_is_a_parse_failure() -> None:
    """A judge answering in the wrong shape has not answered the question."""
    judge = _judge({"verdict": "FAIL", "missing_aspects": "you missed the cap"})
    with pytest.raises(ValueError, match="expected a list of aspect ids"):
        judge.judge(reading_body="r", teach_back_statement="t", aspects=ASPECTS)


def test_aspect_id_outside_the_given_set_is_a_parse_failure() -> None:
    """The load-bearing one: an invented id must never reach the ledger."""
    judge = _judge({"verdict": "FAIL", "missing_aspects": ["retry-bound", "invented"]})
    with pytest.raises(ValueError, match="not given"):
        judge.judge(reading_body="r", teach_back_statement="t", aspects=ASPECTS)


def test_non_string_aspect_id_is_a_parse_failure() -> None:
    judge = _judge({"verdict": "FAIL", "missing_aspects": [1, 2]})
    with pytest.raises(ValueError, match="non-string aspect id"):
        judge.judge(reading_body="r", teach_back_statement="t", aspects=ASPECTS)


def test_duplicate_ids_are_collapsed_not_rejected() -> None:
    judge = _judge(
        {"verdict": "FAIL", "missing_aspects": ["retry-bound", "retry-bound"]}
    )
    verdict = judge.judge(
        reading_body="r", teach_back_statement="t", aspects=ASPECTS
    )
    assert verdict.missing_aspects == ["retry-bound"]


def test_prose_mode_still_rejects_fail_without_missing_aspects() -> None:
    """The FAIL-requires-missing_aspects rule holds in both modes."""
    judge = _judge({"verdict": "FAIL", "missing_aspects": None})
    with pytest.raises(ValueError, match="without missing_aspects"):
        judge.judge(reading_body="r", teach_back_statement="t")


def test_unrecognized_verdict_still_rejected_in_structured_mode() -> None:
    judge = _judge({"verdict": "MAYBE", "missing_aspects": []})
    with pytest.raises(ValueError, match="unrecognized verdict"):
        judge.judge(reading_body="r", teach_back_statement="t", aspects=ASPECTS)


# --- blindness --------------------------------------------------------------


def test_judge_signature_admits_no_hypothesis_in_either_mode() -> None:
    import inspect

    params = set(inspect.signature(ClosureJudge.judge).parameters)
    assert params == {
        "self",
        "reading_body",
        "teach_back_statement",
        "engagement_threshold",
        "aspects",
    }


def test_member_prompt_never_mentions_the_hypothesis() -> None:
    from predictive_review.llm.prompts import load_prompt

    prompt = load_prompt("judge_member_v1").lower()
    for forbidden in ("hypothesis", "dialogue", "prediction", "guess"):
        assert forbidden not in prompt


# --- presentation -----------------------------------------------------------


def test_format_missing_aspects_handles_both_shapes() -> None:
    assert format_missing_aspects("a sentence") == "a sentence"
    assert format_missing_aspects(["a", "b"]) == "a, b"
    assert format_missing_aspects(None) == ""
    assert format_missing_aspects([]) == ""
