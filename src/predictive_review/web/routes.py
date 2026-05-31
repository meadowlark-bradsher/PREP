"""HTTP routes wrapping SessionService.

Routes are deliberately thin: each handler does request parsing, calls
a SessionService method, and renders a template or issues a redirect.
No business logic lives here — the orchestrator is the contract.

Commit 3 lands the skeleton: launcher, hypothesis tabs, reveal action
(auto-engages every region until commit 4 wires the three-way choice),
reconciliation, an editor-shaped dialogue, disposition, and summary.

Routes that take a session_id and region_id check that the region
belongs to the session via SessionService internals; bad URLs surface
as 404s through HTTPException, not silent.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

logger = logging.getLogger("predictive_review.web")

from ..sessions.errors import InvalidPhaseTransition, InvalidRegionStatus
from ..sessions.service import RegionSnapshot, SessionService
from ..storage.models import (
    DispositionStatus,
    EngagementThreshold,
    OverrideReason,
    ReconciliationLayout,
    RegionStatus,
    SessionPhase,
)
from .app import TEMPLATES, get_service
from .git_log import list_commits


def register_routes(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/launch", status_code=303)

    @app.get("/launch", response_class=HTMLResponse)
    def launch_form(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request, "launcher.html", {"error": None}
        )

    @app.post("/launch")
    def launch_submit(
        request: Request,
        engineer: str = Form("default"),
        diff_text: str = Form(""),
        commit: str = Form(""),
        picked_commit: str = Form(""),
        git_range: str = Form(""),
        repo: str = Form("."),
        engagement_threshold: str = Form("default"),
        service: SessionService = Depends(get_service),
    ) -> RedirectResponse:
        # The commit-browser submits picked_commit; the manual SHA input
        # submits commit. Prefer the browser pick when both are present —
        # someone left text in the collapsed details and then clicked a
        # card.
        effective_commit = picked_commit.strip() or commit.strip()
        chosen = [
            v
            for v in (diff_text.strip(), effective_commit, git_range.strip())
            if v
        ]
        if len(chosen) != 1:
            return TEMPLATES.TemplateResponse(
                request,
                "launcher.html",
                {
                    "error": "Pick a commit, or open the advanced section and provide exactly one of SHA / range / paste.",
                },
                status_code=400,
            )

        repo = repo.strip() or "."
        source_commit: Optional[str] = None
        source_range: Optional[str] = None
        if effective_commit:
            try:
                diff_text = _git_show(effective_commit, repo_path=repo)
            except RuntimeError as e:
                return TEMPLATES.TemplateResponse(
                    request,
                    "launcher.html",
                    {"error": str(e)},
                    status_code=400,
                )
            source_commit = effective_commit
        elif git_range.strip():
            try:
                diff_text = _git_diff(git_range.strip(), repo_path=repo)
            except RuntimeError as e:
                return TEMPLATES.TemplateResponse(
                    request,
                    "launcher.html",
                    {"error": str(e)},
                    status_code=400,
                )
            source_range = git_range.strip()

        try:
            threshold_enum = EngagementThreshold(engagement_threshold)
        except ValueError:
            return TEMPLATES.TemplateResponse(
                request,
                "launcher.html",
                {"error": f"Unknown engagement threshold: {engagement_threshold!r}"},
                status_code=400,
            )

        try:
            session_id = service.submit(
                diff_text=diff_text,
                engineer_identifier=engineer.strip() or "default",
                selector_name="llm_judgment",
                layout=ReconciliationLayout.INLINE_HUNK,
                engagement_threshold=threshold_enum,
                source_commit=source_commit,
                source_range=source_range,
            )
        except ValueError as e:
            return TEMPLATES.TemplateResponse(
                request,
                "launcher.html",
                {"error": str(e)},
                status_code=400,
            )
        return RedirectResponse(url=f"/sessions/{session_id}", status_code=303)

    @app.get("/commits", response_class=HTMLResponse)
    def commits_fragment(
        request: Request,
        repo: str = ".",
    ) -> HTMLResponse:
        """HTMX-loaded fragment showing recent commits in the given repo.

        Returns a partial that the launcher swaps into its commit list.
        Errors render as an inline error fragment so the user can fix
        the repo path and retry without reloading.
        """
        repo = repo.strip() or "."
        try:
            commits = list_commits(repo_path=repo, limit=30)
        except RuntimeError as e:
            return TEMPLATES.TemplateResponse(
                request,
                "_commit_error.html",
                {"error": str(e), "repo": repo},
                status_code=400,
            )
        return TEMPLATES.TemplateResponse(
            request,
            "_commit_list.html",
            {"commits": commits, "repo": repo},
        )

    @app.get("/sessions/{session_id}")
    def resume(
        session_id: str,
        service: SessionService = Depends(get_service),
    ) -> RedirectResponse:
        url = _resume_url(service, session_id)
        return RedirectResponse(url=url, status_code=303)

    @app.get("/sessions/{session_id}/hypothesis", response_class=HTMLResponse)
    def hypothesis_view(
        request: Request,
        session_id: str,
        service: SessionService = Depends(get_service),
    ) -> HTMLResponse:
        session = _require_session(service, session_id)
        if session.current_phase is not SessionPhase.HYPOTHESIS:
            return RedirectResponse(
                url=_resume_url(service, session_id), status_code=303
            )
        regions = service.list_regions(session_id)
        # Pre-fill the latest draft (locked OR unlocked) so the engineer
        # can refresh, navigate away and come back, or recover from a
        # validation error without losing their typing.
        drafts = {
            r.id: service.get_current_hypothesis_text(r.id) or ""
            for r in regions
        }
        return TEMPLATES.TemplateResponse(
            request,
            "hypothesis.html",
            {
                "session_id": session_id,
                "regions": regions,
                "drafts": drafts,
                "error": None,
            },
        )

    @app.post("/sessions/{session_id}/hypothesis")
    async def hypothesis_save(
        session_id: str,
        request: Request,
        service: SessionService = Depends(get_service),
    ):
        form = await request.form()
        action = form.get("action") or "save"
        regions = service.list_regions(session_id)

        # Save every non-empty hypothesis as a revision — even on the
        # reveal path, before any validation, so a single empty tab never
        # nukes the engineer's typing in the other tabs.
        for r in regions:
            body = (form.get(f"hypothesis_{r.id}") or "").strip()
            if body:
                service.save_hypothesis(
                    session_id=session_id, region_id=r.id, body=body
                )

        if action == "reveal":
            missing = [
                r.structural_label
                for r in regions
                if not (form.get(f"hypothesis_{r.id}") or "").strip()
            ]
            if missing:
                # Re-render the hypothesis page with an inline error and
                # the engineer's drafts intact (sourced from DB and the
                # submitted form so even unsaved blank-then-typed text
                # comes back).
                drafts = {
                    r.id: (
                        (form.get(f"hypothesis_{r.id}") or "").strip()
                        or service.get_current_hypothesis_text(r.id)
                        or ""
                    )
                    for r in regions
                }
                return TEMPLATES.TemplateResponse(
                    request,
                    "hypothesis.html",
                    {
                        "session_id": session_id,
                        "regions": regions,
                        "drafts": drafts,
                        "error": (
                            "Reveal needs a hypothesis for every region. "
                            f"Still empty: {', '.join(missing)}. "
                            "Even \"I have no idea\" is a valid commit."
                        ),
                    },
                    status_code=400,
                )
            # Inline the reveal here instead of redirecting to a POST
            # endpoint — 303s follow as GETs, which would 405 against a
            # POST-only /reveal. The reveal is the natural consequence
            # of submitting hypotheses with action=reveal anyway.
            try:
                service.lock_and_reveal(session_id=session_id)
            except InvalidPhaseTransition as e:
                logger.warning(
                    "lock_and_reveal failed for session %s: %s", session_id, e
                )
                raise HTTPException(status_code=400, detail=str(e))
            logger.info("session %s revealed", session_id)
            return RedirectResponse(
                url=_resume_url(service, session_id), status_code=303
            )
        return RedirectResponse(
            url=f"/sessions/{session_id}/hypothesis", status_code=303
        )

    @app.post("/sessions/{session_id}/regions/{region_id}/engage")
    def engage(
        session_id: str,
        region_id: str,
        service: SessionService = Depends(get_service),
    ) -> RedirectResponse:
        service.engage_region(session_id=session_id, region_id=region_id)
        return RedirectResponse(
            url=f"/sessions/{session_id}/regions/{region_id}", status_code=303
        )

    @app.post("/sessions/{session_id}/regions/{region_id}/acknowledge")
    def acknowledge(
        session_id: str,
        region_id: str,
        note: str = Form(...),
        service: SessionService = Depends(get_service),
    ) -> RedirectResponse:
        try:
            service.acknowledge_region(
                session_id=session_id, region_id=region_id, note=note
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # Acknowledge lands the region at AWAITING_DISPOSITION — the
        # engineer is sent to the same region surface, which now shows
        # the accept/flag form.
        return RedirectResponse(
            url=f"/sessions/{session_id}/regions/{region_id}", status_code=303
        )

    @app.post("/sessions/{session_id}/regions/{region_id}/defer")
    def defer(
        session_id: str,
        region_id: str,
        service: SessionService = Depends(get_service),
    ) -> RedirectResponse:
        service.defer_region(session_id=session_id, region_id=region_id)
        # Resume picks the next not-CLOSED not-deferred region, so the
        # engineer is moved on. They can revisit via the chip strip.
        return RedirectResponse(
            url=_resume_url(service, session_id), status_code=303
        )

    @app.get(
        "/sessions/{session_id}/regions/{region_id}",
        response_class=HTMLResponse,
    )
    def region_view(
        request: Request,
        session_id: str,
        region_id: str,
        service: SessionService = Depends(get_service),
    ) -> HTMLResponse:
        session = _require_session(service, session_id)
        region = _require_region(service, session_id, region_id)
        if session.current_phase is SessionPhase.COMPLETE:
            return RedirectResponse(
                url=f"/sessions/{session_id}/summary", status_code=303
            )
        reading = service.get_reading(region_id) or ""
        hypothesis = service.get_locked_hypothesis(region_id) or ""
        reconciliation = service.get_reconciliation(region_id) or ""
        turns = service.get_dialogue_turns(region_id)
        attempts = service.get_closure_attempts(region_id)
        latest_fail = next(
            (a for a in reversed(attempts) if a.verdict.value == "fail"),
            None,
        )
        return TEMPLATES.TemplateResponse(
            request,
            "region.html",
            {
                "session_id": session_id,
                "region": region,
                "regions": service.list_regions(session_id),
                "hypothesis": hypothesis,
                "reading": reading,
                "reconciliation": reconciliation,
                "turns": turns,
                "latest_fail": latest_fail,
            },
        )

    @app.post("/sessions/{session_id}/regions/{region_id}/reconcile")
    def submit_reconciliation(
        session_id: str,
        region_id: str,
        body: str = Form(...),
        service: SessionService = Depends(get_service),
    ) -> RedirectResponse:
        if not body.strip():
            raise HTTPException(
                status_code=400, detail="Reconciliation cannot be empty."
            )
        try:
            service.submit_reconciliation(
                session_id=session_id, region_id=region_id, body=body.strip()
            )
        except InvalidRegionStatus as e:
            raise HTTPException(status_code=400, detail=str(e))
        return RedirectResponse(
            url=f"/sessions/{session_id}/regions/{region_id}", status_code=303
        )

    @app.post("/sessions/{session_id}/regions/{region_id}/dialogue")
    def submit_dialogue_turn(
        request: Request,
        session_id: str,
        region_id: str,
        message: str = Form(...),
        service: SessionService = Depends(get_service),
    ):
        if not message.strip():
            raise HTTPException(
                status_code=400, detail="Message cannot be empty."
            )
        service.dialogue_turn(
            session_id=session_id,
            region_id=region_id,
            engineer_message=message.strip(),
        )
        # HTMX requests get the thread partial swapped in-place; legacy
        # form posts get a full-page redirect.
        if request.headers.get("HX-Request"):
            return _render_thread_partial(request, service, session_id, region_id)
        return RedirectResponse(
            url=f"/sessions/{session_id}/regions/{region_id}", status_code=303
        )

    @app.post("/sessions/{session_id}/regions/{region_id}/revise")
    def revise_teach_back(
        session_id: str,
        region_id: str,
        body: str = Form(...),
        service: SessionService = Depends(get_service),
    ) -> RedirectResponse:
        if not body.strip():
            raise HTTPException(
                status_code=400, detail="Revised teach-back cannot be empty."
            )
        service.submit_revised_teach_back(
            session_id=session_id, region_id=region_id, body=body.strip()
        )
        return RedirectResponse(
            url=f"/sessions/{session_id}/regions/{region_id}", status_code=303
        )

    @app.post("/sessions/{session_id}/regions/{region_id}/override")
    def submit_override(
        session_id: str,
        region_id: str,
        reason: str = Form(...),
        service: SessionService = Depends(get_service),
    ) -> RedirectResponse:
        """Engineer overrides the engagement demand. Records value /
        toil / difficulty as structured signal — no model consumes it
        in v1.5, but a future calibration system will."""
        try:
            reason_enum = OverrideReason(reason)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown override reason: {reason!r}",
            )
        try:
            service.submit_override(
                session_id=session_id,
                region_id=region_id,
                reason=reason_enum,
            )
        except InvalidRegionStatus as e:
            raise HTTPException(status_code=400, detail=str(e))
        return RedirectResponse(
            url=f"/sessions/{session_id}/regions/{region_id}", status_code=303
        )

    @app.post("/sessions/{session_id}/regions/{region_id}/disagree")
    def close_with_disagreement(
        session_id: str,
        region_id: str,
        reason: str = Form(""),
        service: SessionService = Depends(get_service),
    ) -> RedirectResponse:
        service.close_with_disagreement(
            session_id=session_id,
            region_id=region_id,
            reason=reason.strip() or None,
        )
        return RedirectResponse(
            url=f"/sessions/{session_id}/regions/{region_id}", status_code=303
        )

    @app.post("/sessions/{session_id}/regions/{region_id}/dispose")
    def set_disposition(
        session_id: str,
        region_id: str,
        action: str = Form(...),
        justification: str = Form(""),
        service: SessionService = Depends(get_service),
    ) -> RedirectResponse:
        if action == "accept":
            status = DispositionStatus.ACCEPTED_AS_IS
        elif action == "flag":
            if not justification.strip():
                raise HTTPException(
                    status_code=400,
                    detail="Flag for redesign requires a justification.",
                )
            status = DispositionStatus.FLAGGED_FOR_REDESIGN
        else:
            raise HTTPException(
                status_code=400, detail=f"Unknown disposition action: {action}"
            )
        service.set_disposition(
            session_id=session_id,
            region_id=region_id,
            status=status,
            justification=justification.strip() or None,
        )
        return RedirectResponse(
            url=_resume_url(service, session_id), status_code=303
        )

    @app.get("/sessions/{session_id}/summary", response_class=HTMLResponse)
    def summary(
        request: Request,
        session_id: str,
        service: SessionService = Depends(get_service),
    ) -> HTMLResponse:
        session = _require_session(service, session_id)
        regions = service.list_regions(session_id)
        per_region = []
        for r in regions:
            per_region.append(
                {
                    "region": r,
                    "hypothesis": service.get_locked_hypothesis(r.id) or "",
                    "reading": service.get_reading(r.id) or "",
                    "reconciliation": service.get_reconciliation(r.id) or "",
                    "turns": service.get_dialogue_turns(r.id),
                    "attempts": service.get_closure_attempts(r.id),
                    "disposition": service.get_disposition(r.id),
                }
            )
        return TEMPLATES.TemplateResponse(
            request,
            "summary.html",
            {
                "session": session,
                "session_id": session_id,
                "per_region": per_region,
            },
        )


def _render_thread_partial(
    request: Request,
    service: SessionService,
    session_id: str,
    region_id: str,
) -> HTMLResponse:
    """Render just the dialogue thread + composer + exits.

    Returned in response to HTMX submits of the composer, so the page
    swaps the thread in place rather than reloading. The non-HTMX path
    still does a full redirect for graceful degradation.
    """
    region = service.get_region(session_id, region_id)
    if region is None:
        raise HTTPException(status_code=404, detail="Region not found")
    turns = service.get_dialogue_turns(region_id)
    attempts = service.get_closure_attempts(region_id)
    latest_fail = next(
        (a for a in reversed(attempts) if a.verdict.value == "fail"), None
    )
    return TEMPLATES.TemplateResponse(
        request,
        "_thread.html",
        {
            "session_id": session_id,
            "region": region,
            "turns": turns,
            "latest_fail": latest_fail,
        },
    )


# --- helpers ---------------------------------------------------------------


def _require_session(service: SessionService, session_id: str):
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _require_region(
    service: SessionService, session_id: str, region_id: str
) -> RegionSnapshot:
    region = service.get_region(session_id, region_id)
    if region is None:
        raise HTTPException(status_code=404, detail="Region not found")
    return region


def _resume_url(service: SessionService, session_id: str) -> str:
    """Pick the right screen for the current state of this session.

    Resume order during RECONCILIATION:
      1. Active (not-CLOSED, not-deferred) regions, in ordinal order.
      2. If none active remain, the first deferred region — at which
         point the engineer must engage / acknowledge to close out.
      3. If everything is CLOSED but the session phase still says
         RECONCILIATION, the summary is the graceful fallback.
    """
    session = _require_session(service, session_id)
    if session.current_phase is SessionPhase.HYPOTHESIS:
        return f"/sessions/{session_id}/hypothesis"
    if session.current_phase is SessionPhase.COMPLETE:
        return f"/sessions/{session_id}/summary"
    regions = service.list_regions(session_id)
    for r in regions:
        if r.status is not RegionStatus.CLOSED and not r.is_deferred:
            return f"/sessions/{session_id}/regions/{r.id}"
    for r in regions:
        if r.status is not RegionStatus.CLOSED:  # deferred
            return f"/sessions/{session_id}/regions/{r.id}"
    return f"/sessions/{session_id}/summary"


def _git_show(sha: str, repo_path: str = ".") -> str:
    return _run_git(["git", "-C", repo_path, "show", sha])


def _git_diff(rng: str, repo_path: str = ".") -> str:
    return _run_git(["git", "-C", repo_path, "diff", rng])


def _run_git(args: list[str]) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, check=True
        )
    except FileNotFoundError as e:
        raise RuntimeError("git executable not found on PATH") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"{' '.join(args)} failed: {e.stderr.strip()}"
        ) from e
    # Strip git-show preamble up to the first diff.
    lines = result.stdout.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("diff --git"):
            return "".join(lines[i:])
    raise RuntimeError("git output contains no diff")
