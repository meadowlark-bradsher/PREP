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
from pathlib import Path

import click

from .judge import JudgeOutcome
from .sessions.service import RegionSnapshot, SessionService
from .storage.models import (
    DispositionStatus,
    ReconciliationLayout,
    RegionStatus,
)
from .wiring import build_default_service


@click.group()
def cli() -> None:
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


@cli.command("run")
@click.option(
    "--diff",
    "diff_source",
    type=click.File("r"),
    default="-",
    help="Path to a unified diff. Reads stdin if omitted (use '-' explicitly).",
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
def run(
    diff_source,
    engineer: str,
    selector: str,
    layout: str,
) -> None:
    """Walk one Predictive Review session interactively, end-to-end."""
    diff_text = diff_source.read()
    if not diff_text.strip():
        raise click.UsageError("diff is empty")

    service = build_default_service()
    layout_enum = (
        ReconciliationLayout.INLINE_HUNK
        if layout == "inline"
        else ReconciliationLayout.NO_HUNK
    )

    click.echo("Submitting diff and selecting regions...")
    session_id = service.submit(
        diff_text=diff_text,
        engineer_identifier=engineer,
        selector_name=selector,
        layout=layout_enum,
    )
    regions = service.list_regions(session_id)
    click.echo(f"Session {session_id}")
    click.echo(f"Selected {len(regions)} regions:")
    for r in regions:
        click.echo(f"  {r.ordinal + 1}. {r.structural_label}")

    _run_hypothesis_phase(service, session_id, regions)

    click.echo("\nLocking hypotheses and generating readings (this calls the LLM)...")
    service.lock_and_reveal(session_id=session_id)

    for region in service.list_regions(session_id):
        _run_region_reconciliation(service, session_id, region, layout_enum)

    click.secho(f"\nSession {session_id} complete.", fg="green")


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


# --- reconciliation + dialogue ------------------------------------------


def _run_region_reconciliation(
    service: SessionService,
    session_id: str,
    region: RegionSnapshot,
    layout: ReconciliationLayout,
) -> None:
    click.echo("")
    click.secho("=" * 60, fg="cyan")
    click.secho(
        f"Region {region.ordinal + 1}: {region.structural_label}", fg="cyan", bold=True
    )
    click.secho("=" * 60, fg="cyan")

    reading = service.get_reading(region.id)
    hypothesis = service.get_locked_hypothesis(region.id)
    if reading is None or hypothesis is None:
        raise click.ClickException(
            f"region {region.id} missing reading or hypothesis; cannot reconcile"
        )

    click.echo("\nReading:")
    click.echo(reading)

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

    _prompt_disposition(service, session_id, region)


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
    click.echo("  :done      revised teach-back (opens editor, re-runs judge)")
    click.echo("  :disagree  close this region with explicit disagreement")
    click.echo("  :edit      open editor for a multi-line dialogue message")
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

        if msg == ":done":
            if _submit_revised_teach_back(service, session_id, region):
                return
        elif msg == ":disagree":
            service.close_with_disagreement(
                session_id=session_id, region_id=region.id
            )
            click.secho("Region closed with disagreement.", fg="yellow")
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
