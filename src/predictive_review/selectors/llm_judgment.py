"""LLM-judgment region selector.

The v1 production selector. Asks an LLM to pick 2-4 load-bearing hunks
out of the diff. Per the PM doc, the selection contract is intentionally
narrow — the selector is pluggable so future approaches (entropy ranking,
blame-aware, IRT-driven) drop in as alternative implementations of the
same Protocol without touching the rest of the application.
"""

from __future__ import annotations

from string import Template

from ..domain.diff import Diff, Hunk
from ..domain.region import Region
from ..llm.client import LLMClient, Message
from ..llm.parsing import extract_json
from ..llm.prompts import load_prompt
from ..llm.threshold import selector_guidance
from ..storage.models import EngagementThreshold
from .base import SelectorContext


class LLMJudgmentSelector:
    name = "llm_judgment"
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
        self._system_template = prompt_template or load_prompt(
            f"selector_{prompt_version}"
        )

    def select(
        self,
        diff: Diff,
        *,
        context: SelectorContext | None = None,
    ) -> list[Region]:
        if not diff.hunks:
            return []

        threshold = (
            context.engagement_threshold
            if context is not None
            else EngagementThreshold.DEFAULT
        )
        system = Template(self._system_template).safe_substitute(
            engagement_threshold=selector_guidance(threshold)
        )

        user_content = _format_hunks(diff.hunks)
        completion = self._llm.complete(
            system=system,
            messages=[Message(role="user", content=user_content)],
            model=self._model,
        )
        data = extract_json(completion.text)

        raw_selections = data.get("selections", [])
        if not isinstance(raw_selections, list) or not raw_selections:
            raise ValueError("selector returned no selections")

        regions: list[Region] = []
        for raw in raw_selections:
            idx = int(raw["hunk_index"]) - 1  # prompt uses 1-based indexing
            if not 0 <= idx < len(diff.hunks):
                raise ValueError(
                    f"selector referenced out-of-range hunk_index: "
                    f"{raw['hunk_index']} (have {len(diff.hunks)} hunks)"
                )
            regions.append(
                Region(
                    structural_label=str(raw["structural_label"]).strip(),
                    hunk=diff.hunks[idx],
                    selector_rationale={
                        "rationale": str(raw.get("rationale", "")).strip(),
                        "prompt_version": self._prompt_version,
                        "model_id": completion.model_id,
                    },
                )
            )
        return regions


def _format_hunks(hunks: tuple[Hunk, ...]) -> str:
    """Number hunks 1-based and present each with its file and body.

    The selector references hunks by index in its JSON response; this
    formatting is what the indices refer to.
    """
    parts = []
    for i, h in enumerate(hunks, start=1):
        parts.append(f"### Hunk {i}: {h.file_path}\n```\n{h.text}\n```")
    return "\n\n".join(parts)
