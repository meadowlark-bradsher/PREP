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
    """Submit → hypothesize → reveal. Regions are at AWAITING_REVEAL_CHOICE."""
    session_id = _start(client)
    regions = service.list_regions(session_id)
    data = {f"hypothesis_{r.id}": "draft" for r in regions}
    data["action"] = "reveal"
    client.post(f"/sessions/{session_id}/hypothesis", data=data)
    client.post(f"/sessions/{session_id}/reveal")
    return session_id, service.list_regions(session_id)


def _engage_all_via_routes(client, session_id, regions):
    """Engage every region through the route. Used by tests that want to
    drive past the three-way choice into the reconciliation flow."""
    for r in regions:
        client.post(f"/sessions/{session_id}/regions/{r.id}/engage")


def test_reveal_lands_regions_at_reveal_choice(client, service):
    session_id, regions = _through_reveal(client, service)
    for r in regions:
        assert r.status.value == "awaiting_reveal_choice"
        assert r.is_deferred is False


def test_reveal_redirects_to_first_region_for_choice(client, service):
    session_id = _start(client)
    regions = service.list_regions(session_id)
    data = {f"hypothesis_{r.id}": "draft" for r in regions}
    data["action"] = "reveal"
    client.post(f"/sessions/{session_id}/hypothesis", data=data)
    r = client.post(f"/sessions/{session_id}/reveal", follow_redirects=False)
    assert r.status_code == 303
    assert f"/sessions/{session_id}/regions/{regions[0].id}" in r.headers["location"]


def test_region_surface_at_reveal_choice_shows_three_buttons(client, service):
    session_id, regions = _through_reveal(client, service)
    r = client.get(f"/sessions/{session_id}/regions/{regions[0].id}")
    assert r.status_code == 200
    assert "Choose your engagement" in r.text
    assert "Engage" in r.text and "Acknowledge" in r.text and "Defer" in r.text
    assert 'name="note"' in r.text  # the acknowledgment input


def test_engage_route_moves_region_to_reconciliation(client, service):
    session_id, regions = _through_reveal(client, service)
    region_id = regions[0].id
    r = client.post(
        f"/sessions/{session_id}/regions/{region_id}/engage",
        follow_redirects=False,
    )
    assert r.status_code == 303
    region = service.get_region(session_id, region_id)
    assert region.status.value == "awaiting_reconciliation"


def test_acknowledge_route_closes_with_note(client, service):
    session_id, regions = _through_reveal(client, service)
    region_id = regions[0].id
    r = client.post(
        f"/sessions/{session_id}/regions/{region_id}/acknowledge",
        data={"note": "routine null-check, no deeper engagement needed"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    region = service.get_region(session_id, region_id)
    assert region.status.value == "awaiting_disposition"
    assert region.closure_mode.value == "acknowledged"
    assert "null-check" in region.acknowledgment_note


def test_acknowledge_route_rejects_blank_note(client, service):
    session_id, regions = _through_reveal(client, service)
    r = client.post(
        f"/sessions/{session_id}/regions/{regions[0].id}/acknowledge",
        data={"note": "   "},
    )
    assert r.status_code == 400


def test_defer_skips_to_next_active_region(client, service):
    session_id, regions = _through_reveal(client, service)
    first_id, second_id = regions[0].id, regions[1].id
    r = client.post(
        f"/sessions/{session_id}/regions/{first_id}/defer",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/sessions/{session_id}/regions/{second_id}"
    assert service.get_region(session_id, first_id).is_deferred is True


def test_deferred_region_surface_notes_prior_deferral(client, service):
    session_id, regions = _through_reveal(client, service)
    region_id = regions[0].id
    client.post(f"/sessions/{session_id}/regions/{region_id}/defer")
    r = client.get(f"/sessions/{session_id}/regions/{region_id}")
    assert "previously deferred" in r.text.lower()


def test_engaging_a_deferred_region_clears_the_flag(client, service):
    session_id, regions = _through_reveal(client, service)
    region_id = regions[0].id
    client.post(f"/sessions/{session_id}/regions/{region_id}/defer")
    client.post(f"/sessions/{session_id}/regions/{region_id}/engage")
    region = service.get_region(session_id, region_id)
    assert region.is_deferred is False
    assert region.status.value == "awaiting_reconciliation"


def test_resume_prefers_active_over_deferred(client, service):
    """Defer region 1, then resume should route to region 2 (active),
    not back to region 1 (deferred)."""
    session_id, regions = _through_reveal(client, service)
    client.post(f"/sessions/{session_id}/regions/{regions[0].id}/defer")
    r = client.get(f"/sessions/{session_id}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/sessions/{session_id}/regions/{regions[1].id}"


def test_resume_routes_to_deferred_when_no_active_remain(client, service):
    """Acknowledge region 2 (close it), defer region 1. Now only the deferred
    region is unresolved — resume should route there even though it's deferred
    because nothing else is left."""
    session_id, regions = _through_reveal(client, service)
    # Acknowledge region 2 → AWAITING_DISPOSITION, then dispose to CLOSED.
    client.post(
        f"/sessions/{session_id}/regions/{regions[1].id}/acknowledge",
        data={"note": "trivial"},
    )
    client.post(
        f"/sessions/{session_id}/regions/{regions[1].id}/dispose",
        data={"action": "accept", "justification": ""},
    )
    # Defer region 1.
    client.post(f"/sessions/{session_id}/regions/{regions[0].id}/defer")
    # Resume should now land on the deferred region.
    r = client.get(f"/sessions/{session_id}", follow_redirects=False)
    assert r.headers["location"] == f"/sessions/{session_id}/regions/{regions[0].id}"


# --- happy path: reconcile → dispose → complete ----------------------------


def test_full_happy_path_through_to_summary(client, service):
    session_id, regions = _through_reveal(client, service)
    _engage_all_via_routes(client, session_id, regions)

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
    _engage_all_via_routes(client, session_id, regions)
    region_id = regions[0].id
    client.post(
        f"/sessions/{session_id}/regions/{region_id}/reconcile",
        data={"body": "no clue"},  # no 'understand' → FAIL
    )
    r = client.get(f"/sessions/{session_id}/regions/{region_id}")
    assert r.status_code == 200
    assert "Dialogue" in r.text
    assert "Judge denied closure" in r.text


# --- chat surface ---------------------------------------------------------


def _into_dialogue(client, service):
    """Drive a session through reveal + engage + a failing reconciliation
    so region 0 is in the dialogue state."""
    session_id, regions = _through_reveal(client, service)
    _engage_all_via_routes(client, session_id, regions)
    region_id = regions[0].id
    client.post(
        f"/sessions/{session_id}/regions/{region_id}/reconcile",
        data={"body": "wrong"},  # FAIL
    )
    return session_id, region_id


def test_dialogue_surface_renders_two_pane_with_pinned_reading(client, service):
    session_id, region_id = _into_dialogue(client, service)
    r = client.get(f"/sessions/{session_id}/regions/{region_id}")
    assert r.status_code == 200
    # Both panes present.
    assert "reading-pane" in r.text and "action-pane" in r.text
    # Reading is pinned alongside.
    assert "FAKE READING" in r.text and "Your locked hypothesis" in r.text
    # Thread container and composer present.
    assert 'id="thread-container"' in r.text
    assert 'name="message"' in r.text
    # All three exits visible (override placeholder until commit 6).
    assert "Try again" in r.text
    assert "Disagree with the reading" in r.text
    assert "Not worth this depth" in r.text


def test_dialogue_surface_frames_judge_verdict_at_top_of_thread(
    client, service
):
    session_id, region_id = _into_dialogue(client, service)
    r = client.get(f"/sessions/{session_id}/regions/{region_id}")
    assert "Judge denied closure" in r.text
    assert "Not yet covered" in r.text


def test_htmx_dialogue_post_returns_thread_partial_not_full_page(
    client, service
):
    session_id, region_id = _into_dialogue(client, service)
    r = client.post(
        f"/sessions/{session_id}/regions/{region_id}/dialogue",
        data={"message": "why does the retry bound out at 3?"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    # The partial — no <html>, no header chrome.
    assert "<html" not in r.text.lower()
    assert 'id="thread-container"' in r.text
    # The engineer's message AND the fake model reply both rendered.
    assert "why does the retry bound out" in r.text
    assert "FAKE REPLY" in r.text


def test_non_htmx_dialogue_post_redirects_for_graceful_degradation(
    client, service
):
    session_id, region_id = _into_dialogue(client, service)
    r = client.post(
        f"/sessions/{session_id}/regions/{region_id}/dialogue",
        data={"message": "hi"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/sessions/{session_id}/regions/{region_id}"


def test_thread_renders_persistent_history_across_turns(client, service):
    session_id, region_id = _into_dialogue(client, service)
    for msg in ("first question", "second question", "third question"):
        client.post(
            f"/sessions/{session_id}/regions/{region_id}/dialogue",
            data={"message": msg},
        )
    r = client.get(f"/sessions/{session_id}/regions/{region_id}")
    # Every engineer turn is rendered, in order.
    body = r.text
    assert body.index("first question") < body.index("second question") < body.index(
        "third question"
    )
    # Each engineer turn paired with a model reply.
    assert body.count("FAKE REPLY") == 3


def test_revise_teach_back_button_is_a_dialogue_exit(client, service):
    session_id, region_id = _into_dialogue(client, service)
    r = client.post(
        f"/sessions/{session_id}/regions/{region_id}/revise",
        data={"body": "I understand now"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    region = service.get_region(session_id, region_id)
    # PASS via 'understand' → AWAITING_DISPOSITION.
    assert region.status.value == "awaiting_disposition"


def test_disagree_exit_records_optional_reason(client, service):
    session_id, region_id = _into_dialogue(client, service)
    client.post(
        f"/sessions/{session_id}/regions/{region_id}/disagree",
        data={"reason": "the reading conflates two distinct call sites"},
    )
    region = service.get_region(session_id, region_id)
    assert region.closure_mode.value == "engineer_disagreed"
    assert "two distinct call sites" in region.disagreement_reason


# --- 404s ------------------------------------------------------------------


def test_unknown_session_resume_returns_404(client):
    r = client.get("/sessions/does-not-exist", follow_redirects=False)
    assert r.status_code == 404


def test_unknown_region_returns_404(client):
    session_id = _start(client)
    r = client.get(f"/sessions/{session_id}/regions/does-not-exist")
    assert r.status_code == 404
