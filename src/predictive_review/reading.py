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
from .llm.client import LLMClient


@dataclass(frozen=True)
class ReadingResult:
    body: str
    model_id: str
    prompt_version: str


class ReadingGenerator:
    name = "reading"
    version = "v1"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def generate(self, region: Region) -> ReadingResult:
        raise NotImplementedError(
            "ReadingGenerator.generate: prompt engineering pending"
        )
