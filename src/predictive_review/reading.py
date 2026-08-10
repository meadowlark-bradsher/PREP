"""Reading generator.

CONTRACT
========
The reading is the model's account of a region, produced after the
hypothesis phase locks. The signature of generate() encodes the central
independence guarantee: it takes the region only — no hypothesis, no
session, no engineer identity. If the reading sees the hypothesis, it
can accommodate the hypothesis and the calibration the mechanism rests
on collapses. The type signature is the load-bearing piece of that
guarantee; orchestration must not bypass it.

The reading is generated from the code alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain.region import Region
from .llm.client import LLMClient, Message
from .llm.prompts import load_prompt


@dataclass(frozen=True)
class ReadingResult:
    body: str
    model_id: str
    prompt_version: str


class ReadingGenerator:
    name = "reading"
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
        self._system = prompt_template or load_prompt(f"reading_{prompt_version}")

    def generate(self, region: Region) -> ReadingResult:
        user_content = (
            f"Region label: {region.structural_label}\n\n"
            f"Code:\n```\n{region.content.body}\n```"
        )
        completion = self._llm.complete(
            system=self._system,
            messages=[Message(role="user", content=user_content)],
            model=self._model,
        )
        return ReadingResult(
            body=completion.text.strip(),
            model_id=completion.model_id,
            prompt_version=self._prompt_version,
        )
