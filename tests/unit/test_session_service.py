"""End-to-end orchestration tests.

These exercise the SessionService against an in-memory SQLite + the test
fakes from tests/fakes.py. No LLM is invoked. The point is to validate
that the state machine, persistence, and component-call ordering match
what the PM doc requires.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from predictive_review.judge import JudgeOutcome
from predictive_review.selectors.registry import default_registry
from predictive_review.sessions.errors import (
    HypothesisAlreadyLocked,
    InvalidPhaseTransition,
    InvalidRegionStatus,
)
from predictive_review.sessions.service import SessionService
from predictive_review.storage.models import (
    Base,
    ClosureAttempt,
    ClosureMode,
    DialogueTurn,
    DispositionStatus,
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
from tests.fakes import (
    FakeClosureJudge,
    FakeDialogueManager,
    FakeReadingGenerator,
)


SAMPLE_DIFF = """\
diff --git a/foo.py b/foo.py
index 0000000..1111111 100644
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,5 @@
 def existing():
     pass
+
+def new():
+    return 42
@@ -10,2 +12,4 @@ def other():
     x = 1
+    # added comment
+    print("hello")
     return x
"""


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture
def reading_gen():
    return FakeReadingGenerator()


@pytest.fixture
def judge():
    return FakeClosureJudge()


@pytest.fixture
def dialogue():
    return FakeDialogueManager()


@pytest.fixture
def service(session_factory, reading_gen, judge, dialogue):
    return SessionService(
        session_factory=session_factory,
        selector_registry=default_registry,
        reading_generator=reading_gen,
        closure_judge=judge,
        dialogue_manager=dialogue,
    )


# --- helpers ---------------------------------------------------------------


def _submit(service: SessionService) -> str:
    return service.submit(
        diff_text=SAMPLE_DIFF,
        engineer_identifier="meadowlark",
        selector_name="first_n_hunks",
        layout=ReconciliationLayout.INLINE_HUNK,
    )


def _region_ids(session_factory, session_id: str) -> list[str]:
    with session_factory() as db:
        stmt = (
            select(Region.id)
            .where(Region.session_id == session_id)
            .order_by(Region.ordinal.asc())
        )
        return list(db.execute(stmt).scalars())


def _engage_all(
    service: SessionService, session_factory, session_id: str
) -> None:
    """Engage every region in the session. Most existing tests drive
    every region through reconciliation, so this is the v1.5-aware
    equivalent of the old "lock_and_reveal puts you straight in
    reconciliation" implicit behavior."""
    for rid in _region_ids(session_factory, session_id):
        service.engage_region(session_id=session_id, region_id=rid)


# --- submit ----------------------------------------------------------------


def test_submit_creates_session_with_regions_and_phase_events(
    service, session_factory
):
    session_id = _submit(service)

    with session_factory() as db:
        session = db.get(Session, session_id)
        assert session is not None
        assert session.current_phase is SessionPhase.HYPOTHESIS
        assert session.selector_name == "first_n_hunks"
        assert len(session.regions) == 2

        events = list(
            db.execute(
                select(PhaseEvent).where(PhaseEvent.session_id == session_id)
            ).scalars()
        )
        # entered_at now carries microseconds, but two events flushed within
        # one transaction can still tie below clock resolution. The unique
        # (session_id, phase) constraint plus state-machine ordering tells us
        # exactly what happened — we don't need timestamp ordering for that.
        assert {e.phase for e in events} == {
            SessionPhase.SUBMITTED,
            SessionPhase.HYPOTHESIS,
        }


# --- hypothesis ------------------------------------------------------------


def test_save_hypothesis_creates_unlocked_revision(service, session_factory):
    session_id = _submit(service)
    region_id = _region_ids(session_factory, session_id)[0]

    service.save_hypothesis(
        session_id=session_id, region_id=region_id, body="first draft"
    )
    service.save_hypothesis(
        session_id=session_id, region_id=region_id, body="second draft"
    )

    with session_factory() as db:
        revisions = list(
            db.execute(
                select(HypothesisRevision)
                .where(HypothesisRevision.region_id == region_id)
                .order_by(HypothesisRevision.revision_number.asc())
            ).scalars()
        )
        assert [r.body for r in revisions] == ["first draft", "second draft"]
        assert all(not r.is_locked for r in revisions)


def test_save_hypothesis_rejected_after_lock(
    service, session_factory, judge
):
    session_id = _submit(service)
    region_ids = _region_ids(session_factory, session_id)
    for rid in region_ids:
        service.save_hypothesis(
            session_id=session_id, region_id=rid, body="draft"
        )
    service.lock_and_reveal(session_id=session_id)

    with pytest.raises(InvalidPhaseTransition):
        service.save_hypothesis(
            session_id=session_id, region_id=region_ids[0], body="too late"
        )


# --- lock_and_reveal -------------------------------------------------------


def test_lock_and_reveal_locks_latest_and_generates_readings(
    service, session_factory, reading_gen
):
    session_id = _submit(service)
    region_ids = _region_ids(session_factory, session_id)
    for rid in region_ids:
        service.save_hypothesis(
            session_id=session_id, region_id=rid, body="v1"
        )
        service.save_hypothesis(
            session_id=session_id, region_id=rid, body="v2 final"
        )

    service.lock_and_reveal(session_id=session_id)

    with session_factory() as db:
        session = db.get(Session, session_id)
        assert session.current_phase is SessionPhase.RECONCILIATION

        for rid in region_ids:
            revisions = list(
                db.execute(
                    select(HypothesisRevision)
                    .where(HypothesisRevision.region_id == rid)
                    .order_by(HypothesisRevision.revision_number.asc())
                ).scalars()
            )
            assert [r.is_locked for r in revisions] == [False, True]

            reading = db.execute(
                select(Reading).where(Reading.region_id == rid)
            ).scalar_one()
            assert "FAKE READING" in reading.body

    # One reading-generator call per region; never called with hypothesis data
    # (signature does not accept it).
    assert len(reading_gen.calls) == len(region_ids)


def test_lock_and_reveal_requires_hypothesis_on_every_region(
    service, session_factory
):
    session_id = _submit(service)
    region_ids = _region_ids(session_factory, session_id)
    service.save_hypothesis(
        session_id=session_id, region_id=region_ids[0], body="only one region"
    )
    with pytest.raises(InvalidPhaseTransition):
        service.lock_and_reveal(session_id=session_id)


# --- reconciliation + closure ---------------------------------------------


def test_passing_reconciliation_moves_region_to_disposition(
    service, session_factory
):
    session_id = _submit(service)
    region_ids = _region_ids(session_factory, session_id)
    for rid in region_ids:
        service.save_hypothesis(
            session_id=session_id, region_id=rid, body="draft"
        )
    service.lock_and_reveal(session_id=session_id)
    _engage_all(service, session_factory, session_id)

    result = service.submit_reconciliation(
        session_id=session_id,
        region_id=region_ids[0],
        body="I understand the retry semantics now.",
    )
    assert result.verdict is JudgeOutcome.PASS
    assert result.attempt_number == 1

    with session_factory() as db:
        region = db.get(Region, region_ids[0])
        assert region.status is RegionStatus.AWAITING_DISPOSITION
        assert region.closure_mode is ClosureMode.JUDGE_PASSED
        attempts = list(
            db.execute(
                select(ClosureAttempt).where(
                    ClosureAttempt.region_id == region_ids[0]
                )
            ).scalars()
        )
        assert len(attempts) == 1


def test_failing_reconciliation_moves_region_to_dialogue(
    service, session_factory
):
    session_id = _submit(service)
    region_ids = _region_ids(session_factory, session_id)
    for rid in region_ids:
        service.save_hypothesis(
            session_id=session_id, region_id=rid, body="draft"
        )
    service.lock_and_reveal(session_id=session_id)
    _engage_all(service, session_factory, session_id)

    result = service.submit_reconciliation(
        session_id=session_id,
        region_id=region_ids[0],
        body="this is wrong actually",  # no 'understand' → FAIL
    )
    assert result.verdict is JudgeOutcome.FAIL
    assert result.missing_aspects is not None

    with session_factory() as db:
        region = db.get(Region, region_ids[0])
        assert region.status is RegionStatus.IN_DIALOGUE
        recon = db.execute(
            select(Reconciliation).where(
                Reconciliation.region_id == region_ids[0]
            )
        ).scalar_one()
        assert recon.body.startswith("this is wrong")


# --- dialogue + revised teach-back ----------------------------------------


def test_dialogue_then_revised_teach_back_closes_region(
    service, session_factory, dialogue
):
    session_id = _submit(service)
    region_ids = _region_ids(session_factory, session_id)
    for rid in region_ids:
        service.save_hypothesis(
            session_id=session_id, region_id=rid, body="draft"
        )
    service.lock_and_reveal(session_id=session_id)
    _engage_all(service, session_factory, session_id)

    region_id = region_ids[0]
    service.submit_reconciliation(
        session_id=session_id, region_id=region_id, body="wrong"
    )
    assert _region_status(session_factory, region_id) is RegionStatus.IN_DIALOGUE

    turn = service.dialogue_turn(
        session_id=session_id,
        region_id=region_id,
        engineer_message="why does it retry on 429 only?",
    )
    assert turn.model_body.startswith("FAKE REPLY")
    assert turn.turn_index == 2

    revised = service.submit_revised_teach_back(
        session_id=session_id,
        region_id=region_id,
        body="now I understand the retry policy",
    )
    assert revised.verdict is JudgeOutcome.PASS
    assert revised.attempt_number == 2

    with session_factory() as db:
        turns = list(
            db.execute(
                select(DialogueTurn)
                .where(DialogueTurn.region_id == region_id)
                .order_by(DialogueTurn.turn_index.asc())
            ).scalars()
        )
        assert [t.turn_index for t in turns] == [1, 2]
        attempts = list(
            db.execute(
                select(ClosureAttempt)
                .where(ClosureAttempt.region_id == region_id)
                .order_by(ClosureAttempt.attempt_number.asc())
            ).scalars()
        )
        assert [a.attempt_number for a in attempts] == [1, 2]
        assert _region_status(session_factory, region_id) is RegionStatus.AWAITING_DISPOSITION

    assert len(dialogue.calls) == 1


def test_close_with_disagreement_skips_to_disposition(service, session_factory):
    session_id = _submit(service)
    region_ids = _region_ids(session_factory, session_id)
    for rid in region_ids:
        service.save_hypothesis(
            session_id=session_id, region_id=rid, body="draft"
        )
    service.lock_and_reveal(session_id=session_id)
    _engage_all(service, session_factory, session_id)

    region_id = region_ids[0]
    service.submit_reconciliation(
        session_id=session_id, region_id=region_id, body="i disagree"
    )
    assert _region_status(session_factory, region_id) is RegionStatus.IN_DIALOGUE

    service.close_with_disagreement(
        session_id=session_id, region_id=region_id
    )
    assert _region_status(session_factory, region_id) is RegionStatus.AWAITING_DISPOSITION
    with session_factory() as db:
        assert (
            db.get(Region, region_id).closure_mode
            is ClosureMode.ENGINEER_DISAGREED
        )


# --- disposition + session completion -------------------------------------


def test_session_completes_when_all_regions_have_dispositions(
    service, session_factory
):
    session_id = _submit(service)
    region_ids = _region_ids(session_factory, session_id)
    for rid in region_ids:
        service.save_hypothesis(
            session_id=session_id, region_id=rid, body="draft"
        )
    service.lock_and_reveal(session_id=session_id)
    _engage_all(service, session_factory, session_id)
    for rid in region_ids:
        service.submit_reconciliation(
            session_id=session_id, region_id=rid, body="I understand"
        )

    for rid in region_ids[:-1]:
        service.set_disposition(
            session_id=session_id,
            region_id=rid,
            status=DispositionStatus.ACCEPTED_AS_IS,
        )
        with session_factory() as db:
            session = db.get(Session, session_id)
            assert session.current_phase is SessionPhase.RECONCILIATION

    service.set_disposition(
        session_id=session_id,
        region_id=region_ids[-1],
        status=DispositionStatus.FLAGGED_FOR_REDESIGN,
        justification="this branch is dead code from a stale refactor",
    )
    with session_factory() as db:
        session = db.get(Session, session_id)
        assert session.current_phase is SessionPhase.COMPLETE
        events = list(
            db.execute(
                select(PhaseEvent).where(PhaseEvent.session_id == session_id)
            ).scalars()
        )
        assert {e.phase for e in events} == {
            SessionPhase.SUBMITTED,
            SessionPhase.HYPOTHESIS,
            SessionPhase.RECONCILIATION,
            SessionPhase.COMPLETE,
        }


def test_flagged_for_redesign_requires_justification(service, session_factory):
    session_id = _submit(service)
    region_ids = _region_ids(session_factory, session_id)
    for rid in region_ids:
        service.save_hypothesis(
            session_id=session_id, region_id=rid, body="draft"
        )
    service.lock_and_reveal(session_id=session_id)
    _engage_all(service, session_factory, session_id)
    service.submit_reconciliation(
        session_id=session_id, region_id=region_ids[0], body="I understand"
    )

    with pytest.raises(ValueError):
        service.set_disposition(
            session_id=session_id,
            region_id=region_ids[0],
            status=DispositionStatus.FLAGGED_FOR_REDESIGN,
            justification=None,
        )


# --- phase guards ---------------------------------------------------------


def test_submit_reconciliation_rejected_before_reveal(
    service, session_factory
):
    session_id = _submit(service)
    region_id = _region_ids(session_factory, session_id)[0]
    service.save_hypothesis(
        session_id=session_id, region_id=region_id, body="draft"
    )
    with pytest.raises(InvalidPhaseTransition):
        service.submit_reconciliation(
            session_id=session_id, region_id=region_id, body="I understand"
        )


def test_dialogue_turn_rejected_when_region_not_in_dialogue(
    service, session_factory
):
    session_id = _submit(service)
    region_ids = _region_ids(session_factory, session_id)
    for rid in region_ids:
        service.save_hypothesis(
            session_id=session_id, region_id=rid, body="draft"
        )
    service.lock_and_reveal(session_id=session_id)
    with pytest.raises(InvalidRegionStatus):
        service.dialogue_turn(
            session_id=session_id,
            region_id=region_ids[0],
            engineer_message="hi",
        )


# --- v1.5: three-way choice + override + deferral -------------------------


def _drive_to_reveal_choice(service, session_factory) -> tuple[str, list[str]]:
    """Submit, hypothesize on every region, lock and reveal. The two-region
    sample diff leaves both regions at AWAITING_REVEAL_CHOICE — ready for
    the three-way choice."""
    session_id = _submit(service)
    region_ids = _region_ids(session_factory, session_id)
    for rid in region_ids:
        service.save_hypothesis(
            session_id=session_id, region_id=rid, body="draft"
        )
    service.lock_and_reveal(session_id=session_id)
    return session_id, region_ids


def test_lock_and_reveal_lands_regions_at_reveal_choice(
    service, session_factory
):
    session_id, region_ids = _drive_to_reveal_choice(service, session_factory)
    with session_factory() as db:
        for rid in region_ids:
            region = db.get(Region, rid)
            assert region.status is RegionStatus.AWAITING_REVEAL_CHOICE
            assert region.closure_mode is None
            assert region.is_deferred is False


def test_engage_region_moves_to_reconciliation(service, session_factory):
    session_id, region_ids = _drive_to_reveal_choice(service, session_factory)
    service.engage_region(session_id=session_id, region_id=region_ids[0])
    with session_factory() as db:
        assert (
            db.get(Region, region_ids[0]).status
            is RegionStatus.AWAITING_RECONCILIATION
        )


def test_acknowledge_region_closes_with_note(service, session_factory):
    session_id, region_ids = _drive_to_reveal_choice(service, session_factory)
    service.acknowledge_region(
        session_id=session_id,
        region_id=region_ids[0],
        note="routine null-check, no deeper engagement needed",
    )
    with session_factory() as db:
        region = db.get(Region, region_ids[0])
        assert region.status is RegionStatus.AWAITING_DISPOSITION
        assert region.closure_mode is ClosureMode.ACKNOWLEDGED
        assert "null-check" in region.acknowledgment_note


def test_acknowledge_region_requires_non_empty_note(service, session_factory):
    session_id, region_ids = _drive_to_reveal_choice(service, session_factory)
    with pytest.raises(ValueError, match="acknowledgment_note"):
        service.acknowledge_region(
            session_id=session_id, region_id=region_ids[0], note="   "
        )


def test_defer_region_sets_flag_without_closing(service, session_factory):
    session_id, region_ids = _drive_to_reveal_choice(service, session_factory)
    service.defer_region(session_id=session_id, region_id=region_ids[0])
    with session_factory() as db:
        region = db.get(Region, region_ids[0])
        assert region.status is RegionStatus.AWAITING_REVEAL_CHOICE
        assert region.is_deferred is True
        assert region.closure_mode is None


def test_revisit_deferred_region_clears_flag(service, session_factory):
    session_id, region_ids = _drive_to_reveal_choice(service, session_factory)
    service.defer_region(session_id=session_id, region_id=region_ids[0])
    service.revisit_deferred_region(
        session_id=session_id, region_id=region_ids[0]
    )
    with session_factory() as db:
        region = db.get(Region, region_ids[0])
        assert region.is_deferred is False
        # Still at the three-way choice — ready for the re-choice
        assert region.status is RegionStatus.AWAITING_REVEAL_CHOICE


def test_revisit_not_deferred_raises(service, session_factory):
    session_id, region_ids = _drive_to_reveal_choice(service, session_factory)
    with pytest.raises(InvalidRegionStatus, match="not deferred"):
        service.revisit_deferred_region(
            session_id=session_id, region_id=region_ids[0]
        )


def test_engage_after_defer_clears_the_deferral(service, session_factory):
    session_id, region_ids = _drive_to_reveal_choice(service, session_factory)
    service.defer_region(session_id=session_id, region_id=region_ids[0])
    service.engage_region(session_id=session_id, region_id=region_ids[0])
    with session_factory() as db:
        region = db.get(Region, region_ids[0])
        assert region.is_deferred is False
        assert region.status is RegionStatus.AWAITING_RECONCILIATION


def test_submit_override_records_reason_and_closes(service, session_factory):
    session_id, region_ids = _drive_to_reveal_choice(service, session_factory)
    region_id = region_ids[0]
    service.engage_region(session_id=session_id, region_id=region_id)
    # Force a FAIL to land in IN_DIALOGUE
    service.submit_reconciliation(
        session_id=session_id, region_id=region_id, body="wrong"
    )
    assert _region_status(session_factory, region_id) is RegionStatus.IN_DIALOGUE

    service.submit_override(
        session_id=session_id,
        region_id=region_id,
        reason=OverrideReason.TOIL,
    )
    with session_factory() as db:
        region = db.get(Region, region_id)
        assert region.status is RegionStatus.AWAITING_DISPOSITION
        assert region.closure_mode is ClosureMode.ENGINEER_OVERRODE
        assert region.override_reason is OverrideReason.TOIL


def test_close_with_disagreement_stores_optional_reason(
    service, session_factory
):
    session_id, region_ids = _drive_to_reveal_choice(service, session_factory)
    region_id = region_ids[0]
    service.engage_region(session_id=session_id, region_id=region_id)
    service.submit_reconciliation(
        session_id=session_id, region_id=region_id, body="wrong"
    )
    service.close_with_disagreement(
        session_id=session_id,
        region_id=region_id,
        reason="the model is confused about which retry path is being patched",
    )
    with session_factory() as db:
        region = db.get(Region, region_id)
        assert region.closure_mode is ClosureMode.ENGINEER_DISAGREED
        assert "retry path" in region.disagreement_reason


def test_deferred_region_blocks_session_completion(service, session_factory):
    """One region engaged through to disposition, the other deferred. Session
    must not complete until the deferred region is also resolved."""
    session_id, region_ids = _drive_to_reveal_choice(service, session_factory)
    service.engage_region(session_id=session_id, region_id=region_ids[0])
    service.defer_region(session_id=session_id, region_id=region_ids[1])

    service.submit_reconciliation(
        session_id=session_id,
        region_id=region_ids[0],
        body="I understand",
    )
    service.set_disposition(
        session_id=session_id,
        region_id=region_ids[0],
        status=DispositionStatus.ACCEPTED_AS_IS,
    )

    with session_factory() as db:
        # Session still in RECONCILIATION because region_ids[1] is deferred.
        assert (
            db.get(Session, session_id).current_phase
            is SessionPhase.RECONCILIATION
        )

    # Revisit and acknowledge the deferred region; THEN the session completes.
    service.revisit_deferred_region(
        session_id=session_id, region_id=region_ids[1]
    )
    service.acknowledge_region(
        session_id=session_id,
        region_id=region_ids[1],
        note="trivial rename, nothing to learn",
    )
    service.set_disposition(
        session_id=session_id,
        region_id=region_ids[1],
        status=DispositionStatus.ACCEPTED_AS_IS,
    )

    with session_factory() as db:
        assert (
            db.get(Session, session_id).current_phase is SessionPhase.COMPLETE
        )


# --- internal helper -------------------------------------------------------


def _region_status(session_factory, region_id: str) -> RegionStatus:
    with session_factory() as db:
        return db.get(Region, region_id).status
