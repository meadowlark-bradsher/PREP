"""Test doubles for the LLM-bearing components.

These satisfy the same contracts as the production stubs (Reading, Judge,
Dialogue) but return canned values so the orchestrator can be exercised
end-to-end without an LLM. Each fake records its calls for assertion.

Distinct from FirstNHunksSelector in selectors/development.py: that
selector is intended to be runnable in a real dev environment; these
fakes are strictly for tests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from predictive_review.dialogue import DialogueMessage, DialogueResponse
from predictive_review.domain.region import Region
from predictive_review.judge import JudgeOutcome, JudgeVerdict
from predictive_review.llm.client import Completion, Message
from predictive_review.reading import ReadingResult


@dataclass
class FakeReadingGenerator:
    name: str = "fake_reading"
    version: str = "fake"
    calls: list[Region] = field(default_factory=list)

    def generate(self, region: Region) -> ReadingResult:
        self.calls.append(region)
        return ReadingResult(
            body=f"FAKE READING of {region.structural_label}",
            model_id="fake",
            prompt_version="fake",
        )


@dataclass
class FakeClosureJudge:
    """Configurable judge.

    `decide` returns (outcome, missing_aspects) given (reading_body,
    teach_back_statement). Default policy: PASS if teach-back contains
    'understand', otherwise FAIL — gives tests an easy lever to drive
    either branch by choosing teach-back wording.
    """

    decide: Callable[[str, str], tuple[JudgeOutcome, str | None]] | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)
    name: str = "fake_judge"
    version: str = "fake"

    def judge(
        self,
        *,
        reading_body: str,
        teach_back_statement: str,
    ) -> JudgeVerdict:
        self.calls.append((reading_body, teach_back_statement))
        decider = self.decide or _default_judge_policy
        outcome, missing = decider(reading_body, teach_back_statement)
        return JudgeVerdict(
            outcome=outcome,
            missing_aspects=missing,
            model_id="fake",
            prompt_version="fake",
        )


def _default_judge_policy(
    reading_body: str, teach_back_statement: str
) -> tuple[JudgeOutcome, str | None]:
    if "understand" in teach_back_statement.lower():
        return JudgeOutcome.PASS, None
    return JudgeOutcome.FAIL, "teach-back did not address the retry semantics"


@dataclass
class CapturingLLMClient:
    """A fake LLMClient that captures every call and returns a canned response.

    Used by component wiring tests: assert the component invoked the client
    with the expected system / messages / model, and that it parsed the
    canned response back into the right typed result.
    """

    response_text: str = ""
    model_id: str = "claude-test"
    calls: list[dict] = field(default_factory=list)

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int = 4096,
    ) -> Completion:
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "model": model,
                "max_tokens": max_tokens,
            }
        )
        return Completion(text=self.response_text, model_id=self.model_id)


@dataclass
class FakeDialogueManager:
    name: str = "fake_dialogue"
    version: str = "fake"
    calls: list[tuple[Region, str, int]] = field(default_factory=list)

    def respond(
        self,
        *,
        region: Region,
        reading_body: str,
        prior_turns: list[DialogueMessage],
        engineer_message: str,
    ) -> DialogueResponse:
        self.calls.append((region, engineer_message, len(prior_turns)))
        return DialogueResponse(
            body=f"FAKE REPLY to: {engineer_message}",
            model_id="fake",
            prompt_version="fake",
        )
