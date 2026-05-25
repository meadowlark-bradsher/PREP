"""Closure judge.

CONTRACT
========
Determines whether the engineer's teach-back statement covers the
substantive content of the reading. Two independence properties are
load-bearing and are enforced by the type signature.

  1. Fresh context per invocation. The judge sees a Reading and a
     teach-back statement; it does NOT see the dialogue history. If the
     same model that conducted the clarifying dialogue also judged
     closure, it would have every incentive to declare its own teaching
     successful. The judge is a separate LLM invocation with its own
     prompt and no conversational state.

  2. The judge does NOT see the engineer's hypothesis. The hypothesis
     is the engineer's prior, captured for comparison with the reading
     and surfaced to the engineer during reconciliation. Letting the
     judge see it would conflate calibration with verification.

On FAIL, the judge names what aspect of the reading is not yet covered
so the engineer has an actionable path forward — either by asking the
dialogue model about that aspect or by revising the teach-back directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .llm.client import LLMClient, Message
from .llm.parsing import extract_json
from .llm.prompts import load_prompt


class JudgeOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class JudgeVerdict:
    outcome: JudgeOutcome
    missing_aspects: str | None
    model_id: str
    prompt_version: str


class ClosureJudge:
    name = "judge"
    version = "v1"

    def __init__(
        self,
        *,
        llm: LLMClient,
        model: str,
        prompt_version: str = "v1",
        prompt_template: str | None = None,
    ) -> None:
        self._llm = llm
        self._model = model
        self._prompt_version = prompt_version
        self._system = prompt_template or load_prompt(f"judge_{prompt_version}")

    def judge(
        self,
        *,
        reading_body: str,
        teach_back_statement: str,
    ) -> JudgeVerdict:
        user_content = (
            f"Reading:\n{reading_body}\n\n"
            f"Engineer's teach-back:\n{teach_back_statement}"
        )
        completion = self._llm.complete(
            system=self._system,
            messages=[Message(role="user", content=user_content)],
            model=self._model,
        )
        data = extract_json(completion.text)

        raw_verdict = str(data.get("verdict", "")).strip().upper()
        if raw_verdict not in ("PASS", "FAIL"):
            raise ValueError(
                f"judge returned unrecognized verdict: {raw_verdict!r}"
            )
        outcome = JudgeOutcome.PASS if raw_verdict == "PASS" else JudgeOutcome.FAIL
        missing = data.get("missing_aspects")
        if outcome is JudgeOutcome.PASS:
            missing = None
        elif not missing:
            raise ValueError("judge returned FAIL without missing_aspects")

        return JudgeVerdict(
            outcome=outcome,
            missing_aspects=missing,
            model_id=completion.model_id,
            prompt_version=self._prompt_version,
        )
