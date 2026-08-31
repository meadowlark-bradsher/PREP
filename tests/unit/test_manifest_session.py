"""Manifest sessions end-to-end through SessionService, with fakes.

Covers what the selector tests cannot: criterion resolution against the
source, the recorded column, and rejection at launch — before any session
row exists.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from predictive_review.content_sources import DiffSource, ManifestSource
from predictive_review.selectors.manifest import UnknownCriterion
from predictive_review.selectors.registry import default_registry
from predictive_review.sessions.service import SessionService
from predictive_review.storage.models import (
    Base,
    EngagementThreshold,
    ReconciliationLayout,
    Session,
)
from tests.fakes import FakeClosureJudge, FakeDialogueManager, FakeReadingGenerator

FIXTURE = Path(__file__).parent.parent / "fixtures" / "load_bearing_repo"

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
def service(session_factory):
    return SessionService(
        session_factory=session_factory,
        selector_registry=default_registry,
        reading_generator=FakeReadingGenerator(),
        closure_judge=FakeClosureJudge(),
        dialogue_manager=FakeDialogueManager(),
    )


def _submit_manifest(service, *, criterion=None, root=FIXTURE):
    return service.submit(
        source=ManifestSource(root),
        engineer_identifier="meadowlark",
        selector_name="manifest",
        layout=ReconciliationLayout.NO_HUNK,
        criterion=criterion,
        engagement_threshold=EngagementThreshold.THOROUGH,
    )


# --- criterion resolution and recording -------------------------------------


def test_defaults_to_the_manifests_default_criterion(service) -> None:
    session_id = _submit_manifest(service)
    assert service.get_session(session_id).criterion == "identity"


def test_explicit_criterion_is_recorded(service) -> None:
    session_id = _submit_manifest(service, criterion="churn-90d")
    assert service.get_session(session_id).criterion == "churn-90d"


def test_criterion_changes_the_region_order(service) -> None:
    by_identity = service.list_regions(_submit_manifest(service, criterion="identity"))
    by_churn = service.list_regions(_submit_manifest(service, criterion="churn-90d"))

    assert [r.structural_label for r in by_identity] == [
        "auth/token-refresh",
        "cache/lru-read",
    ]
    assert [r.structural_label for r in by_churn] == [
        "cache/lru-read",
        "auth/token-refresh",
    ]


def test_diff_sessions_record_no_criterion(service) -> None:
    session_id = service.submit(
        source=DiffSource(SAMPLE_DIFF),
        engineer_identifier="meadowlark",
        selector_name="first_n_hunks",
        layout=ReconciliationLayout.INLINE_HUNK,
    )
    assert service.get_session(session_id).criterion is None


def test_criterion_is_persisted_on_the_session_row(service, session_factory) -> None:
    session_id = _submit_manifest(service, criterion="correctness")
    with session_factory() as db:
        stored = db.execute(
            select(Session.criterion).where(Session.id == session_id)
        ).scalar_one()
    assert stored == "correctness"


# --- rejection at launch -----------------------------------------------------


def test_unknown_criterion_rejected_before_a_session_exists(service, session_factory) -> None:
    with pytest.raises(UnknownCriterion) as e:
        _submit_manifest(service, criterion="maintainability")

    for declared in ("correctness", "churn-90d", "identity"):
        assert declared in str(e.value)

    with session_factory() as db:
        assert db.execute(select(Session.id)).all() == [], "no session row may survive"


def test_criterion_on_a_diff_source_is_rejected(service) -> None:
    """A diff declares no criteria; asking to order by one is a mistake
    worth surfacing rather than ignoring."""
    with pytest.raises(ValueError, match="declares no criteria"):
        service.submit(
            source=DiffSource(SAMPLE_DIFF),
            engineer_identifier="meadowlark",
            selector_name="first_n_hunks",
            layout=ReconciliationLayout.INLINE_HUNK,
            criterion="correctness",
        )


# --- staleness ---------------------------------------------------------------


def test_stale_members_are_excluded_from_the_session(service) -> None:
    session_id = _submit_manifest(service)
    labels = {r.structural_label for r in service.list_regions(session_id)}
    assert "cache/eviction" not in labels


def test_selector_name_semantics_unchanged_for_diff_sessions(service) -> None:
    session_id = service.submit(
        source=DiffSource(SAMPLE_DIFF),
        engineer_identifier="meadowlark",
        selector_name="first_n_hunks",
        layout=ReconciliationLayout.INLINE_HUNK,
    )
    snapshot = service.get_session(session_id)
    assert snapshot.selector_name == "first_n_hunks"
    assert snapshot.selector_version == "v1"


def test_manifest_session_records_the_manifest_selector(service) -> None:
    snapshot = service.get_session(_submit_manifest(service))
    assert snapshot.selector_name == "manifest"
    assert snapshot.selector_version == "v1"


def test_manifest_json_is_kept_as_the_session_source_text(service, session_factory) -> None:
    """Provenance: whatever the session was launched from is retained."""
    session_id = _submit_manifest(service)
    with session_factory() as db:
        stored = db.execute(
            select(Session.diff_text).where(Session.id == session_id)
        ).scalar_one()
    assert json.loads(stored)["contract"] == "load-bearing/0.1"


# --- invariant 7: judge scope follows the criterion -------------------------


def _reveal_and_reconcile(service, session_id, teach_back="it retries a bit"):
    """Drive one region to a judged closure attempt."""
    for r in service.list_regions(session_id):
        service.save_hypothesis(session_id=session_id, region_id=r.id, body="a guess")
    service.lock_and_reveal(session_id=session_id)
    target = service.list_regions(session_id)[0]
    service.engage_region(session_id=session_id, region_id=target.id)
    return target, service.submit_reconciliation(
        session_id=session_id, region_id=target.id, body=teach_back
    )


def _judge_for(service):
    return service._judge  # the FakeClosureJudge the fixture installed


def test_aspects_are_filtered_by_criterion_before_reaching_the_judge(
    session_factory,
) -> None:
    judge = FakeClosureJudge()
    service = SessionService(
        session_factory=session_factory,
        selector_registry=default_registry,
        reading_generator=FakeReadingGenerator(),
        closure_judge=judge,
        dialogue_manager=FakeDialogueManager(),
    )
    session_id = _submit_manifest(service, criterion="correctness")
    _reveal_and_reconcile(service, session_id)

    seen = judge.aspects_seen[-1]
    # auth/token-refresh declares retry-bound (correctness), churn-hotspot
    # (churn-90d) and failure-modes-distinct (universal).
    assert seen is not None
    assert "retry-bound" in seen
    assert "failure-modes-distinct" in seen, "universal aspects always apply"
    assert "churn-hotspot" not in seen, "other criteria must not leak in"


def test_a_different_criterion_yields_a_different_aspect_scope(
    session_factory,
) -> None:
    judge = FakeClosureJudge()
    service = SessionService(
        session_factory=session_factory,
        selector_registry=default_registry,
        reading_generator=FakeReadingGenerator(),
        closure_judge=judge,
        dialogue_manager=FakeDialogueManager(),
    )
    session_id = _submit_manifest(service, criterion="churn-90d")
    _reveal_and_reconcile(service, session_id)

    seen = judge.aspects_seen[-1]
    assert seen is not None
    # churn-90d orders cache/lru-read first, whose aspects are
    # read-promotes (correctness) and none-ambiguity (universal).
    assert "none-ambiguity" in seen
    assert "read-promotes" not in seen


def test_diff_regions_pass_no_aspects(session_factory) -> None:
    judge = FakeClosureJudge()
    service = SessionService(
        session_factory=session_factory,
        selector_registry=default_registry,
        reading_generator=FakeReadingGenerator(),
        closure_judge=judge,
        dialogue_manager=FakeDialogueManager(),
    )
    session_id = service.submit(
        source=DiffSource(SAMPLE_DIFF),
        engineer_identifier="meadowlark",
        selector_name="first_n_hunks",
        layout=ReconciliationLayout.INLINE_HUNK,
    )
    _reveal_and_reconcile(service, session_id)

    assert judge.aspects_seen[-1] is None, "prose mode is untouched for code hunks"


# --- provenance: criterion and aspect_scope on the attempt ------------------


def test_closure_attempt_records_criterion_and_aspect_scope(
    service, session_factory
) -> None:
    from predictive_review.storage.models import ClosureAttempt

    session_id = _submit_manifest(service, criterion="correctness")
    _reveal_and_reconcile(service, session_id)

    with session_factory() as db:
        attempt = db.execute(select(ClosureAttempt)).scalars().first()

    assert attempt.criterion == "correctness"
    assert attempt.aspect_scope is not None
    assert "retry-bound" in attempt.aspect_scope
    assert "churn-hotspot" not in attempt.aspect_scope


def test_diff_attempt_records_no_criterion_and_no_scope(
    service, session_factory
) -> None:
    from predictive_review.storage.models import ClosureAttempt

    session_id = service.submit(
        source=DiffSource(SAMPLE_DIFF),
        engineer_identifier="meadowlark",
        selector_name="first_n_hunks",
        layout=ReconciliationLayout.INLINE_HUNK,
    )
    _reveal_and_reconcile(service, session_id)

    with session_factory() as db:
        attempt = db.execute(select(ClosureAttempt)).scalars().first()

    assert attempt.criterion is None
    assert attempt.aspect_scope is None, "NULL scope is the prose-mode discriminator"


def test_structured_missing_aspects_survives_the_round_trip(
    service, session_factory
) -> None:
    from predictive_review.storage.models import ClosureAttempt

    session_id = _submit_manifest(service, criterion="correctness")
    _, result = _reveal_and_reconcile(service, session_id)

    assert isinstance(result.missing_aspects, list)
    with session_factory() as db:
        attempt = db.execute(select(ClosureAttempt)).scalars().first()
    assert isinstance(attempt.missing_aspects, list)
    assert set(attempt.missing_aspects) <= set(attempt.aspect_scope)


def test_hypothesis_is_still_stripped_in_member_mode(session_factory) -> None:
    """The type-gate holds regardless of content kind."""
    from predictive_review.sessions.service import _to_region_view
    from predictive_review.storage.models import Region as OrmRegion

    judge = FakeClosureJudge()
    service = SessionService(
        session_factory=session_factory,
        selector_registry=default_registry,
        reading_generator=FakeReadingGenerator(),
        closure_judge=judge,
        dialogue_manager=FakeDialogueManager(),
    )
    session_id = _submit_manifest(service)
    for r in service.list_regions(session_id):
        service.save_hypothesis(
            session_id=session_id, region_id=r.id, body="SECRET PRIOR"
        )

    with session_factory() as db:
        orm_region = db.execute(select(OrmRegion)).scalars().first()
        view = _to_region_view(orm_region)

    assert not hasattr(view, "hypothesis")
    assert "SECRET PRIOR" not in repr(view)
