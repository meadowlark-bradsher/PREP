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
    DialogueTurn,
    DispositionStatus,
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
        # SQLite CURRENT_TIMESTAMP is second-precision, so events written in
        # the same transaction tie on entered_at. Check membership; ordering
        # will become assertable once entered_at carries microseconds.
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

    region_id = region_ids[0]
    service.submit_reconciliation(
        session_id=session_id, region_id=region_id, body="i disagree"
    )
    assert _region_status(session_factory, region_id) is RegionStatus.IN_DIALOGUE

    service.close_with_disagreement(
        session_id=session_id, region_id=region_id
    )
    assert _region_status(session_factory, region_id) is RegionStatus.AWAITING_DISPOSITION


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


# --- internal helper -------------------------------------------------------


def _region_status(session_factory, region_id: str) -> RegionStatus:
    with session_factory() as db:
        return db.get(Region, region_id).status
