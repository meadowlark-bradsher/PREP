"""Web route tests.

Wire a SessionService backed by an in-memory SQLite + the fakes from
tests/fakes.py, override the get_service dependency on the FastAPI app,
and walk the routes via TestClient. No real LLM or git is invoked.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from predictive_review.selectors.development import FirstNHunksSelector
from predictive_review.selectors.registry import SelectorRegistry
from predictive_review.sessions.service import SessionService
from predictive_review.storage.models import Base
from predictive_review.web.app import create_app, get_service
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
def service():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    # Routes hardcode selector_name="llm_judgment" for production wiring;
    # tests map that name to FirstNHunksSelector so no LLM is invoked.
    registry = SelectorRegistry()
    registry.register("llm_judgment", FirstNHunksSelector)
    return SessionService(
        session_factory=factory,
        selector_registry=registry,
        reading_generator=FakeReadingGenerator(),
        closure_judge=FakeClosureJudge(),
        dialogue_manager=FakeDialogueManager(),
    )


@pytest.fixture
def client(service):
    app = create_app()
    app.dependency_overrides[get_service] = lambda: service
    return TestClient(app)


# --- launcher --------------------------------------------------------------


def test_root_redirects_to_launcher(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/launch"


def test_launcher_renders(client):
    r = client.get("/launch")
    assert r.status_code == 200
    assert "Start a session" in r.text
    assert 'name="diff_text"' in r.text
    assert 'name="commit"' in r.text
    assert 'name="git_range"' in r.text


def test_launch_rejects_no_input(client):
    r = client.post(
        "/launch",
        data={"engineer": "x", "diff_text": "", "commit": "", "git_range": ""},
    )
    assert r.status_code == 400
    assert "exactly one" in r.text


def test_launch_rejects_multiple_inputs(client):
    r = client.post(
        "/launch",
        data={
            "engineer": "x",
            "diff_text": SAMPLE_DIFF,
            "commit": "HEAD",
            "git_range": "",
        },
    )
    assert r.status_code == 400


def test_launch_with_paste_creates_session_and_redirects(client):
    r = client.post(
        "/launch",
        data={
            "engineer": "meadowlark",
            "diff_text": SAMPLE_DIFF,
            "commit": "",
            "git_range": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/sessions/")


# --- resume + hypothesis ---------------------------------------------------


def _start(client, diff=SAMPLE_DIFF) -> str:
    r = client.post(
        "/launch",
        data={
            "engineer": "x",
            "diff_text": diff,
            "commit": "",
            "git_range": "",
        },
        follow_redirects=False,
    )
    return r.headers["location"].rsplit("/", 1)[-1]


def test_resume_lands_on_hypothesis_for_fresh_session(client):
    session_id = _start(client)
    r = client.get(f"/sessions/{session_id}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/sessions/{session_id}/hypothesis"


def test_hypothesis_page_renders_tabs_per_region(client, service):
    session_id = _start(client)
    r = client.get(f"/sessions/{session_id}/hypothesis")
    assert r.status_code == 200
    regions = service.list_regions(session_id)
    for region in regions:
        assert region.structural_label in r.text
        assert f'name="hypothesis_{region.id}"' in r.text


def test_hypothesis_save_rejects_empty(client, service):
    session_id = _start(client)
    regions = service.list_regions(session_id)
    data = {f"hypothesis_{r.id}": "" for r in regions}
    r = client.post(f"/sessions/{session_id}/hypothesis", data=data)
    assert r.status_code == 400


def test_hypothesis_save_with_reveal_action_triggers_reveal(client, service):
    session_id = _start(client)
    regions = service.list_regions(session_id)
    data = {f"hypothesis_{r.id}": "I think this is X" for r in regions}
    data["action"] = "reveal"
    r = client.post(
        f"/sessions/{session_id}/hypothesis", data=data, follow_redirects=False
    )
    # Save → redirect to /reveal which then redirects to a region surface.
    assert r.status_code == 303
    assert r.headers["location"] == f"/sessions/{session_id}/reveal"


# --- reveal + region surface ----------------------------------------------


def _through_reveal(client, service) -> tuple[str, list]:
    session_id = _start(client)
    regions = service.list_regions(session_id)
    data = {f"hypothesis_{r.id}": "draft" for r in regions}
    data["action"] = "reveal"
    client.post(f"/sessions/{session_id}/hypothesis", data=data)
    client.post(f"/sessions/{session_id}/reveal")
    return session_id, service.list_regions(session_id)


def test_reveal_auto_engages_all_regions(client, service):
    session_id, regions = _through_reveal(client, service)
    for r in regions:
        # In commit 3, post-reveal regions are auto-engaged to AWAITING_RECONCILIATION.
        # Commit 4 will replace this with the three-way choice.
        assert r.status.value == "awaiting_reconciliation"


def test_region_surface_shows_hypothesis_reading_and_reconciliation_form(
    client, service
):
    session_id, regions = _through_reveal(client, service)
    r = client.get(f"/sessions/{session_id}/regions/{regions[0].id}")
    assert r.status_code == 200
    assert "Your locked hypothesis" in r.text
    assert "draft" in r.text  # the hypothesis text
    assert "FAKE READING" in r.text
    assert 'name="body"' in r.text  # the reconciliation textarea


# --- happy path: reconcile → dispose → complete ----------------------------


def test_full_happy_path_through_to_summary(client, service):
    session_id, regions = _through_reveal(client, service)

    # Reconcile each region with text containing "understand" to trip the
    # FakeClosureJudge into PASS.
    for region in regions:
        client.post(
            f"/sessions/{session_id}/regions/{region.id}/reconcile",
            data={"body": "I understand the change"},
        )

    # Dispose each (accept-as-is).
    for i, region in enumerate(regions):
        r = client.post(
            f"/sessions/{session_id}/regions/{region.id}/dispose",
            data={"action": "accept", "justification": ""},
            follow_redirects=False,
        )
        # All but the last leave the session in RECONCILIATION; the last
        # transitions to COMPLETE, so resume → /summary.
        assert r.status_code == 303

    # After disposing all, resume should point at summary.
    r = client.get(f"/sessions/{session_id}", follow_redirects=False)
    assert r.headers["location"] == f"/sessions/{session_id}/summary"

    # Summary renders.
    r = client.get(f"/sessions/{session_id}/summary")
    assert r.status_code == 200
    for region in regions:
        assert region.structural_label in r.text


# --- failing reconciliation routes to dialogue -----------------------------


def test_failing_reconciliation_lands_on_dialogue_form(client, service):
    session_id, regions = _through_reveal(client, service)
    region_id = regions[0].id
    client.post(
        f"/sessions/{session_id}/regions/{region_id}/reconcile",
        data={"body": "no clue"},  # no 'understand' → FAIL
    )
    r = client.get(f"/sessions/{session_id}/regions/{region_id}")
    assert r.status_code == 200
    assert "Dialogue" in r.text
    assert "Judge denied closure" in r.text


# --- 404s ------------------------------------------------------------------


def test_unknown_session_resume_returns_404(client):
    r = client.get("/sessions/does-not-exist", follow_redirects=False)
    assert r.status_code == 404


def test_unknown_region_returns_404(client):
    session_id = _start(client)
    r = client.get(f"/sessions/{session_id}/regions/does-not-exist")
    assert r.status_code == 404
