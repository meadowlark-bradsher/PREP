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
from .llm.client import LLMClient, Message
from .llm.prompts import load_prompt


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


# Anthropic API role names differ from our internal TurnRole; we map at the
# call boundary so the rest of the application uses domain vocabulary.
_API_ROLE = {
    TurnRole.ENGINEER: "user",
    TurnRole.MODEL: "assistant",
}


class DialogueManager:
    name = "dialogue"
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
        self._base_system = prompt_template or load_prompt(f"dialogue_{prompt_version}")

    def respond(
        self,
        *,
        region: Region,
        reading_body: str,
        prior_turns: list[DialogueMessage],
        engineer_message: str,
    ) -> DialogueResponse:
        # Persistent per-conversation context goes in the system prompt so
        # it is not retransmitted with every turn.
        system = (
            f"{self._base_system}\n\n"
            f"Region label: {region.structural_label}\n\n"
            f"The reading the engineer is reconciling with:\n{reading_body}"
        )

        messages = [
            Message(role=_API_ROLE[turn.role], content=turn.body)
            for turn in prior_turns
        ]
        messages.append(Message(role="user", content=engineer_message))

        completion = self._llm.complete(
            system=system,
            messages=messages,
            model=self._model,
        )
        return DialogueResponse(
            body=completion.text.strip(),
            model_id=completion.model_id,
            prompt_version=self._prompt_version,
        )
