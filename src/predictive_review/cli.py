"""Predictive Review CLI.

The CLI is the v1 production wiring for the application: it stands in
for the FastAPI web layer until that exists, and it remains useful for
non-interactive automation afterwards (scripted dogfooding, regression
runs, etc.).

Commands are intentionally narrow. `prep init-db` brings a fresh DB to
the current migration head. `prep run` walks a single full session
end-to-end.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import click

from .judge import JudgeOutcome
from .logging_config import configure_logging
from .sessions.service import RegionSnapshot, SessionService
from .storage.models import (
    DispositionStatus,
    EngagementThreshold,
    OverrideReason,
    ReconciliationLayout,
    RegionStatus,
)
from .wiring import build_default_service


@click.group()
def cli() -> None:
    """Predictive Review CLI."""
    configure_logging()
    """Predictive Review — prior elicitation with reconciliation."""


@cli.command("init-db")
@click.option(
    "--alembic-config",
    "alembic_config",
    type=click.Path(exists=True, dir_okay=False),
    default="alembic.ini",
    show_default=True,
    help="Path to alembic.ini. Defaults to alembic.ini in the current directory.",
)
def init_db(alembic_config: str) -> None:
    """Apply Alembic migrations to bring the database to head."""
    from alembic import command
    from alembic.config import Config

    cfg_path = Path(alembic_config).resolve()
    if not cfg_path.exists():
        raise click.UsageError(
            f"alembic config not found at {cfg_path}. "
            "Run from the project root, or pass --alembic-config."
        )
    cfg = Config(str(cfg_path))
    command.upgrade(cfg, "head")
    click.echo(f"applied migrations using {cfg_path}")


@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
@click.option("--reload/--no-reload", default=False, help="Enable uvicorn auto-reload (development).")
def serve(host: str, port: int, reload: bool) -> None:
    """Run the FastAPI web UI."""
    import uvicorn

    uvicorn.run(
        "predictive_review.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


@cli.command("run")
@click.option(
    "--diff",
    "diff_source",
    type=click.File("r"),
    default=None,
    help="Path to a unified diff file. Use '-' for stdin.",
)
@click.option(
    "--commit",
    "commit_sha",
    default=None,
    help="Run `git show <sha>` and use that diff (e.g. HEAD, HEAD~1, 2ea9187).",
)
@click.option(
    "--range",
    "git_range",
    default=None,
    help="Run `git diff <range>` and use that diff (e.g. main..HEAD).",
)
@click.option(
    "--engineer",
    default="default",
    show_default=True,
    help="Engineer identifier recorded on the session.",
)
@click.option(
    "--selector",
    default="llm_judgment",
    show_default=True,
    help="Selector name. 'first_n_hunks' is a no-LLM dev selector for dry runs.",
)
@click.option(
    "--layout",
    type=click.Choice(["inline", "no-hunk"]),
    default="inline",
    show_default=True,
    help="Reconciliation layout. 'inline' shows the hunk in the reconciliation editor.",
)
@click.option(
    "--threshold",
    type=click.Choice(["load_bearing_only", "default", "thorough"]),
    default="default",
    show_default=True,
    help=(
        "Session-level engagement threshold. Modulates selector "
        "aggressiveness and judge coverage bar."
    ),
)
def run(
    diff_source,
    commit_sha: str | None,
    git_range: str | None,
    engineer: str,
    selector: str,
    layout: str,
    threshold: str,
) -> None:
    """Walk one Predictive Review session interactively, end-to-end.

    Diff source is one of: --diff (file or stdin), --commit (git show), or
    --range (git diff). With none of these, reads from stdin.

    After reveal, each region prompts a three-way choice: engage (full
    reconciliation), acknowledge (close without engaging, requires a one-line
    note), or defer (revisit later). Deferred regions block session
    completion until resolved.
    """
    diff_text = _resolve_diff_text(diff_source, commit_sha, git_range)
    if not diff_text.strip():
        raise click.UsageError("diff is empty")

    service = build_default_service()
    layout_enum = (
        ReconciliationLayout.INLINE_HUNK
        if layout == "inline"
        else ReconciliationLayout.NO_HUNK
    )
    threshold_enum = EngagementThreshold(threshold)
    source_commit = commit_sha or None
    source_range = git_range or None

    click.echo("Submitting diff and selecting regions...")
    session_id = service.submit(
        diff_text=diff_text,
        engineer_identifier=engineer,
        selector_name=selector,
        layout=layout_enum,
        engagement_threshold=threshold_enum,
        source_commit=source_commit,
        source_range=source_range,
    )
    regions = service.list_regions(session_id)
    click.echo(f"Session {session_id}")
    click.echo(f"Selected {len(regions)} regions:")
    for r in regions:
        click.echo(f"  {r.ordinal + 1}. {r.structural_label}")

    _run_hypothesis_phase(service, session_id, regions)

    click.echo("\nLocking hypotheses and generating readings (this calls the LLM)...")
    service.lock_and_reveal(session_id=session_id)

    _run_v1_5_main_loop(service, session_id, layout_enum)

    final = service.get_session(session_id)
    if final and final.current_phase.value == "complete":
        click.secho(f"\nSession {session_id} complete.", fg="green")
    else:
        click.secho(
            f"\nSession {session_id} left incomplete (regions remain unresolved).",
            fg="yellow",
        )


# --- diff sourcing -------------------------------------------------------


def _resolve_diff_text(
    diff_source,
    commit_sha: str | None,
    git_range: str | None,
) -> str:
    sources_given = sum(
        x is not None for x in (diff_source, commit_sha, git_range)
    )
    if sources_given > 1:
        raise click.UsageError(
            "--diff, --commit, and --range are mutually exclusive"
        )
    if commit_sha:
        return _strip_to_diff(_run_git(["git", "show", commit_sha]))
    if git_range:
        return _strip_to_diff(_run_git(["git", "diff", git_range]))
    source = diff_source or click.get_text_stream("stdin")
    return source.read()


def _run_git(args: list[str]) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, check=True
        )
    except FileNotFoundError as e:
        raise click.ClickException(
            "git executable not found on PATH"
        ) from e
    except subprocess.CalledProcessError as e:
        raise click.ClickException(
            f"{' '.join(args)} failed:\n{e.stderr.strip()}"
        ) from e
    return result.stdout


def _strip_to_diff(output: str) -> str:
    """Strip the git-show preamble (commit / Author / Date / message) up to
    the first `diff --git` line. `git diff` output has no preamble so this
    is a no-op for it.
    """
    lines = output.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("diff --git"):
            return "".join(lines[i:])
    raise click.ClickException("git output contains no diff")


# --- hypothesis phase ----------------------------------------------------


def _run_hypothesis_phase(
    service: SessionService,
    session_id: str,
    regions: list[RegionSnapshot],
) -> None:
    template = _build_hypothesis_template(regions)
    edited = click.edit(template, extension=".md")
    if edited is None:
        raise click.Abort()

    hypotheses = _parse_hypotheses(edited, len(regions))
    for region, body in zip(regions, hypotheses):
        service.save_hypothesis(
            session_id=session_id, region_id=region.id, body=body
        )


_HYPOTHESIS_PLACEHOLDER = "(write your hypothesis here)"


def _build_hypothesis_template(regions: list[RegionSnapshot]) -> str:
    parts = [
        "# Hypothesis phase",
        "#",
        "# Write your hypothesis for each region below. Move freely between",
        "# regions. Do not edit the >>>HYPOTHESIS N>>> / <<<HYPOTHESIS N<<<",
        "# markers. Save and exit your editor to commit and reveal.",
        "",
    ]
    n = len(regions)
    for r in regions:
        parts += [
            f"## Region {r.ordinal + 1} of {n}: {r.structural_label}",
            "",
            "```",
            r.hunk_text.rstrip(),
            "```",
            "",
            f">>>HYPOTHESIS {r.ordinal + 1}>>>",
            "",
            _HYPOTHESIS_PLACEHOLDER,
            "",
            f"<<<HYPOTHESIS {r.ordinal + 1}<<<",
            "",
        ]
    return "\n".join(parts)


def _parse_hypotheses(text: str, n: int) -> list[str]:
    out = []
    for i in range(1, n + 1):
        pattern = rf">>>HYPOTHESIS {i}>>>(.*?)<<<HYPOTHESIS {i}<<<"
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            raise click.UsageError(f"missing hypothesis markers for region {i}")
        body = m.group(1).strip()
        if not body or body == _HYPOTHESIS_PLACEHOLDER:
            raise click.UsageError(f"hypothesis for region {i} is empty")
        out.append(body)
    return out


# --- v1.5 main loop ------------------------------------------------------


def _run_v1_5_main_loop(
    service: SessionService,
    session_id: str,
    layout: ReconciliationLayout,
) -> None:
    """Drive every region from AWAITING_REVEAL_CHOICE through to CLOSED,
    deferring to the engineer's three-way choice per region.

    Order:
      1. Active (not-CLOSED, not-deferred) regions in ordinal order.
      2. After the active pass, prompt to revisit deferred regions.
      3. Engineer can leave deferred regions unresolved by declining
         to revisit; the session remains incomplete.
    """
    while True:
        snaps = service.list_regions(session_id)
        active = [
            s for s in snaps
            if s.status is not RegionStatus.CLOSED and not s.is_deferred
        ]
        if active:
            for snap in active:
                _walk_one_region(service, session_id, snap.id, layout)
            continue

        deferred = [s for s in service.list_regions(session_id) if s.is_deferred]
        if not deferred:
            return

        click.echo("")
        click.secho(
            f"{len(deferred)} region(s) deferred — still to resolve:",
            fg="yellow",
        )
        for s in deferred:
            click.echo(f"  {s.ordinal + 1}. {s.structural_label}")
        if not click.confirm("Revisit them now?", default=True):
            return
        for snap in deferred:
            _walk_one_region(service, session_id, snap.id, layout)


def _walk_one_region(
    service: SessionService,
    session_id: str,
    region_id: str,
    layout: ReconciliationLayout,
) -> None:
    """Drive a single region across whatever sub-state it's in.

    The region state can change inside each branch; we re-read after each
    transition. Returns early when the region defers or closes.
    """
    region = service.get_region(session_id, region_id)
    if region is None or region.status is RegionStatus.CLOSED:
        return

    if region.status is RegionStatus.AWAITING_REVEAL_CHOICE:
        choice = _run_reveal_choice(service, session_id, region)
        if choice == "defer":
            return
        region = service.get_region(session_id, region_id)
        if region is None:
            return

    if region.status is RegionStatus.AWAITING_RECONCILIATION:
        _run_region_reconciliation(service, session_id, region, layout)
        region = service.get_region(session_id, region_id)
        if region is None:
            return

    if region.status is RegionStatus.AWAITING_DISPOSITION:
        _prompt_disposition(service, session_id, region)


def _print_region_header(region: RegionSnapshot) -> None:
    click.echo("")
    click.secho("=" * 60, fg="cyan")
    click.secho(
        f"Region {region.ordinal + 1}: {region.structural_label}",
        fg="cyan",
        bold=True,
    )
    click.secho("=" * 60, fg="cyan")


def _run_reveal_choice(
    service: SessionService,
    session_id: str,
    region: RegionSnapshot,
) -> str:
    """Three-way choice prompt. Returns one of 'engage' / 'acknowledge' / 'defer'."""
    _print_region_header(region)

    reading = service.get_reading(region.id) or "(no reading available)"
    hypothesis = service.get_locked_hypothesis(region.id) or "(none recorded)"

    click.secho("\nYour locked hypothesis:", bold=True)
    click.echo(hypothesis)
    click.secho("\nModel's reading:", bold=True)
    click.echo(reading)

    if region.is_deferred:
        click.secho(
            "\n(You previously deferred this region.)",
            fg="yellow",
        )

    click.echo("")
    choice = click.prompt(
        "How do you want to engage? (e)ngage / (a)cknowledge / (d)efer",
        type=click.Choice(["e", "a", "d"], case_sensitive=False),
    )
    if choice == "e":
        service.engage_region(session_id=session_id, region_id=region.id)
        return "engage"
    if choice == "a":
        while True:
            note = click.prompt(
                "Acknowledgment note (one line, required)", default=""
            ).strip()
            if note:
                break
            click.secho("Note is required for acknowledge.", fg="yellow")
        service.acknowledge_region(
            session_id=session_id, region_id=region.id, note=note
        )
        return "acknowledge"
    # defer
    service.defer_region(session_id=session_id, region_id=region.id)
    click.secho(
        "Region deferred. You'll be prompted to revisit it after the main pass.",
        fg="yellow",
    )
    return "defer"


# --- reconciliation + dialogue ------------------------------------------


def _run_region_reconciliation(
    service: SessionService,
    session_id: str,
    region: RegionSnapshot,
    layout: ReconciliationLayout,
) -> None:
    """Open the reconciliation editor and submit to the judge.

    The region header + reading were already printed by
    _run_reveal_choice; this function just handles the engaged-path
    write step and any failing-judge dialogue.
    """
    reading = service.get_reading(region.id)
    hypothesis = service.get_locked_hypothesis(region.id)
    if reading is None or hypothesis is None:
        raise click.ClickException(
            f"region {region.id} missing reading or hypothesis; cannot reconcile"
        )

    template = _build_reconciliation_template(region, hypothesis, reading, layout)
    edited = click.edit(template, extension=".md")
    if edited is None:
        raise click.Abort()

    reconciliation = _parse_reconciliation(edited)
    if not reconciliation:
        raise click.UsageError(
            f"reconciliation for region {region.ordinal + 1} is empty"
        )

    click.echo("\nSubmitting to closure judge...")
    result = service.submit_reconciliation(
        session_id=session_id, region_id=region.id, body=reconciliation
    )
    if result.verdict is JudgeOutcome.PASS:
        click.secho("Closure: PASS", fg="green")
    else:
        click.secho(f"Closure: FAIL — {result.missing_aspects}", fg="yellow")
        _run_dialogue_loop(service, session_id, region)


_RECONCILIATION_PLACEHOLDER = "(write your reconciliation here)"
_RECONCILIATION_SEPARATOR = "<<<<< RECONCILIATION BELOW >>>>>"


def _build_reconciliation_template(
    region: RegionSnapshot,
    hypothesis: str,
    reading: str,
    layout: ReconciliationLayout,
) -> str:
    parts = [
        f"# Reconciliation for Region {region.ordinal + 1}: {region.structural_label}",
        "#",
        "# Write your reconciliation in your own words below the separator.",
        "# What did your hypothesis get right? What did it miss? What is your",
        "# current understanding of this choice?",
        "# Save and exit to submit.",
        "",
    ]
    if layout is ReconciliationLayout.INLINE_HUNK:
        parts += ["## Hunk", "```", region.hunk_text.rstrip(), "```", ""]
    parts += [
        "## Reading",
        reading,
        "",
        "## Your locked hypothesis",
        hypothesis,
        "",
        _RECONCILIATION_SEPARATOR,
        "",
        _RECONCILIATION_PLACEHOLDER,
    ]
    return "\n".join(parts)


def _parse_reconciliation(text: str) -> str:
    if _RECONCILIATION_SEPARATOR not in text:
        raise click.UsageError("reconciliation template missing separator")
    after = text.split(_RECONCILIATION_SEPARATOR, 1)[1]
    body = "\n".join(
        line for line in after.splitlines() if not line.lstrip().startswith("#")
    ).strip()
    if body == _RECONCILIATION_PLACEHOLDER:
        return ""
    return body


def _run_dialogue_loop(
    service: SessionService,
    session_id: str,
    region: RegionSnapshot,
) -> None:
    click.echo("\nDialogue with the model. Commands:")
    click.echo("  :try-again  open editor for a revised teach-back, re-run judge")
    click.echo("  :disagree   close this region — the reading itself is wrong")
    click.echo("  :override   close this region — not worth this depth (value/toil/difficulty)")
    click.echo("  :edit       open editor for a multi-line dialogue message")
    click.echo("")

    while True:
        snap = next(
            r for r in service.list_regions(session_id) if r.id == region.id
        )
        if snap.status is not RegionStatus.IN_DIALOGUE:
            return

        msg = click.prompt("> ", prompt_suffix="", default="", show_default=False).strip()
        if not msg:
            continue

        if msg in (":try-again", ":done"):  # :done kept as alias for the old habit
            if _submit_revised_teach_back(service, session_id, region):
                return
        elif msg == ":disagree":
            reason = click.prompt(
                "Reason for disagreement (optional; press Enter to skip)",
                default="",
                show_default=False,
            ).strip()
            service.close_with_disagreement(
                session_id=session_id,
                region_id=region.id,
                reason=reason or None,
            )
            click.secho("Region closed with disagreement.", fg="yellow")
            return
        elif msg == ":override":
            axis = click.prompt(
                "Which axis? (v)alue / (t)oil / (d)ifficulty",
                type=click.Choice(["v", "t", "d"], case_sensitive=False),
            )
            reason_enum = {
                "v": OverrideReason.VALUE,
                "t": OverrideReason.TOIL,
                "d": OverrideReason.DIFFICULTY,
            }[axis]
            service.submit_override(
                session_id=session_id,
                region_id=region.id,
                reason=reason_enum,
            )
            click.secho(
                f"Region closed with override ({reason_enum.value}).", fg="yellow"
            )
            return
        elif msg == ":edit":
            edited = click.edit("(write your message here)", extension=".md")
            if edited is None:
                continue
            body = _strip_comments(edited).strip()
            if not body or body == "(write your message here)":
                continue
            _send_dialogue_turn(service, session_id, region, body)
        else:
            _send_dialogue_turn(service, session_id, region, msg)


def _submit_revised_teach_back(
    service: SessionService,
    session_id: str,
    region: RegionSnapshot,
) -> bool:
    template = (
        f"# Revised teach-back for Region {region.ordinal + 1}: "
        f"{region.structural_label}\n#\n"
        "# Save and exit to submit to the closure judge.\n\n"
        "(write your revised teach-back here)\n"
    )
    edited = click.edit(template, extension=".md")
    if edited is None:
        return False
    body = _strip_comments(edited).strip()
    if not body or body == "(write your revised teach-back here)":
        click.echo("revised teach-back is empty; back to dialogue")
        return False

    result = service.submit_revised_teach_back(
        session_id=session_id, region_id=region.id, body=body
    )
    if result.verdict is JudgeOutcome.PASS:
        click.secho("Closure: PASS", fg="green")
        return True
    click.secho(f"Closure: FAIL — {result.missing_aspects}", fg="yellow")
    return False


def _send_dialogue_turn(
    service: SessionService,
    session_id: str,
    region: RegionSnapshot,
    body: str,
) -> None:
    result = service.dialogue_turn(
        session_id=session_id, region_id=region.id, engineer_message=body
    )
    click.echo("")
    click.secho("Model:", bold=True)
    click.echo(result.model_body)
    click.echo("")


def _prompt_disposition(
    service: SessionService,
    session_id: str,
    region: RegionSnapshot,
) -> None:
    choice = click.prompt(
        "Disposition: (a)ccept as-is / (f)lag for redesign",
        type=click.Choice(["a", "f"], case_sensitive=False),
    )
    if choice == "f":
        justification = click.prompt("Justification (required)")
        service.set_disposition(
            session_id=session_id,
            region_id=region.id,
            status=DispositionStatus.FLAGGED_FOR_REDESIGN,
            justification=justification,
        )
    else:
        service.set_disposition(
            session_id=session_id,
            region_id=region.id,
            status=DispositionStatus.ACCEPTED_AS_IS,
        )


def _strip_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
