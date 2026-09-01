"""The producer skill's hashing must agree with PREP's, exactly.

`.claude/skills/load-bearing/` ships a self-contained script, because a
producer runs inside a target repo where PREP is not installed. That
leaves two implementations of the same freshness rule, and a divergence
between them does not look like a bug: every member simply reads as
stale, as though the code had moved.

These tests are the join. If the two implementations ever disagree — on
CRLF, on trailing newlines, on whitespace, on any byte — this file fails
instead of the field.
"""

from __future__ import annotations

import importlib.util
import json
import random
import shutil
from pathlib import Path

import pytest

from predictive_review.content_sources import ManifestError, ManifestSource
from predictive_review.content_sources.manifest import range_hash as prep_range_hash

FIXTURE = Path(__file__).parent.parent / "fixtures" / "load_bearing_repo"
SCRIPT = (
    Path(__file__).parent.parent.parent
    / ".claude" / "skills" / "load-bearing" / "scripts" / "lb_manifest.py"
)


def _load_producer():
    spec = importlib.util.spec_from_file_location("lb_manifest", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


producer = _load_producer()


# --- the freshness rule, from both sides ------------------------------------


def test_producer_ships_with_the_skill() -> None:
    assert SCRIPT.is_file(), f"producer script missing at {SCRIPT}"


def test_agrees_on_every_fixture_anchor() -> None:
    manifest = json.loads((FIXTURE / ".load-bearing" / "manifest.json").read_text())
    checked = 0
    for member in manifest["members"]:
        for anchor in member["anchors"]:
            target = FIXTURE / anchor["path"]
            assert producer.range_hash(
                target, anchor["start"], anchor["end"]
            ) == prep_range_hash(target, anchor["start"], anchor["end"])
            checked += 1
    assert checked >= 4, "fixture should exercise several anchors"


@pytest.mark.parametrize(
    "content",
    [
        b"one\ntwo\nthree\n",           # trailing newline
        b"one\ntwo\nthree",             # none
        b"one\r\ntwo\r\nthree\r\n",     # CRLF
        b"one\r\ntwo\nthree\r\n",       # mixed
        b"  leading\ntrailing   \n",    # whitespace that must NOT be stripped
        b"\n\n\n",                      # blank lines only
        b"single",                      # one line, no terminator
        b"\xef\xbb\xbfBOM\nsecond\n",   # BOM, which must NOT be special-cased
        "unicode — em dash\nnext\n".encode(),
    ],
)
def test_agrees_on_edge_case_files(tmp_path: Path, content: bytes) -> None:
    f = tmp_path / "f.txt"
    f.write_bytes(content)
    line_count = max(1, len(producer.read_lines(f)))
    for start in range(1, line_count + 1):
        for end in range(start, line_count + 1):
            assert producer.range_hash(f, start, end) == prep_range_hash(f, start, end), (
                f"disagreement on {content!r} lines {start}-{end}"
            )


def test_agrees_across_a_randomised_sweep(tmp_path: Path) -> None:
    """Fixed seed: deterministic, but wide enough to catch an off-by-one."""
    rng = random.Random(20260831)
    alphabet = ["alpha", "  beta", "gamma  ", "", "\tdelta", "eéf"]

    for case in range(40):
        lines = [rng.choice(alphabet) for _ in range(rng.randint(1, 12))]
        text = ("\r\n" if case % 3 == 0 else "\n").join(lines)
        if case % 2 == 0:
            text += "\n"
        f = tmp_path / f"case{case}.txt"
        f.write_bytes(text.encode())

        n = max(1, len(producer.read_lines(f)))
        start = rng.randint(1, n)
        end = rng.randint(start, n)
        assert producer.range_hash(f, start, end) == prep_range_hash(f, start, end), (
            f"case {case}: disagreement on lines {start}-{end} of {text!r}"
        )


def test_crlf_and_lf_hash_identically_in_both(tmp_path: Path) -> None:
    body = "alpha\nbravo\ncharlie\n"
    lf, crlf = tmp_path / "lf.txt", tmp_path / "crlf.txt"
    lf.write_bytes(body.encode())
    crlf.write_bytes(body.replace("\n", "\r\n").encode())

    assert producer.range_hash(lf, 1, 3) == producer.range_hash(crlf, 1, 3)
    assert producer.range_hash(lf, 1, 3) == prep_range_hash(crlf, 1, 3)


# --- the two validators must accept and reject the same manifests -----------


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    return root


def _write(root: Path, data: dict) -> None:
    (root / ".load-bearing" / "manifest.json").write_text(json.dumps(data, indent=2))


def _load(root: Path) -> dict:
    return json.loads((root / ".load-bearing" / "manifest.json").read_text())


def _producer_validate(root: Path):
    return producer.validate(_load(root), root / ".load-bearing")


def test_both_accept_the_fixture() -> None:
    _producer_validate(FIXTURE)          # raises on failure
    ManifestSource(FIXTURE)              # likewise


def test_both_agree_on_the_stale_member() -> None:
    _, resolved = _producer_validate(FIXTURE)
    _, stale = producer.staleness(resolved, FIXTURE)

    assert stale == ["cache/eviction"]
    assert list(ManifestSource(FIXTURE).stale_member_ids) == stale


@pytest.mark.parametrize(
    "mutate,label",
    [
        (lambda d: d.update({"reviewed": True}), "state field at top level"),
        (lambda d: d["members"][0].update({"understood": 1}), "state field on a member"),
        (lambda d: d["members"][0]["aspects"][0].update({"verified": 1}), "state field in an aspect"),
        (lambda d: d.update({"extra": 1}), "unknown top-level field"),
        (lambda d: d["members"][0].update({"priority": 3}), "unknown member field"),
        (lambda d: d["members"][0].update({"body": "also inline"}), "body and body_ref"),
        (lambda d: d["members"][0].update({"body_ref": "../../../etc/passwd"}), "body_ref escape"),
        (lambda d: d.update({"contract": "load-bearing/1.0"}), "major version mismatch"),
        (lambda d: d["members"][0]["scores"].pop("churn-90d"), "partial scores"),
        (lambda d: d["criteria"][2].update({"composed_of": ["correctness", "nope"]}), "undeclared component"),
        (lambda d: d.update({"default_criterion": "nope"}), "undeclared default"),
        (lambda d: d.update({"members": []}), "no members"),
    ],
)
def test_both_reject_the_same_mutations(tmp_path: Path, mutate, label: str) -> None:
    root = _copy(tmp_path)
    data = _load(root)
    mutate(data)
    _write(root, data)

    with pytest.raises(producer.Problem) as producer_error:
        _producer_validate(root)
    with pytest.raises(ManifestError) as prep_error:
        ManifestSource(root)

    assert producer_error.value.path == prep_error.value.path, (
        f"{label}: producer blamed {producer_error.value.path!r}, "
        f"PREP blamed {prep_error.value.path!r}"
    )


def test_both_allow_state_shaped_keys_inside_metadata(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    data = _load(root)
    data["members"][0]["metadata"] = {"status": "whatever"}
    _write(root, data)

    _producer_validate(root)
    assert ManifestSource(root).produce()


# --- an anchor the script emits is one PREP reads as fresh ------------------


def test_script_emitted_anchor_is_fresh_to_prep(tmp_path: Path) -> None:
    """The end of the loop: what the producer stamps, PREP accepts."""
    root = tmp_path / "repo"
    (root / ".load-bearing").mkdir(parents=True)
    (root / "src").mkdir()
    source = root / "src" / "thing.py"
    source.write_bytes(b"def a():\n    return 1\n\n\ndef b():\n    return 2\n")

    anchor = {
        "path": "src/thing.py",
        "start": 1,
        "end": 2,
        "range_hash": producer.range_hash(source, 1, 2),
    }
    manifest = {
        "contract": "load-bearing/0.1",
        "source_tree": "0" * 40,
        "criteria": [{"id": "correctness"}],
        "default_criterion": "correctness",
        "members": [
            {
                "id": "thing/a",
                "body": "`a` returns 1 and takes no arguments.",
                "anchors": [anchor],
                "scores": {"correctness": 0.7},
                "aspects": [
                    {"id": "returns-one", "criteria": [], "claim": "a returns 1"}
                ],
            }
        ],
    }
    (root / ".load-bearing" / "manifest.json").write_text(json.dumps(manifest))

    source_obj = ManifestSource(root)
    assert source_obj.stale_member_ids == ()
    assert [c.metadata["member_id"] for c in source_obj.produce()] == ["thing/a"]
