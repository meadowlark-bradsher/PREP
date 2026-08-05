"""Tests for the git log parser used by the launcher's commit browser."""

from __future__ import annotations

import subprocess

import pytest

from predictive_review.web.git_log import (
    CommitSummary,
    _parse,
    list_commits,
)


def test_parse_single_commit_with_body():
    raw = (
        "abc123\x1fabc\x1fAlice\x1f2026-05-01T12:00:00-07:00\x1f"
        "Subject one\x1fA longer body\nwith newlines\x1e"
    )
    commits = _parse(raw)
    assert len(commits) == 1
    c = commits[0]
    assert c.sha == "abc123"
    assert c.short_sha == "abc"
    assert c.author == "Alice"
    assert c.author_date == "2026-05-01T12:00:00-07:00"
    assert c.subject == "Subject one"
    assert c.body == "A longer body\nwith newlines"


def test_parse_multiple_commits_separated_by_record_sep():
    raw = (
        "aaa\x1faaa\x1fA\x1f2026-05-02\x1fFirst\x1fBody A\x1e"
        "bbb\x1fbbb\x1fB\x1f2026-05-01\x1fSecond\x1f\x1e"
    )
    commits = _parse(raw)
    assert [c.short_sha for c in commits] == ["aaa", "bbb"]
    assert commits[0].body == "Body A"
    assert commits[1].body == ""  # empty body trimmed to empty string


def test_parse_drops_malformed_chunks():
    raw = "abc\x1fabc\x1e"  # only 2 fields, malformed
    assert _parse(raw) == []


def test_list_commits_against_a_real_temp_repo(tmp_path):
    repo = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "First commit\n\nBody text here"],
        cwd=repo,
        check=True,
    )
    (repo / "a.txt").write_text("hello\nworld\n")
    subprocess.run(["git", "commit", "-q", "-am", "Second commit"], cwd=repo, check=True)

    commits = list_commits(repo_path=str(repo))
    assert len(commits) == 2
    assert commits[0].subject == "Second commit"
    assert commits[1].subject == "First commit"
    assert "Body text here" in commits[1].body


def test_list_commits_raises_on_bad_path(tmp_path):
    not_a_repo = tmp_path / "nope"
    not_a_repo.mkdir()
    with pytest.raises(RuntimeError):
        list_commits(repo_path=str(not_a_repo))
