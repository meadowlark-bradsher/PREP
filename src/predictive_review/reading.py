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

The reading is generated from the region's `body` alone. Not from its
metadata: a member carries the authoring agent's `rationale`, its scores
and its declared aspects alongside the body, and none of that may reach
this prompt. The rationale is the agent's argument for why the region
matters, and a reading written in its light would be arguing rather than
describing — the engineer would then be reconciling against an advocate.
`tests/unit/test_manifest_source.py` pins this.

KIND DISPATCH
=============
`reading_v1` addresses a code hunk and says so ("Code:", "the snippet").
A member's body is prose about a part of the system and may contain no
code at all, so it gets `reading_member_v1` and a neutral framing. An
unrecognised kind is an error rather than a fallback to the code prompt:
silently describing something as code is exactly the failure this
dispatch exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain.region import Region
from .llm.client import LLMClient, Message
from .llm.prompts import load_prompt

CODE_HUNK = "code_hunk"
MEMBER = "member"

# Kind -> the prompt family that addresses it. Adding a content kind means
# adding a prompt written for it; there is deliberately no default.
_PROMPT_FAMILY: dict[str, str] = {
    CODE_HUNK: "reading",
    MEMBER: "reading_member",
}


class UnknownContentKind(ValueError):
    """No prompt is written for this content kind."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        known = ", ".join(sorted(_PROMPT_FAMILY))
        super().__init__(
            f"no reading prompt for content kind {kind!r}; known kinds: {known}"
        )


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
        member_prompt_template: str | None = None,
    ) -> None:
        self._llm = llm
        self._model = model
        self._prompt_version = prompt_version
        self._system = prompt_template or load_prompt(f"reading_{prompt_version}")
        # Resolved on first member region so that constructing a generator
        # with an explicit code template does not require the member prompt.
        self._member_system = member_prompt_template

    def generate(self, region: Region) -> ReadingResult:
        kind = region.content.kind
        if kind not in _PROMPT_FAMILY:
            raise UnknownContentKind(kind)

        system = self._system_for(kind)
        completion = self._llm.complete(
            system=system,
            messages=[
                Message(role="user", content=_user_content(region, kind))
            ],
            model=self._model,
        )
        return ReadingResult(
            body=completion.text.strip(),
            model_id=completion.model_id,
            prompt_version=self._prompt_version_for(kind),
        )

    def _system_for(self, kind: str) -> str:
        if kind == CODE_HUNK:
            return self._system
        if self._member_system is None:
            self._member_system = load_prompt(
                f"reading_member_{self._prompt_version}"
            )
        return self._member_system

    def _prompt_version_for(self, kind: str) -> str:
        """Record which prompt produced the reading.

        Two prompts describe two different kinds of material; a version
        that did not distinguish them would make historical readings
        incomparable in exactly the way prompt_version exists to prevent.
        """
        if kind == CODE_HUNK:
            return self._prompt_version
        return f"member_{self._prompt_version}"


def _user_content(region: Region, kind: str) -> str:
    """Label plus body. Never metadata — see the module docstring."""
    if kind == CODE_HUNK:
        return (
            f"Region label: {region.structural_label}\n\n"
            f"Code:\n```\n{region.content.body}\n```"
        )
    return (
        f"Region label: {region.structural_label}\n\n"
        f"Material:\n{region.content.body}"
    )
