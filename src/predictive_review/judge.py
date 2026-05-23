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


class JudgeOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class JudgeVerdict:
    outcome: JudgeOutcome
    missing_aspects: str | None
    model_id: str
    prompt_version: str


from .llm.client import LLMClient  # noqa: E402 — kept below dataclass for clarity


class ClosureJudge:
    name = "judge"
    version = "v1"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def judge(
        self,
        *,
        reading_body: str,
        teach_back_statement: str,
    ) -> JudgeVerdict:
        raise NotImplementedError(
            "ClosureJudge.judge: prompt engineering pending"
        )
