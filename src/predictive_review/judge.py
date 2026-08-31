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

TWO MODES, ONE CONTRACT
=======================
Without `aspects`, the judge answers in prose: `missing_aspects` is a
sentence naming what the teach-back left out. This is the only mode a
diff-sourced region has, because a hunk declares no aspects.

With `aspects`, the judge answers structurally: `missing_aspects` is a
list of ids drawn from the aspects it was given. That list is what a
ledger can act on; prose is not. On FAIL the list must be non-empty and a
subset of what was supplied — anything else is a parse failure, treated
exactly like malformed JSON, because a judge inventing aspect ids is not
a judge whose verdict means anything.

Both modes share the blindness contract above. `aspects` describes the
material, never the engineer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from string import Template

from .llm.client import LLMClient, Message
from .llm.parsing import extract_json
from .llm.prompts import load_prompt
from .domain.aspect import Aspect
from .llm.threshold import judge_guidance
from .storage.models import EngagementThreshold


class JudgeOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class JudgeVerdict:
    outcome: JudgeOutcome
    # Prose (str) in unscoped mode, aspect ids (list) when the region
    # declared aspects, None on PASS. The runtime type is the
    # discriminator; `aspect_scope` on the persisted attempt records which
    # mode produced it.
    missing_aspects: list[str] | str | None
    model_id: str
    prompt_version: str

    @property
    def is_structured(self) -> bool:
        return isinstance(self.missing_aspects, list)


def format_missing_aspects(missing: list[str] | str | None) -> str:
    """Render either verdict shape as one line of human-facing text.

    Aspect ids are terse by design — they are identifiers a ledger keys
    on, not sentences. Presenting the ids is honest about what the judge
    actually returned; expanding them into their claims would require the
    manifest, which the presentation layer does not have.
    """
    if not missing:
        return ""
    if isinstance(missing, list):
        return ", ".join(missing)
    return str(missing)


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
        member_prompt_template: str | None = None,
    ) -> None:
        self._llm = llm
        self._model = model
        self._prompt_version = prompt_version
        self._system_template = prompt_template or load_prompt(
            f"judge_{prompt_version}"
        )
        # Resolved on first aspect-mode call so that constructing a judge
        # with an explicit prose template (as tests do) does not require
        # the member prompt to exist.
        self._member_template = member_prompt_template

    def judge(
        self,
        *,
        reading_body: str,
        teach_back_statement: str,
        engagement_threshold: EngagementThreshold = EngagementThreshold.DEFAULT,
        aspects: list[Aspect] | None = None,
    ) -> JudgeVerdict:
        """Judge coverage. Supplying `aspects` selects structured mode.

        `aspects` must already be narrowed to the session's criterion —
        the judge scores against exactly what it is handed, which is what
        makes a PASS mean "at this scope" (contract invariant 7).
        """
        structured = bool(aspects)
        template = self._resolve_template(structured)
        system = Template(template).safe_substitute(
            engagement_threshold=judge_guidance(engagement_threshold)
        )
        user_content = _build_user_content(
            reading_body=reading_body,
            teach_back_statement=teach_back_statement,
            aspects=aspects if structured else None,
        )
        completion = self._llm.complete(
            system=system,
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

        if structured:
            missing = _parse_structured_missing(
                data.get("missing_aspects"), outcome, aspects or []
            )
        else:
            missing = _parse_prose_missing(data.get("missing_aspects"), outcome)

        return JudgeVerdict(
            outcome=outcome,
            missing_aspects=missing,
            model_id=completion.model_id,
            prompt_version=self._prompt_version_for(structured),
        )

    def _resolve_template(self, structured: bool) -> str:
        if not structured:
            return self._system_template
        if self._member_template is None:
            self._member_template = load_prompt(
                f"judge_member_{self._prompt_version}"
            )
        return self._member_template

    def _prompt_version_for(self, structured: bool) -> str:
        """Record which prompt actually judged this attempt.

        Two prompts can judge the same region across its lifetime, so the
        recorded version has to distinguish them or the provenance lies.
        """
        return f"member_{self._prompt_version}" if structured else self._prompt_version


def _build_user_content(
    *,
    reading_body: str,
    teach_back_statement: str,
    aspects: list[Aspect] | None,
) -> str:
    parts = [f"Reading:\n{reading_body}"]
    if aspects:
        listed = "\n".join(f"- {a.id}: {a.claim}" for a in aspects)
        parts.append(f"Aspects a correct account must cover:\n{listed}")
    parts.append(f"Engineer's teach-back:\n{teach_back_statement}")
    return "\n\n".join(parts)


def _parse_prose_missing(raw: object, outcome: JudgeOutcome) -> str | None:
    if outcome is JudgeOutcome.PASS:
        return None
    if not raw:
        raise ValueError("judge returned FAIL without missing_aspects")
    return raw  # type: ignore[return-value]


def _parse_structured_missing(
    raw: object, outcome: JudgeOutcome, given: list[Aspect]
) -> list[str] | None:
    """P5. A structured verdict is only worth having if it is trustworthy.

    Every deviation here is a parse failure rather than a salvage: a judge
    that returns prose in structured mode, or names an aspect nobody gave
    it, has not answered the question that was asked. Treating that as a
    soft signal would let unverifiable ids reach the ledger.
    """
    if outcome is JudgeOutcome.PASS:
        return None

    if not isinstance(raw, list):
        raise ValueError(
            f"judge returned missing_aspects as {type(raw).__name__} in "
            "structured mode; expected a list of aspect ids"
        )
    if not raw:
        raise ValueError("judge returned FAIL without missing_aspects")
    if not all(isinstance(item, str) for item in raw):
        raise ValueError("judge returned a non-string aspect id")

    allowed = {a.id for a in given}
    unknown = [item for item in raw if item not in allowed]
    if unknown:
        raise ValueError(
            f"judge named aspect id(s) it was not given: "
            f"{', '.join(repr(u) for u in unknown)}; "
            f"given: {', '.join(sorted(allowed))}"
        )

    seen: dict[str, None] = {}
    for item in raw:
        seen.setdefault(item, None)
    return list(seen)
