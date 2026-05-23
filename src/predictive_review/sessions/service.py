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

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession, selectinload

from ..dialogue import DialogueManager, DialogueMessage, TurnRole
from ..domain.diff import Hunk, parse_diff
from ..domain.region import Region as RegionView
from ..judge import ClosureJudge, JudgeOutcome, JudgeVerdict
from ..reading import ReadingGenerator
from ..selectors.base import SelectorContext
from ..selectors.registry import SelectorRegistry
from ..storage.models import (
    ClosureAttempt,
    ClosureVerdict,
    DialogueRole,
    DialogueTurn,
    Disposition,
    DispositionStatus,
    Engineer,
    HypothesisRevision,
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
        diff_text: str,
        engineer_identifier: str,
        selector_name: str,
        layout: ReconciliationLayout,
    ) -> str:
        """Parse the diff, run the selector, persist session + regions.

        Returns the new session id; the session is left in HYPOTHESIS phase.
        """
        diff = parse_diff(diff_text)
        selector = self._selectors.get(selector_name)
        candidate_regions = selector.select(
            diff, context=SelectorContext(engineer_identifier=engineer_identifier)
        )
        if not candidate_regions:
            raise ValueError("selector returned no regions")

        with self._session_factory() as db:
            engineer = self._get_or_create_engineer(db, engineer_identifier)
            session = Session(
                engineer_id=engineer.id,
                diff_text=diff_text,
                selector_name=selector.name,
                selector_version=selector.version,
                reconciliation_layout=layout,
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
                        hunk_ref=region_view.hunk.text,
                        selector_rationale=dict(region_view.selector_rationale),
                        status=RegionStatus.AWAITING_RECONCILIATION,
                    )
                )

            db.commit()
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

            session.current_phase = SessionPhase.RECONCILIATION
            db.add(
                PhaseEvent(
                    session_id=session.id, phase=SessionPhase.RECONCILIATION
                )
            )
            db.commit()

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
            self._get_session(db, session_id)
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
            )

    def close_with_disagreement(
        self,
        *,
        session_id: str,
        region_id: str,
    ) -> None:
        with self._session_factory() as db:
            self._get_session(db, session_id)
            region = self._get_region(db, region_id, session_id)
            self._require_region_status(region, RegionStatus.IN_DIALOGUE)

            region.status = RegionStatus.AWAITING_DISPOSITION
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

            if all(r.status is RegionStatus.CLOSED for r in session.regions):
                session.current_phase = SessionPhase.COMPLETE
                db.add(
                    PhaseEvent(
                        session_id=session.id, phase=SessionPhase.COMPLETE
                    )
                )

            db.commit()

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
    ) -> ClosureAttemptResult:
        verdict: JudgeVerdict = self._judge.judge(
            reading_body=reading_body,
            teach_back_statement=teach_back_statement,
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
    fresh RegionView with only the persisted code metadata guarantees
    the LLM call has no traversal path to them.

    v1 wart: Region.hunk_ref stores only the hunk text, so we lose
    file_path and line numbers on the round-trip. The production
    reading generator will need this metadata; a later commit will
    persist the full Hunk as JSON rather than text.
    """
    return RegionView(
        structural_label=region.structural_label,
        hunk=Hunk(
            file_path="",
            old_start=0,
            old_count=0,
            new_start=0,
            new_count=0,
            text=region.hunk_ref,
        ),
        selector_rationale=region.selector_rationale or {},
    )


def _orm_to_turn_role(role: DialogueRole) -> TurnRole:
    return TurnRole(role.value)
