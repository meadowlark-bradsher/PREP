"""End-to-end `prep run --source manifest` over the fixture, with fakes.

Drives the real Click command — not the service underneath it — through
launch, hypothesis, lock/reveal, teach-back, a structured FAIL, a revised
teach-back, and disposition. No real LLM is constructed anywhere: the
whole service graph is replaced at the `build_default_service` seam.

The point is to catch the wiring the unit tests each half-see: that the
manifest source, the manifest selector, the criterion, the aspect scope
and the structured verdict all survive the trip through the CLI.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from predictive_review import cli as cli_module
from predictive_review.cli import cli
from predictive_review.judge import JudgeOutcome
from predictive_review.selectors.registry import default_registry
from predictive_review.sessions.service import SessionService
from predictive_review.storage.models import Base
from tests.fakes import FakeClosureJudge, FakeDialogueManager, FakeReadingGenerator

FIXTURE = Path(__file__).parent.parent / "fixtures" / "load_bearing_repo"


@pytest.fixture
def fake_service():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    judge = FakeClosureJudge()
    service = SessionService(
        session_factory=sessionmaker(bind=engine, expire_on_commit=False, future=True),
        selector_registry=default_registry,
        reading_generator=FakeReadingGenerator(),
        closure_judge=judge,
        dialogue_manager=FakeDialogueManager(),
    )
    service._test_judge = judge
    return service


@pytest.fixture
def run_cli(monkeypatch, fake_service):
    """Run the real command with the LLM graph and the editor stubbed."""
    monkeypatch.setattr(cli_module, "build_default_service", lambda: fake_service)

    edits: list[str] = []

    def fake_edit(template, extension=".md"):
        """Stand in for the engineer at the editor.

        Substitutes into the real template rather than returning a canned
        document, so the CLI's own marker format and parsers are exercised
        instead of bypassed.
        """
        answer = edits.pop(0) if edits else "a written answer"
        for placeholder in (
            cli_module._HYPOTHESIS_PLACEHOLDER,
            cli_module._RECONCILIATION_PLACEHOLDER,
            "(write your revised teach-back here)",
        ):
            template = template.replace(placeholder, answer)
        return template

    monkeypatch.setattr(cli_module.click, "edit", fake_edit)

    def _run(args, *, editor_answers, stdin=""):
        edits.clear()
        edits.extend(editor_answers)
        return CliRunner().invoke(cli, args, input=stdin, catch_exceptions=False)

    return _run


def test_manifest_run_reaches_a_structured_verdict(run_cli, fake_service) -> None:
    result = run_cli(
        [
            "run",
            "--source", "manifest",
            "--repo", str(FIXTURE),
            "--selector", "manifest",
            "--criterion", "correctness",
            "--threshold", "load_bearing_only",
            "--layout", "no-hunk",
            "--engineer", "meadowlark",
        ],
        editor_answers=[
            "my prior guess",             # hypotheses (both regions, one editor pass)
            "it retries sometimes",       # teach-back 1 -> FAIL (structured)
            "I understand the retry cap", # revised teach-back -> PASS
            "it retries sometimes",       # region 2 teach-back -> FAIL
            "I understand this too",      # region 2 revised -> PASS
        ],
        # per region: reveal choice 'e', then after FAIL ':try-again',
        # then disposition 'a' (addressed)
        stdin="e\n:try-again\na\ne\n:try-again\na\n",
    )

    assert result.exit_code == 0, result.output
    out = result.output

    # launch surface
    assert "Ordering by criterion: correctness" in out
    assert "1 member(s) stale" in out
    assert "cache/eviction" in out
    assert "auth/token-refresh" in out

    # the structured verdict reached the screen
    assert "Closure: FAIL" in out
    assert "retry-bound" in out or "failure-modes-distinct" in out
    assert "Closure: PASS" in out
    assert "complete" in out.lower()


def test_stale_member_never_appears_as_a_region(run_cli) -> None:
    result = run_cli(
        [
            "run", "--source", "manifest", "--repo", str(FIXTURE),
            "--selector", "manifest", "--threshold", "load_bearing_only",
            "--layout", "no-hunk",
        ],
        editor_answers=[
            "my prior guess", "I understand it", "I understand it",
        ],
        stdin="e\na\ne\na\n",
    )
    assert result.exit_code == 0, result.output
    # named in the staleness report, never offered as a region to review
    assert "cache/eviction" in result.output
    region_lines = [
        line for line in result.output.splitlines()
        if line.strip().startswith(("1.", "2.", "3."))
    ]
    assert not any("cache/eviction" in line for line in region_lines)


def test_default_criterion_is_used_when_none_given(run_cli) -> None:
    result = run_cli(
        [
            "run", "--source", "manifest", "--repo", str(FIXTURE),
            "--selector", "manifest", "--threshold", "load_bearing_only",
            "--layout", "no-hunk",
        ],
        editor_answers=[
            "my prior guess", "I understand it", "I understand it",
        ],
        stdin="e\na\ne\na\n",
    )
    assert result.exit_code == 0, result.output
    assert "Ordering by criterion: identity" in result.output


def test_unknown_criterion_fails_at_launch(run_cli) -> None:
    result = run_cli(
        [
            "run", "--source", "manifest", "--repo", str(FIXTURE),
            "--selector", "manifest", "--criterion", "maintainability",
        ],
        editor_answers=[],
    )
    assert result.exit_code != 0
    assert "maintainability" in result.output
    for declared in ("correctness", "churn-90d", "identity"):
        assert declared in result.output
    assert "Session " not in result.output, "no session may be announced"
