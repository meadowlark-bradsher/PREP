"""List recent commits in a local git repo for the launcher's commit browser.

The browser is the primary affordance on the launcher in v1.5+: an
engineer running prep serve from a repo gets a list of recent commits
they can click on, each with sha + author + date + subject + body
excerpt so they can decide which commit is worth a Predictive Review
session before they spend any LLM tokens on it.

This module shells out to `git log` with NUL-like control characters
as separators so commit message contents can't collide with the parser.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

# Control characters that are very unlikely to show up in commit text.
# RS = record separator (between commits), US = unit separator (between
# fields within a commit). Using these instead of newlines means commit
# bodies can contain whatever they want without breaking the parser.
_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"

_FORMAT = (
    f"%H{_FIELD_SEP}%h{_FIELD_SEP}%an{_FIELD_SEP}%aI"
    f"{_FIELD_SEP}%s{_FIELD_SEP}%b{_RECORD_SEP}"
)


@dataclass(frozen=True)
class CommitSummary:
    sha: str
    short_sha: str
    author: str
    author_date: str  # ISO 8601 author date
    subject: str
    body: str


def list_commits(repo_path: str = ".", limit: int = 30) -> list[CommitSummary]:
    """Run `git log` in repo_path and return the most recent `limit` commits.

    Raises RuntimeError on any git failure with a human-readable message
    the launcher route can render inline.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                repo_path,
                "log",
                "--format=format:" + _FORMAT,
                "-n",
                str(limit),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError("git executable not found on PATH") from e
    except subprocess.CalledProcessError as e:
        msg = e.stderr.strip() or e.stdout.strip() or "(no error output)"
        raise RuntimeError(msg) from e

    return _parse(result.stdout)


def _parse(raw: str) -> list[CommitSummary]:
    commits: list[CommitSummary] = []
    for chunk in raw.split(_RECORD_SEP):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split(_FIELD_SEP)
        if len(parts) < 6:
            continue
        sha, short, author, date, subject = parts[:5]
        body = parts[5].strip()
        commits.append(
            CommitSummary(
                sha=sha,
                short_sha=short,
                author=author,
                author_date=date,
                subject=subject,
                body=body,
            )
        )
    return commits
