"""Session orchestration.

Walks a submitted diff through the user-flow phases described in the PM doc:

    SUBMITTED → HYPOTHESIS → RECONCILIATION → COMPLETE

REVEALED is defined in the SessionPhase enum but collapsed into RECONCILIATION
here — the reveal is an atomic action that produces readings and immediately
leaves the session in reconciliation. The phase column never holds REVEALED
in practice; the enum keeps it as a reserved name.

Each method opens its own DB session and manages its own transaction. The
service holds no per-request state.

LOAD-BEARING PROPERTIES ENFORCED HERE
=====================================

  - Commit before reveal: hypothesis writes are accepted only while the
    session is in HYPOTHESIS phase. The partial unique index on
    hypothesis_revisions.region_id WHERE is_locked = 1 guarantees at most
    one locked revision per region even under concurrent writes.

  - Independence of LLM components: the orchestrator passes the reading
    generator only the Region, the judge only the (reading_body,
    teach_back_statement) pair, and the dialogue manager only the
    per-region prior turns. None of these can see the others' state
    because the LLM-call type signatures don't allow it.

  - Closure by coverage: a region only moves to AWAITING_DISPOSITION when
    the judge passes it, or when the engineer explicitly chooses to close
    with disagreement.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession, selectinload

logger = logging.getLogger("predictive_review.sessions")

from ..content_sources.base import ContentSource
from ..dialogue import DialogueManager, DialogueMessage, TurnRole
from ..domain.content import RegionContent
from ..domain.region import Region as RegionView
from ..judge import ClosureJudge, JudgeOutcome, JudgeVerdict
from ..reading import ReadingGenerator
from ..selectors.base import SelectorContext
from ..selectors.registry import SelectorRegistry
from ..storage.models import (
    ClosureAttempt,
    ClosureMode,
    ClosureVerdict,
    DialogueRole,
    DialogueTurn,
    Disposition,
    DispositionStatus,
    EngagementThreshold,
    Engineer,
    HypothesisRevision,
    OverrideReason,
    PhaseEvent,
    Reading,
    Reconciliation,
    ReconciliationLayout,
    Region,
    RegionStatus,
    Session,
    SessionPhase,
)
from .errors import (
    HypothesisAlreadyLocked,
    InvalidPhaseTransition,
    InvalidRegionStatus,
    RegionNotFound,
    SessionNotFound,
)


@dataclass(frozen=True)
class ClosureAttemptResult:
    verdict: JudgeOutcome
    missing_aspects: str | None
    attempt_number: int


@dataclass(frozen=True)
class DialogueTurnResult:
    model_body: str
    turn_index: int


@dataclass(frozen=True)
class SessionSnapshot:
    """Read-only view of a session header for callers outside the service."""

    id: str
    current_phase: "SessionPhase"
    engineer_identifier: str
    selector_name: str
    selector_version: str
    layout: "ReconciliationLayout"
    source_commit: str | None
    source_range: str | None


@dataclass(frozen=True)
class DialogueTurnSnapshot:
    role: "TurnRole"
    body: str
    turn_index: int


@dataclass(frozen=True)
class ClosureAttemptSnapshot:
    attempt_number: int
    teach_back_statement: str
    verdict: "ClosureVerdict"
    missing_aspects: str | None


@dataclass(frozen=True)
class DispositionSnapshot:
    status: "DispositionStatus"
    justification: str | None


@dataclass(frozen=True)
class RegionSnapshot:
    """Read-only view of a region for callers outside the service.

    The CLI and the future web layer use these to iterate regions without
    reaching into the ORM directly. Snapshots are point-in-time — re-read
    after each state-changing call.
    """

    id: str
    ordinal: int
    structural_label: str
    hunk_text: str
    status: "RegionStatus"
    closure_mode: "ClosureMode | None"
    is_deferred: bool = False
    override_reason: "OverrideReason | None" = None
    disagreement_reason: str | None = None
    acknowledgment_note: str | None = None


class SessionService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], DbSession],
        selector_registry: SelectorRegistry,
        reading_generator: ReadingGenerator,
        closure_judge: ClosureJudge,
        dialogue_manager: DialogueManager,
    ) -> None:
        self._session_factory = session_factory
        self._selectors = selector_registry
        self._reading = reading_generator
        self._judge = closure_judge
        self._dialogue = dialogue_manager

    # --- phase 1: submit ----------------------------------------------------

    def submit(
        self,
        *,
        source: ContentSource,
        engineer_identifier: str,
        selector_name: str,
        layout: ReconciliationLayout,
        engagement_threshold: EngagementThreshold = EngagementThreshold.DEFAULT,
        source_commit: str | None = None,
        source_range: str | None = None,
    ) -> str:
        """Produce candidate content, run the selector, persist session + regions.

        Returns the new session id; the session is left in HYPOTHESIS phase.
        The source has already resolved its own material — this method
        never parses, reads the filesystem, or shells out.
        source_commit / source_range carry the diff's git provenance when
        the diff came from `git show <sha>` or `git diff <range>`; both
        are None when the diff was pasted.
        """
        contents = source.produce()
        selector = self._selectors.get(selector_name)
        candidate_regions = selector.select(
            contents,
            context=SelectorContext(
                engineer_identifier=engineer_identifier,
                engagement_threshold=engagement_threshold,
            ),
        )
        if not candidate_regions:
            raise ValueError("selector returned no regions")

        with self._session_factory() as db:
            engineer = self._get_or_create_engineer(db, engineer_identifier)
            session = Session(
                engineer_id=engineer.id,
                diff_text=source.raw_text,
                source_commit=source_commit,
                source_range=source_range,
                selector_name=selector.name,
                selector_version=selector.version,
                reconciliation_layout=layout,
                engagement_threshold=engagement_threshold,
                current_phase=SessionPhase.HYPOTHESIS,
            )
            db.add(session)
            db.flush()

            db.add(PhaseEvent(session_id=session.id, phase=SessionPhase.SUBMITTED))
            db.add(PhaseEvent(session_id=session.id, phase=SessionPhase.HYPOTHESIS))

            for ordinal, region_view in enumerate(candidate_regions):
                db.add(
                    Region(
                        session_id=session.id,
                        ordinal=ordinal,
                        structural_label=region_view.structural_label,
                        content=region_view.content.to_dict(),
                        selector_rationale=dict(region_view.selector_rationale),
                        status=RegionStatus.AWAITING_HYPOTHESIS,
                    )
                )

            db.commit()
            logger.info(
                "session %s submitted: engineer=%s selector=%s threshold=%s regions=%d",
                session.id,
                engineer_identifier,
                selector.name,
                engagement_threshold.value,
                len(candidate_regions),
            )
            return session.id

    # --- phase 2: hypothesis ------------------------------------------------

    def save_hypothesis(
        self,
        *,
        session_id: str,
        region_id: str,
        body: str,
    ) -> None:
        with self._session_factory() as db:
            session = self._get_session(db, session_id)
            self._require_phase(session, SessionPhase.HYPOTHESIS)
            region = self._get_region(db, region_id, session_id)

            latest = self._latest_hypothesis(db, region.id)
            if latest is not None and latest.is_locked:
                raise HypothesisAlreadyLocked(
                    f"region {region_id} hypothesis is locked"
                )
            next_number = (latest.revision_number + 1) if latest else 1

            db.add(
                HypothesisRevision(
                    region_id=region.id,
                    revision_number=next_number,
                    body=body,
                    is_locked=False,
                )
            )
            db.commit()

    def lock_and_reveal(self, *, session_id: str) -> None:
        """Lock the latest hypothesis per region, generate readings, transition.

        Reading generation happens after the lock. The reading generator sees
        only the Region — never the locked hypothesis.
        """
        with self._session_factory() as db:
            session = self._get_session_with_regions(db, session_id)
            self._require_phase(session, SessionPhase.HYPOTHESIS)

            for region in session.regions:
                latest = self._latest_hypothesis(db, region.id)
                if latest is None:
                    raise InvalidPhaseTransition(
                        f"region {region.id} has no hypothesis to lock"
                    )
                latest.is_locked = True

            # Build RegionViews from persisted code state only — no path to
            # hypotheses, even though they live on the same ORM graph.
            for region in session.regions:
                region_view = _to_region_view(region)
                reading_result = self._reading.generate(region_view)
                db.add(
                    Reading(
                        region_id=region.id,
                        body=reading_result.body,
                        model_id=reading_result.model_id,
                        prompt_version=reading_result.prompt_version,
                    )
                )
                # v1.5: regions land at the three-way choice, not reconciliation
                region.status = RegionStatus.AWAITING_REVEAL_CHOICE

            session.current_phase = SessionPhase.RECONCILIATION
            db.add(
                PhaseEvent(
                    session_id=session.id, phase=SessionPhase.RECONCILIATION
                )
            )
            db.commit()
            logger.info(
                "session %s reveal: locked hypotheses and generated %d readings",
                session.id,
                len(session.regions),
            )

    # --- phase 3: reconciliation -------------------------------------------

    def submit_reconciliation(
        self,
        *,
        session_id: str,
        region_id: str,
        body: str,
    ) -> ClosureAttemptResult:
        with self._session_factory() as db:
            session = self._get_session(db, session_id)
            self._require_phase(session, SessionPhase.RECONCILIATION)
            region = self._get_region(db, region_id, session_id)
            self._require_region_status(region, RegionStatus.AWAITING_RECONCILIATION)

            reading = self._fetch_reading(db, region.id)
            if reading is None:
                raise InvalidPhaseTransition(
                    f"region {region_id} has no reading; lock_and_reveal first"
                )

            db.add(Reconciliation(region_id=region.id, body=body))
            return self._judge_and_persist(
                db=db,
                region=region,
                reading_body=reading.body,
                teach_back_statement=body,
                attempt_number=1,
                engagement_threshold=session.engagement_threshold,
            )

    def dialogue_turn(
        self,
        *,
        session_id: str,
        region_id: str,
        engineer_message: str,
    ) -> DialogueTurnResult:
        with self._session_factory() as db:
            self._get_session(db, session_id)
            region = self._get_region(db, region_id, session_id)
            self._require_region_status(region, RegionStatus.IN_DIALOGUE)

            reading = self._fetch_reading(db, region.id)
            assert reading is not None  # IN_DIALOGUE implies a reading exists

            prior_turns = self._fetch_dialogue_turns(db, region.id)
            engineer_index = len(prior_turns) + 1

            db.add(
                DialogueTurn(
                    region_id=region.id,
                    turn_index=engineer_index,
                    role=DialogueRole.ENGINEER,
                    body=engineer_message,
                )
            )

            response = self._dialogue.respond(
                region=_to_region_view(region),
                reading_body=reading.body,
                prior_turns=[
                    DialogueMessage(role=_orm_to_turn_role(t.role), body=t.body)
                    for t in prior_turns
                ],
                engineer_message=engineer_message,
            )

            model_index = engineer_index + 1
            db.add(
                DialogueTurn(
                    region_id=region.id,
                    turn_index=model_index,
                    role=DialogueRole.MODEL,
                    body=response.body,
                    model_id=response.model_id,
                    prompt_version=response.prompt_version,
                )
            )
            db.commit()
            return DialogueTurnResult(
                model_body=response.body, turn_index=model_index
            )

    def submit_revised_teach_back(
        self,
        *,
        session_id: str,
        region_id: str,
        body: str,
    ) -> ClosureAttemptResult:
        with self._session_factory() as db:
            session = self._get_session(db, session_id)
            region = self._get_region(db, region_id, session_id)
            self._require_region_status(region, RegionStatus.IN_DIALOGUE)

            reading = self._fetch_reading(db, region.id)
            assert reading is not None

            latest = self._latest_closure_attempt(db, region.id)
            assert latest is not None
            next_number = latest.attempt_number + 1

            return self._judge_and_persist(
                db=db,
                region=region,
                reading_body=reading.body,
                teach_back_statement=body,
                attempt_number=next_number,
                engagement_threshold=session.engagement_threshold,
            )

    def close_with_disagreement(
        self,
        *,
        session_id: str,
        region_id: str,
        reason: str | None = None,
    ) -> None:
        """Engineer thinks the reading itself is wrong. Optional free-text
        reason is persisted on the region for the artifact."""
        with self._session_factory() as db:
            self._get_session(db, session_id)
            region = self._get_region(db, region_id, session_id)
            self._require_region_status(region, RegionStatus.IN_DIALOGUE)

            region.status = RegionStatus.AWAITING_DISPOSITION
            region.closure_mode = ClosureMode.ENGINEER_DISAGREED
            if reason is not None and reason.strip():
                region.disagreement_reason = reason.strip()
            db.commit()

    def submit_override(
        self,
        *,
        session_id: str,
        region_id: str,
        reason: OverrideReason,
    ) -> None:
        """Engineer accepts the reading but overrides the engagement demand.

        Records which of value / toil / difficulty drove the decision so a
        future calibration system has structured signal rather than a binary.
        """
        with self._session_factory() as db:
            self._get_session(db, session_id)
            region = self._get_region(db, region_id, session_id)
            self._require_region_status(region, RegionStatus.IN_DIALOGUE)

            region.status = RegionStatus.AWAITING_DISPOSITION
            region.closure_mode = ClosureMode.ENGINEER_OVERRODE
            region.override_reason = reason
            db.commit()

    # --- the three-way choice (post-reveal) --------------------------------

    def engage_region(self, *, session_id: str, region_id: str) -> None:
        """Engineer chose engage — proceed to reconciliation. Clears any
        prior deferral on this region."""
        with self._session_factory() as db:
            self._get_session(db, session_id)
            region = self._get_region(db, region_id, session_id)
            self._require_region_status(region, RegionStatus.AWAITING_REVEAL_CHOICE)
            region.is_deferred = False
            region.status = RegionStatus.AWAITING_RECONCILIATION
            db.commit()

    def acknowledge_region(
        self,
        *,
        session_id: str,
        region_id: str,
        note: str,
    ) -> None:
        """Engineer chose acknowledge — close without engaging deeply.

        A short note is required; it is the appropriately-sized friction the
        spec calls for, so that disengagement remains a deliberate act.
        """
        if not note or not note.strip():
            raise ValueError("acknowledgment_note is required")
        with self._session_factory() as db:
            self._get_session(db, session_id)
            region = self._get_region(db, region_id, session_id)
            self._require_region_status(region, RegionStatus.AWAITING_REVEAL_CHOICE)
            region.is_deferred = False
            region.status = RegionStatus.AWAITING_DISPOSITION
            region.closure_mode = ClosureMode.ACKNOWLEDGED
            region.acknowledgment_note = note.strip()
            db.commit()

    def defer_region(self, *, session_id: str, region_id: str) -> None:
        """Engineer chose defer — revisit this region later. Sets the
        deferral flag; status stays at AWAITING_REVEAL_CHOICE so the
        three-way choice re-presents on revisit."""
        with self._session_factory() as db:
            self._get_session(db, session_id)
            region = self._get_region(db, region_id, session_id)
            self._require_region_status(region, RegionStatus.AWAITING_REVEAL_CHOICE)
            region.is_deferred = True
            db.commit()

    def revisit_deferred_region(
        self, *, session_id: str, region_id: str
    ) -> None:
        """Engineer is coming back to a deferred region. Clears the flag;
        the next action is the three-way choice again."""
        with self._session_factory() as db:
            self._get_session(db, session_id)
            region = self._get_region(db, region_id, session_id)
            self._require_region_status(region, RegionStatus.AWAITING_REVEAL_CHOICE)
            if not region.is_deferred:
                raise InvalidRegionStatus(
                    f"region {region_id} is not deferred; nothing to revisit"
                )
            region.is_deferred = False
            db.commit()

    # --- phase 4: disposition ----------------------------------------------

    def set_disposition(
        self,
        *,
        session_id: str,
        region_id: str,
        status: DispositionStatus,
        justification: str | None = None,
    ) -> None:
        if status is DispositionStatus.FLAGGED_FOR_REDESIGN and not justification:
            raise ValueError("flagged-for-redesign requires a justification")

        with self._session_factory() as db:
            session = self._get_session_with_regions(db, session_id)
            region = self._get_region(db, region_id, session_id)
            self._require_region_status(region, RegionStatus.AWAITING_DISPOSITION)

            db.add(
                Disposition(
                    region_id=region.id,
                    status=status,
                    justification=justification,
                )
            )
            region.status = RegionStatus.CLOSED

            # Session completes only when every region is CLOSED and no
            # region is deferred. The all-CLOSED check would catch deferrals
            # implicitly (deferred regions sit at AWAITING_REVEAL_CHOICE),
            # but the explicit deferral check is honest about why.
            all_closed = all(
                r.status is RegionStatus.CLOSED for r in session.regions
            )
            any_deferred = any(r.is_deferred for r in session.regions)
            if all_closed and not any_deferred:
                session.current_phase = SessionPhase.COMPLETE
                db.add(
                    PhaseEvent(
                        session_id=session.id, phase=SessionPhase.COMPLETE
                    )
                )

            db.commit()

    # --- read-side --------------------------------------------------------

    def list_regions(self, session_id: str) -> list[RegionSnapshot]:
        with self._session_factory() as db:
            session = self._get_session_with_regions(db, session_id)
            return [
                RegionSnapshot(
                    id=r.id,
                    ordinal=r.ordinal,
                    structural_label=r.structural_label,
                    # Presentation-facing field name is still hunk_text; it is
                    # sourced from the widened content body. Renaming it belongs
                    # to the presentation-layer generalization, kept separate here.
                    hunk_text=(r.content or {}).get("body", ""),
                    status=r.status,
                    closure_mode=r.closure_mode,
                    is_deferred=r.is_deferred,
                    override_reason=r.override_reason,
                    disagreement_reason=r.disagreement_reason,
                    acknowledgment_note=r.acknowledgment_note,
                )
                for r in session.regions
            ]

    def get_session(self, session_id: str) -> SessionSnapshot | None:
        with self._session_factory() as db:
            session = db.get(Session, session_id)
            if session is None:
                return None
            engineer = db.get(Engineer, session.engineer_id)
            return SessionSnapshot(
                id=session.id,
                current_phase=session.current_phase,
                engineer_identifier=engineer.identifier if engineer else "",
                selector_name=session.selector_name,
                selector_version=session.selector_version,
                layout=session.reconciliation_layout,
                source_commit=session.source_commit,
                source_range=session.source_range,
            )

    def get_region(
        self, session_id: str, region_id: str
    ) -> RegionSnapshot | None:
        for r in self.list_regions(session_id):
            if r.id == region_id:
                return r
        return None

    def get_reconciliation(self, region_id: str) -> str | None:
        with self._session_factory() as db:
            stmt = select(Reconciliation).where(
                Reconciliation.region_id == region_id
            )
            row = db.execute(stmt).scalar_one_or_none()
            return row.body if row else None

    def get_dialogue_turns(
        self, region_id: str
    ) -> list[DialogueTurnSnapshot]:
        with self._session_factory() as db:
            turns = self._fetch_dialogue_turns(db, region_id)
            return [
                DialogueTurnSnapshot(
                    role=_orm_to_turn_role(t.role),
                    body=t.body,
                    turn_index=t.turn_index,
                )
                for t in turns
            ]

    def get_closure_attempts(
        self, region_id: str
    ) -> list[ClosureAttemptSnapshot]:
        with self._session_factory() as db:
            stmt = (
                select(ClosureAttempt)
                .where(ClosureAttempt.region_id == region_id)
                .order_by(ClosureAttempt.attempt_number.asc())
            )
            return [
                ClosureAttemptSnapshot(
                    attempt_number=a.attempt_number,
                    teach_back_statement=a.teach_back_statement,
                    verdict=a.verdict,
                    missing_aspects=a.missing_aspects,
                )
                for a in db.execute(stmt).scalars()
            ]

    def get_disposition(
        self, region_id: str
    ) -> DispositionSnapshot | None:
        with self._session_factory() as db:
            stmt = select(Disposition).where(
                Disposition.region_id == region_id
            )
            row = db.execute(stmt).scalar_one_or_none()
            return (
                DispositionSnapshot(
                    status=row.status, justification=row.justification
                )
                if row
                else None
            )

    def get_reading(self, region_id: str) -> str | None:
        with self._session_factory() as db:
            reading = self._fetch_reading(db, region_id)
            return reading.body if reading else None

    def get_current_hypothesis_text(self, region_id: str) -> str | None:
        """Latest hypothesis revision body, locked or not.

        For the in-flight hypothesis tabs, where nothing is locked yet,
        this surfaces the engineer's most recent draft so refreshes and
        re-renders preserve their typing. After reveal, this returns the
        same thing as get_locked_hypothesis because the locked revision
        is also the latest.
        """
        with self._session_factory() as db:
            latest = self._latest_hypothesis(db, region_id)
            return latest.body if latest else None

    def get_locked_hypothesis(self, region_id: str) -> str | None:
        with self._session_factory() as db:
            stmt = (
                select(HypothesisRevision)
                .where(HypothesisRevision.region_id == region_id)
                .where(HypothesisRevision.is_locked.is_(True))
            )
            rev = db.execute(stmt).scalar_one_or_none()
            return rev.body if rev else None

    # --- helpers -----------------------------------------------------------

    def _get_or_create_engineer(
        self, db: DbSession, identifier: str
    ) -> Engineer:
        stmt = select(Engineer).where(Engineer.identifier == identifier)
        engineer = db.execute(stmt).scalar_one_or_none()
        if engineer is None:
            engineer = Engineer(identifier=identifier)
            db.add(engineer)
            db.flush()
        return engineer

    def _get_session(self, db: DbSession, session_id: str) -> Session:
        session = db.get(Session, session_id)
        if session is None:
            raise SessionNotFound(session_id)
        return session

    def _get_session_with_regions(
        self, db: DbSession, session_id: str
    ) -> Session:
        stmt = (
            select(Session)
            .options(selectinload(Session.regions))
            .where(Session.id == session_id)
        )
        session = db.execute(stmt).scalar_one_or_none()
        if session is None:
            raise SessionNotFound(session_id)
        return session

    def _get_region(
        self, db: DbSession, region_id: str, session_id: str
    ) -> Region:
        region = db.get(Region, region_id)
        if region is None or region.session_id != session_id:
            raise RegionNotFound(region_id)
        return region

    def _require_phase(
        self, session: Session, expected: SessionPhase
    ) -> None:
        if session.current_phase is not expected:
            raise InvalidPhaseTransition(
                f"expected phase {expected.value}, "
                f"session is in {session.current_phase.value}"
            )

    def _require_region_status(
        self, region: Region, expected: RegionStatus
    ) -> None:
        if region.status is not expected:
            raise InvalidRegionStatus(
                f"expected status {expected.value}, "
                f"region is in {region.status.value}"
            )

    def _latest_hypothesis(
        self, db: DbSession, region_id: str
    ) -> HypothesisRevision | None:
        stmt = (
            select(HypothesisRevision)
            .where(HypothesisRevision.region_id == region_id)
            .order_by(HypothesisRevision.revision_number.desc())
            .limit(1)
        )
        return db.execute(stmt).scalar_one_or_none()

    def _fetch_reading(
        self, db: DbSession, region_id: str
    ) -> Reading | None:
        stmt = select(Reading).where(Reading.region_id == region_id)
        return db.execute(stmt).scalar_one_or_none()

    def _fetch_dialogue_turns(
        self, db: DbSession, region_id: str
    ) -> list[DialogueTurn]:
        stmt = (
            select(DialogueTurn)
            .where(DialogueTurn.region_id == region_id)
            .order_by(DialogueTurn.turn_index.asc())
        )
        return list(db.execute(stmt).scalars())

    def _latest_closure_attempt(
        self, db: DbSession, region_id: str
    ) -> ClosureAttempt | None:
        stmt = (
            select(ClosureAttempt)
            .where(ClosureAttempt.region_id == region_id)
            .order_by(ClosureAttempt.attempt_number.desc())
            .limit(1)
        )
        return db.execute(stmt).scalar_one_or_none()

    def _judge_and_persist(
        self,
        *,
        db: DbSession,
        region: Region,
        reading_body: str,
        teach_back_statement: str,
        attempt_number: int,
        engagement_threshold: EngagementThreshold,
    ) -> ClosureAttemptResult:
        verdict: JudgeVerdict = self._judge.judge(
            reading_body=reading_body,
            teach_back_statement=teach_back_statement,
            engagement_threshold=engagement_threshold,
        )
        db.add(
            ClosureAttempt(
                region_id=region.id,
                attempt_number=attempt_number,
                teach_back_statement=teach_back_statement,
                verdict=ClosureVerdict(verdict.outcome.value),
                missing_aspects=verdict.missing_aspects,
                judge_model_id=verdict.model_id,
                judge_prompt_version=verdict.prompt_version,
            )
        )

        if verdict.outcome is JudgeOutcome.PASS:
            region.status = RegionStatus.AWAITING_DISPOSITION
            region.closure_mode = ClosureMode.JUDGE_PASSED
        else:
            region.status = RegionStatus.IN_DIALOGUE

        db.commit()
        return ClosureAttemptResult(
            verdict=verdict.outcome,
            missing_aspects=verdict.missing_aspects,
            attempt_number=attempt_number,
        )


def _to_region_view(region: Region) -> RegionView:
    """Build an in-memory RegionView from a persisted ORM Region.

    The reading generator and dialogue manager must not see hypotheses
    even though hypotheses live on the same ORM graph; constructing a
    fresh RegionView with only the persisted content guarantees the LLM
    call has no traversal path to them.

    RegionContent.from_dict is kind-agnostic, so this rehydration never
    changes as new content kinds (glossary terms, concepts) are added.
    """
    return RegionView(
        structural_label=region.structural_label,
        content=RegionContent.from_dict(region.content),
        selector_rationale=region.selector_rationale or {},
    )


def _orm_to_turn_role(role: DialogueRole) -> TurnRole:
    return TurnRole(role.value)
