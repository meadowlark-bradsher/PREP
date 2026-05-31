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
    # Commit browser is the primary surface.
    assert "Pick a commit" in r.text
    assert 'name="repo"' in r.text
    assert 'hx-get="/commits"' in r.text
    # The legacy inputs are still in the form, just collapsed under details.
    assert 'name="diff_text"' in r.text
    assert 'name="commit"' in r.text
    assert 'name="git_range"' in r.text
    assert "Other diff sources" in r.text


def test_launch_rejects_no_input(client):
    r = client.post(
        "/launch",
        data={"engineer": "x", "diff_text": "", "commit": "", "git_range": ""},
    )
    assert r.status_code == 400
    # Error message points the engineer at the commit browser or the
    # collapsed advanced section; the literal "exactly one" phrasing
    # appears in the latter description.
    assert "Pick a commit" in r.text or "exactly one" in r.text


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


def test_hypothesis_save_action_accepts_blank_tabs(client, service):
    """Plain save (no reveal) should accept partial drafts — engineer can
    fill some tabs, save, come back later. Only reveal demands all-filled."""
    session_id = _start(client)
    regions = service.list_regions(session_id)
    data = {f"hypothesis_{r.id}": "" for r in regions}
    data["action"] = "save"
    r = client.post(
        f"/sessions/{session_id}/hypothesis", data=data, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/sessions/{session_id}/hypothesis"


def test_hypothesis_reveal_with_empty_tab_rerenders_with_error_preserving_drafts(
    client, service
):
    """The original 'reveal a partial form' bug: an unfilled tab nuked the
    other tabs' typing AND returned a raw JSON error. The fix renders the
    page back with an inline error and the filled drafts intact."""
    session_id = _start(client)
    regions = service.list_regions(session_id)
    # Fill region 0, leave region 1 blank.
    data = {
        f"hypothesis_{regions[0].id}": "Long thoughtful prediction here",
        f"hypothesis_{regions[1].id}": "",
        "action": "reveal",
    }
    r = client.post(f"/sessions/{session_id}/hypothesis", data=data)
    # Inline error response, not a JSON HTTPException.
    assert r.status_code == 400
    assert "application/json" not in r.headers.get("content-type", "")
    # Filled draft is preserved in the re-rendered form.
    assert "Long thoughtful prediction here" in r.text
    # Helpful error names the missing region.
    assert regions[1].structural_label in r.text
    assert "I have no idea" in r.text  # The hint about valid empty commits.
    # And the filled draft is in fact persisted to the DB as a revision.
    assert (
        service.get_current_hypothesis_text(regions[0].id)
        == "Long thoughtful prediction here"
    )


def test_hypothesis_save_with_reveal_action_triggers_reveal(client, service):
    session_id = _start(client)
    regions = service.list_regions(session_id)
    data = {f"hypothesis_{r.id}": "I think this is X" for r in regions}
    data["action"] = "reveal"
    r = client.post(
        f"/sessions/{session_id}/hypothesis", data=data, follow_redirects=False
    )
    # Submit-with-reveal does the lock_and_reveal inline and redirects
    # to the first region's reveal-choice surface in one 303 hop.
    assert r.status_code == 303
    assert (
        r.headers["location"]
        == f"/sessions/{session_id}/regions/{regions[0].id}"
    )


# --- reveal + region surface ----------------------------------------------


def _through_reveal(client, service) -> tuple[str, list]:
    """Submit → hypothesize → reveal. Regions are at AWAITING_REVEAL_CHOICE.

    The hypothesis POST with action=reveal does the lock_and_reveal inline,
    so this is a single request (the old standalone /reveal POST is gone).
    """
    session_id = _start(client)
    regions = service.list_regions(session_id)
    data = {f"hypothesis_{r.id}": "draft" for r in regions}
    data["action"] = "reveal"
    client.post(f"/sessions/{session_id}/hypothesis", data=data)
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


def test_hypothesis_reveal_redirect_target_is_first_region_not_a_405(
    client, service
):
    """Regression: the hypothesis POST used to 303 to a POST-only /reveal
    endpoint, which the browser followed as GET and the server returned 405.
    The reveal is now inlined into hypothesis POST and the redirect goes
    straight to the first region's surface."""
    session_id = _start(client)
    regions = service.list_regions(session_id)
    data = {f"hypothesis_{r.id}": "draft" for r in regions}
    data["action"] = "reveal"
    r = client.post(
        f"/sessions/{session_id}/hypothesis", data=data, follow_redirects=False
    )
    assert r.status_code == 303
    assert (
        r.headers["location"]
        == f"/sessions/{session_id}/regions/{regions[0].id}"
    )
    # Following the redirect lands on a real page, not a 405.
    landing = client.get(r.headers["location"])
    assert landing.status_code == 200


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


# --- structured override (value / toil / difficulty) ----------------------


def test_dialogue_surface_shows_three_override_buttons(client, service):
    session_id, region_id = _into_dialogue(client, service)
    r = client.get(f"/sessions/{session_id}/regions/{region_id}")
    # All three taxonomy axes have their own button.
    assert 'name="reason" value="value"' in r.text
    assert 'name="reason" value="toil"' in r.text
    assert 'name="reason" value="difficulty"' in r.text
    # Each axis has its explanatory description visible.
    assert "stakes" in r.text  # value description
    assert "interacting points" in r.text  # toil description
    assert "engagement cost" in r.text  # difficulty description


@pytest.mark.parametrize(
    "reason_str, expected_value",
    [("value", "value"), ("toil", "toil"), ("difficulty", "difficulty")],
)
def test_override_route_records_each_reason(
    client, service, reason_str, expected_value
):
    session_id, region_id = _into_dialogue(client, service)
    r = client.post(
        f"/sessions/{session_id}/regions/{region_id}/override",
        data={"reason": reason_str},
        follow_redirects=False,
    )
    assert r.status_code == 303
    region = service.get_region(session_id, region_id)
    assert region.status.value == "awaiting_disposition"
    assert region.closure_mode.value == "engineer_overrode"
    assert region.override_reason.value == expected_value


def test_override_route_rejects_unknown_reason(client, service):
    session_id, region_id = _into_dialogue(client, service)
    r = client.post(
        f"/sessions/{session_id}/regions/{region_id}/override",
        data={"reason": "vibes"},
    )
    assert r.status_code == 400
    # State unchanged.
    region = service.get_region(session_id, region_id)
    assert region.status.value == "in_dialogue"


def test_override_route_rejects_when_region_not_in_dialogue(client, service):
    """Overriding only makes sense mid-dialogue, after a judge denial.
    A region not in IN_DIALOGUE should be refused at the service layer."""
    session_id, regions = _through_reveal(client, service)
    # Region is at AWAITING_REVEAL_CHOICE, not IN_DIALOGUE.
    r = client.post(
        f"/sessions/{session_id}/regions/{regions[0].id}/override",
        data={"reason": "value"},
    )
    assert r.status_code == 400


def test_override_reason_surfaces_in_session_summary(client, service):
    """Drive through to completion via an override and verify the reason
    is visible on the summary page — the deliverable the calibration
    system would consume."""
    session_id, region_id = _into_dialogue(client, service)
    client.post(
        f"/sessions/{session_id}/regions/{region_id}/override",
        data={"reason": "toil"},
    )
    client.post(
        f"/sessions/{session_id}/regions/{region_id}/dispose",
        data={"action": "accept", "justification": ""},
    )
    # The other region needs to close too for the session to complete.
    # _into_dialogue already engaged it; reconcile and dispose through.
    other = next(r for r in service.list_regions(session_id) if r.id != region_id)
    client.post(
        f"/sessions/{session_id}/regions/{other.id}/reconcile",
        data={"body": "I understand"},  # PASS via FakeClosureJudge policy
    )
    client.post(
        f"/sessions/{session_id}/regions/{other.id}/dispose",
        data={"action": "accept", "justification": ""},
    )

    r = client.get(f"/sessions/{session_id}/summary")
    assert r.status_code == 200
    assert "Override reason" in r.text
    assert "toil" in r.text


# --- 404s ------------------------------------------------------------------


def test_launcher_renders_threshold_radio_with_default_checked(client):
    r = client.get("/launch")
    assert r.status_code == 200
    assert 'name="engagement_threshold"' in r.text
    assert 'value="load_bearing_only"' in r.text
    assert 'value="default" checked' in r.text
    assert 'value="thorough"' in r.text


def test_launch_persists_chosen_threshold_on_session(client, service):
    session_id = _start_with_threshold(client, "thorough")
    snap = service.get_session(session_id)
    assert snap is not None
    assert snap is not None  # for type-narrowing
    # The persisted threshold matches the radio choice.
    from predictive_review.storage.models import EngagementThreshold
    # SessionSnapshot doesn't carry engagement_threshold yet (not load-bearing
    # for the read-side UI); fetch from ORM via list_regions context.
    # Confirm via the service-level attribute on the persisted row:
    with service._session_factory() as db:  # type: ignore[attr-defined]
        from predictive_review.storage.models import Session as Sess
        row = db.get(Sess, session_id)
        assert row.engagement_threshold is EngagementThreshold.THOROUGH


def test_launch_rejects_unknown_threshold(client):
    r = client.post(
        "/launch",
        data={
            "engineer": "x",
            "diff_text": SAMPLE_DIFF,
            "commit": "",
            "git_range": "",
            "engagement_threshold": "vibes",
        },
    )
    assert r.status_code == 400


def test_threshold_flows_into_judge_call(client, service):
    """The judge call receives the engagement_threshold the session was
    submitted with. The FakeClosureJudge records every call's threshold
    in its .calls list."""
    session_id = _start_with_threshold(client, "load_bearing_only")
    regions = service.list_regions(session_id)
    data = {f"hypothesis_{r.id}": "draft" for r in regions}
    data["action"] = "reveal"
    client.post(f"/sessions/{session_id}/hypothesis", data=data)
    _engage_all_via_routes(client, session_id, regions)
    client.post(
        f"/sessions/{session_id}/regions/{regions[0].id}/reconcile",
        data={"body": "I understand"},
    )
    # FakeClosureJudge records (reading_body, teach_back_statement,
    # engagement_threshold) per call.
    from predictive_review.storage.models import EngagementThreshold
    assert service._judge.calls  # type: ignore[attr-defined]
    assert (
        service._judge.calls[0][2]  # type: ignore[attr-defined]
        is EngagementThreshold.LOAD_BEARING_ONLY
    )


def _start_with_threshold(client, threshold: str) -> str:
    r = client.post(
        "/launch",
        data={
            "engineer": "x",
            "diff_text": SAMPLE_DIFF,
            "commit": "",
            "git_range": "",
            "engagement_threshold": threshold,
        },
        follow_redirects=False,
    )
    return r.headers["location"].rsplit("/", 1)[-1]


def test_unknown_session_resume_returns_404(client):
    r = client.get("/sessions/does-not-exist", follow_redirects=False)
    assert r.status_code == 404


def test_unknown_region_returns_404(client):
    session_id = _start(client)
    r = client.get(f"/sessions/{session_id}/regions/does-not-exist")
    assert r.status_code == 404


# --- commit browser -------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path):
    """A throwaway local git repo with two commits, for browser tests."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "Add a.txt\n\nThe initial file."],
        cwd=tmp_path, check=True,
    )
    (tmp_path / "a.txt").write_text("hello\nworld\n")
    subprocess.run(
        ["git", "commit", "-q", "-am", "Add a second line\n\nWith a body too."],
        cwd=tmp_path, check=True,
    )
    return tmp_path


def test_commits_fragment_lists_recent_commits(client, git_repo):
    r = client.get(f"/commits?repo={git_repo}")
    assert r.status_code == 200
    assert "Add a second line" in r.text
    assert "Add a.txt" in r.text
    assert "Tester" in r.text
    # Each commit is a submit button targeting the launcher form.
    assert 'name="picked_commit"' in r.text
    assert 'form="launcher-form"' in r.text


def test_commits_fragment_renders_body_as_markdown(client, git_repo):
    r = client.get(f"/commits?repo={git_repo}")
    # Bodies go through the markdown filter — paragraphs wrapped in <p>.
    assert "<p>The initial file.</p>" in r.text


def test_commits_fragment_collapses_body_by_default(client, git_repo):
    """Browsing by title alone is the default; bodies are revealed by
    clicking the per-card '▸ Show body' toggle. The body element must
    therefore be present in the DOM but hidden, and the toggle button
    must have aria-expanded=false to start."""
    r = client.get(f"/commits?repo={git_repo}")
    assert 'class="commit-body prose" hidden' in r.text
    assert 'class="commit-expand"' in r.text
    assert 'aria-expanded="false"' in r.text
    # Cards without bodies don't get a toggle — locked in for later
    # when our test repo grows a subject-only commit.
    assert "▸ Show body" in r.text


def test_commits_fragment_returns_error_for_non_repo_path(client, tmp_path):
    r = client.get(f"/commits?repo={tmp_path}")
    assert r.status_code == 400
    assert "Couldn't list commits" in r.text


def test_launch_with_picked_commit_uses_git_show_in_that_repo(
    client, service, git_repo
):
    # Find the SHA of the most recent commit in the test repo.
    import subprocess

    sha = subprocess.run(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    r = client.post(
        "/launch",
        data={
            "engineer": "meadowlark",
            "picked_commit": sha,
            "repo": str(git_repo),
            "engagement_threshold": "default",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    session_id = r.headers["location"].rsplit("/", 1)[-1]
    snap = service.get_session(session_id)
    assert snap is not None
    assert snap.source_commit == sha
    # The diff for that commit was actually captured.
    from predictive_review.storage.models import Session as Sess
    with service._session_factory() as db:  # type: ignore[attr-defined]
        row = db.get(Sess, session_id)
        assert "a.txt" in row.diff_text


def test_launch_picked_commit_wins_over_manual_commit_field(client, git_repo):
    """Defensive: if the engineer left stale text in the collapsed manual
    SHA field and then clicked a commit card, picked_commit should win."""
    import subprocess

    sha = subprocess.run(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    r = client.post(
        "/launch",
        data={
            "engineer": "x",
            "picked_commit": sha,
            "commit": "stale-text-from-typing-earlier",
            "repo": str(git_repo),
            "engagement_threshold": "default",
        },
        follow_redirects=False,
    )
    # Picked wins; no error from the stale 'commit' field colliding.
    assert r.status_code == 303
