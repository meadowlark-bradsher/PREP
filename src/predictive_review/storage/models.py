"""ORM models for Predictive Review.

The schema captures the full competency-data surface from day one even though
v1 only renders a subset. The unrendered fields exist so future IRT-style
modeling has a real history to consume instead of a cold start.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
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
    AWAITING_RECONCILIATION = "awaiting_reconciliation"
    IN_DIALOGUE = "in_dialogue"
    AWAITING_DISPOSITION = "awaiting_disposition"
    CLOSED = "closed"


class DispositionStatus(str, enum.Enum):
    ACCEPTED_AS_IS = "accepted_as_is"
    FLAGGED_FOR_REDESIGN = "flagged_for_redesign"
    CLOSED_WITH_DISAGREEMENT = "closed_with_disagreement"


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
    selector_name: Mapped[str] = mapped_column(String(64))
    selector_version: Mapped[str] = mapped_column(String(32))
    reconciliation_layout: Mapped[ReconciliationLayout] = mapped_column(
        Enum(ReconciliationLayout)
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
    entered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

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
    hunk_ref: Mapped[str] = mapped_column(Text)
    selector_rationale: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[RegionStatus] = mapped_column(
        Enum(RegionStatus), default=RegionStatus.AWAITING_RECONCILIATION
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
    FAIL with the judge's explanation of what the reading covers that the
    statement does not.
    """

    __tablename__ = "closure_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    region_id: Mapped[str] = mapped_column(ForeignKey("regions.id"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    teach_back_statement: Mapped[str] = mapped_column(Text)
    verdict: Mapped[ClosureVerdict] = mapped_column(Enum(ClosureVerdict))
    missing_aspects: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
