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
