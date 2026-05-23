"""V1 production selector: LLM-judgment over the diff.

Currently a stub. The prompt that lets a model identify load-bearing
regions is the load-bearing prompt of the whole application and is
being engineered in weeks 4–5 per the PM timeline. Until that prompt
exists, this class raises so production paths fail loudly rather than
silently.

For development and tests, use selectors.development.FirstNHunksSelector.
"""

from __future__ import annotations

from ..domain.diff import Diff
from ..domain.region import Region
from ..llm.client import LLMClient
from .base import SelectorContext


class LLMJudgmentSelector:
    name = "llm_judgment"
    version = "v1"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def select(
        self,
        diff: Diff,
        *,
        context: SelectorContext | None = None,
    ) -> list[Region]:
        raise NotImplementedError(
            "LLMJudgmentSelector.select: prompt engineering pending "
            "(see PM doc §'Architecture: region selection')"
        )
