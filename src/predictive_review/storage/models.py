"""ORM models for Predictive Review.

The schema captures the full competency-data surface from day one even though
v1 only renders a subset. The unrendered fields exist so future IRT-style
modeling has a real history to consume instead of a cold start.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class SessionPhase(str, enum.Enum):
    SUBMITTED = "submitted"
    HYPOTHESIS = "hypothesis"
    REVEALED = "revealed"
    RECONCILIATION = "reconciliation"
    COMPLETE = "complete"


class RegionStatus(str, enum.Enum):
    """Region-level lifecycle.

    AWAITING_HYPOTHESIS is the initial state — the region exists but
    the engineer hasn't written a hypothesis yet. (The MVP used
    AWAITING_RECONCILIATION as the initial state, which was a misnomer.)

    AWAITING_REVEAL_CHOICE is the v1.5 state between lock_and_reveal
    and the three-way choice. The engineer sees their locked hypothesis
    + the reading + three buttons (engage / acknowledge / defer) before
    being routed onward.
    """

    AWAITING_HYPOTHESIS = "awaiting_hypothesis"
    AWAITING_REVEAL_CHOICE = "awaiting_reveal_choice"
    AWAITING_RECONCILIATION = "awaiting_reconciliation"
    IN_DIALOGUE = "in_dialogue"
    AWAITING_DISPOSITION = "awaiting_disposition"
    CLOSED = "closed"


class DispositionStatus(str, enum.Enum):
    """What the engineer decided about the actual code change.

    Orthogonal to ClosureMode (how the region's understanding-check closed):
    an engineer can disagree with the model's reading and still accept the
    code as-is, or agree with the reading and flag for redesign.
    """

    ACCEPTED_AS_IS = "accepted_as_is"
    FLAGGED_FOR_REDESIGN = "flagged_for_redesign"


class ClosureMode(str, enum.Enum):
    """How a region reached AWAITING_DISPOSITION.

    Four ways a region can close in v1.5:
      - JUDGE_PASSED: the closure judge approved the engineer's teach-back.
      - ENGINEER_DISAGREED: the engineer thinks the reading itself is wrong.
        A `regions.disagreement_reason` may carry free-text explanation.
      - ACKNOWLEDGED: the engineer chose at reveal not to engage deeply.
        `regions.acknowledgment_note` carries the required one-line note.
      - ENGINEER_OVERRODE: the engineer accepts the reading but judges the
        coverage being demanded exceeds what the region is worth to them.
        `regions.override_reason` records value/toil/difficulty.
    """

    JUDGE_PASSED = "judge_passed"
    ENGINEER_DISAGREED = "engineer_disagreed"
    ACKNOWLEDGED = "acknowledged"
    ENGINEER_OVERRODE = "engineer_overrode"


class EngagementThreshold(str, enum.Enum):
    """Session-level coarse knob for selector aggressiveness and judge strictness.

    Set at launch. Flows into selector and judge prompts as a template
    variable. LOAD_BEARING_ONLY surfaces fewer regions and applies a
    lighter coverage bar; THOROUGH surfaces more and applies a stricter
    bar; DEFAULT sits in the middle.
    """

    LOAD_BEARING_ONLY = "load_bearing_only"
    DEFAULT = "default"
    THOROUGH = "thorough"


class OverrideReason(str, enum.Enum):
    """Why the engineer overrode the engagement demand on a region.

    Three orthogonal axes that produce the same surface symptom
    ("I don't want to engage with this") but mean different things in
    the artifact. Collected for future calibration; no model consumes
    them in v1.5.
    """

    VALUE = "value"
    TOIL = "toil"
    DIFFICULTY = "difficulty"


class ClosureVerdict(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"


class DialogueRole(str, enum.Enum):
    ENGINEER = "engineer"
    MODEL = "model"


class ReconciliationLayout(str, enum.Enum):
    INLINE_HUNK = "inline_hunk"
    NO_HUNK = "no_hunk"


class Engineer(Base):
    __tablename__ = "engineers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    identifier: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    sessions: Mapped[list["Session"]] = relationship(back_populates="engineer")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    engineer_id: Mapped[str] = mapped_column(ForeignKey("engineers.id"))
    diff_text: Mapped[str] = mapped_column(Text)
    # Provenance: populated when the diff came from --commit or --range,
    # nullable when the diff was pasted or piped from stdin.
    source_commit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_range: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    selector_name: Mapped[str] = mapped_column(String(64))
    selector_version: Mapped[str] = mapped_column(String(32))
    # The load type this session ordered its regions by, when the content
    # source declared criteria to choose among. NULL for diff sessions,
    # which have no criteria and no ordering to override.
    criterion: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reconciliation_layout: Mapped[ReconciliationLayout] = mapped_column(
        Enum(ReconciliationLayout)
    )
    engagement_threshold: Mapped[EngagementThreshold] = mapped_column(
        Enum(EngagementThreshold),
        default=EngagementThreshold.DEFAULT,
        # SQLAlchemy's Enum type stores the enum NAME (uppercase) by default,
        # not the value. server_default must match — emitting the lowercase
        # .value would violate the CHECK constraint on backfilled rows.
        server_default=EngagementThreshold.DEFAULT.name,
    )
    current_phase: Mapped[SessionPhase] = mapped_column(
        Enum(SessionPhase), default=SessionPhase.SUBMITTED
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    engineer: Mapped[Engineer] = relationship(back_populates="sessions")
    regions: Mapped[list["Region"]] = relationship(
        back_populates="session", order_by="Region.ordinal"
    )
    phase_events: Mapped[list["PhaseEvent"]] = relationship(back_populates="session")


class PhaseEvent(Base):
    """Audit log of phase transitions, one row per (session, phase) entry.

    `Session.current_phase` is the fast read for routing; this table is the
    timeline that future analytics consume for time-on-task signals.
    """

    __tablename__ = "phase_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    phase: Mapped[SessionPhase] = mapped_column(Enum(SessionPhase))
    # Python-side default gives microsecond precision so events written in
    # the same transaction can be ordered. SQLite CURRENT_TIMESTAMP only
    # resolves to seconds, which ties events that were inserted moments
    # apart and leaves their ordering implementation-defined.
    entered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    session: Mapped[Session] = relationship(back_populates="phase_events")

    __table_args__ = (
        UniqueConstraint("session_id", "phase", name="uq_phase_event_per_session"),
    )


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    structural_label: Mapped[str] = mapped_column(Text)
    # Widened carrier: {"kind", "body", "metadata"}. A code_hunk stores diff
    # geometry under metadata; other kinds (glossary_term, concept) store
    # their own. Was a diff-only `hunk` column before the RegionContent
    # widening (expand: add+backfill content; contract: drop hunk).
    content: Mapped[dict] = mapped_column(JSON)
    selector_rationale: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[RegionStatus] = mapped_column(
        Enum(RegionStatus), default=RegionStatus.AWAITING_HYPOTHESIS
    )
    closure_mode: Mapped[Optional[ClosureMode]] = mapped_column(
        Enum(ClosureMode), nullable=True
    )
    # Override reason — populated only when closure_mode = ENGINEER_OVERRODE.
    # Records which of value/toil/difficulty drove the engineer's decision
    # to override the engagement demand on this region.
    override_reason: Mapped[Optional[OverrideReason]] = mapped_column(
        Enum(OverrideReason), nullable=True
    )
    # Free-text reason — populated only when closure_mode = ENGINEER_DISAGREED.
    # The engineer's account of why the reading itself is wrong.
    disagreement_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # One-line note — required when closure_mode = ACKNOWLEDGED. The
    # engineer's brief explanation of why they chose not to engage deeply.
    acknowledgment_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Deferral is a region property, not a session phase. A region with
    # is_deferred = True blocks session completion until the engineer
    # revisits it and picks engage/acknowledge from the three-way choice.
    is_deferred: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    session: Mapped[Session] = relationship(back_populates="regions")
    hypothesis_revisions: Mapped[list["HypothesisRevision"]] = relationship(
        back_populates="region", order_by="HypothesisRevision.revision_number"
    )
    reading: Mapped[Optional["Reading"]] = relationship(
        back_populates="region", uselist=False
    )
    reconciliation: Mapped[Optional["Reconciliation"]] = relationship(
        back_populates="region", uselist=False
    )
    dialogue_turns: Mapped[list["DialogueTurn"]] = relationship(
        back_populates="region", order_by="DialogueTurn.turn_index"
    )
    closure_attempts: Mapped[list["ClosureAttempt"]] = relationship(
        back_populates="region", order_by="ClosureAttempt.attempt_number"
    )
    disposition: Mapped[Optional["Disposition"]] = relationship(
        back_populates="region", uselist=False
    )

    __table_args__ = (
        UniqueConstraint("session_id", "ordinal", name="uq_region_ordinal_per_session"),
    )


class HypothesisRevision(Base):
    """Every edit to a region's hypothesis during the hypothesis phase.

    The partial unique index enforces at most one `is_locked=True` row per
    region — this is the DB-level realization of commit-before-reveal.
    """

    __tablename__ = "hypothesis_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    region_id: Mapped[str] = mapped_column(ForeignKey("regions.id"))
    revision_number: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    is_locked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    region: Mapped[Region] = relationship(back_populates="hypothesis_revisions")

    __table_args__ = (
        UniqueConstraint(
            "region_id", "revision_number", name="uq_hypothesis_revision_number"
        ),
        Index(
            "uq_hypothesis_locked_per_region",
            "region_id",
            unique=True,
            sqlite_where=text("is_locked = 1"),
        ),
    )


class Reading(Base):
    __tablename__ = "readings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    region_id: Mapped[str] = mapped_column(ForeignKey("regions.id"), unique=True)
    body: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    region: Mapped[Region] = relationship(back_populates="reading")


class Reconciliation(Base):
    __tablename__ = "reconciliations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    region_id: Mapped[str] = mapped_column(ForeignKey("regions.id"), unique=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    region: Mapped[Region] = relationship(back_populates="reconciliation")


class DialogueTurn(Base):
    __tablename__ = "dialogue_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    region_id: Mapped[str] = mapped_column(ForeignKey("regions.id"))
    turn_index: Mapped[int] = mapped_column(Integer)
    role: Mapped[DialogueRole] = mapped_column(Enum(DialogueRole))
    body: Mapped[str] = mapped_column(Text)
    model_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    region: Mapped[Region] = relationship(back_populates="dialogue_turns")

    __table_args__ = (
        UniqueConstraint("region_id", "turn_index", name="uq_dialogue_turn_index"),
    )


class ClosureAttempt(Base):
    """One row per judge invocation on a region.

    `teach_back_statement` is captured per attempt because the engineer may
    revise it between attempts during dialogue. `missing_aspects` is set on
    FAIL with what the reading covers that the statement does not.

    It has two shapes. When the region declared aspects, it is a list of
    aspect ids the judge was given; otherwise it is a sentence of prose.
    `aspect_scope` is the discriminator: non-NULL exactly when the judge ran
    in structured mode, and holding the ids it was allowed to name.

    `criterion` records the load type in scope for the attempt. Together
    with the verdict that is the provenance contract invariant 7 requires:
    a PASS is `teach-back-verified@<criterion>`, never bare, so a ledger
    cannot later promote scope-narrowed evidence as whole-member evidence.
    """

    __tablename__ = "closure_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    region_id: Mapped[str] = mapped_column(ForeignKey("regions.id"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    teach_back_statement: Mapped[str] = mapped_column(Text)
    verdict: Mapped[ClosureVerdict] = mapped_column(Enum(ClosureVerdict))
    missing_aspects: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    criterion: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    aspect_scope: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    judge_model_id: Mapped[str] = mapped_column(String(64))
    judge_prompt_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    region: Mapped[Region] = relationship(back_populates="closure_attempts")

    __table_args__ = (
        UniqueConstraint(
            "region_id", "attempt_number", name="uq_closure_attempt_number"
        ),
    )


class Disposition(Base):
    __tablename__ = "dispositions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    region_id: Mapped[str] = mapped_column(ForeignKey("regions.id"), unique=True)
    status: Mapped[DispositionStatus] = mapped_column(Enum(DispositionStatus))
    justification: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    region: Mapped[Region] = relationship(back_populates="disposition")
