"""Clarifying dialogue manager.

CONTRACT
========
Conducts the per-region dialogue that opens when the closure judge
denies a teach-back. The dialogue model sees the reading and the
prior turns of this region's thread; it does NOT see the closure
judge's verdicts directly — only the engineer's restated questions
or pushback, which is the engineer's interpretation of those verdicts.

Threads are per-region. There is no cross-region context.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .domain.region import Region
from .llm.client import LLMClient


class TurnRole(str, Enum):
    ENGINEER = "engineer"
    MODEL = "model"


@dataclass(frozen=True)
class DialogueMessage:
    role: TurnRole
    body: str


@dataclass(frozen=True)
class DialogueResponse:
    body: str
    model_id: str
    prompt_version: str


class DialogueManager:
    name = "dialogue"
    version = "v1"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def respond(
        self,
        *,
        region: Region,
        reading_body: str,
        prior_turns: list[DialogueMessage],
        engineer_message: str,
    ) -> DialogueResponse:
        raise NotImplementedError(
            "DialogueManager.respond: prompt engineering pending"
        )
