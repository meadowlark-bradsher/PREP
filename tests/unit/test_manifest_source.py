"""ManifestSource: contract validation, body resolution, and freshness.

Every test runs against the hand-authored fixture under
`tests/fixtures/load_bearing_repo/` (there is no producer skill yet), or
against a tmp-dir copy of it that the test doctors first. No LLM is
involved anywhere in this file.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from predictive_review.content_sources import ContentSource, ManifestError, ManifestSource
from predictive_review.content_sources.manifest import range_hash

FIXTURE = Path(__file__).parent.parent / "fixtures" / "load_bearing_repo"


# --- helpers ---------------------------------------------------------------


def _copy_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    return root


def _manifest_path(root: Path) -> Path:
    return root / ".load-bearing" / "manifest.json"


def _load(root: Path) -> dict:
    return json.loads(_manifest_path(root).read_text())


def _write(root: Path, data: dict) -> None:
    _manifest_path(root).write_text(json.dumps(data, indent=2))


def _member(data: dict, member_id: str) -> dict:
    return next(m for m in data["members"] if m["id"] == member_id)


# --- the happy path --------------------------------------------------------


def test_valid_manifest_round_trips() -> None:
    source = ManifestSource(FIXTURE)

    assert isinstance(source, ContentSource)
    assert source.default_criterion == "identity"
    assert set(source.criterion_ids) == {"correctness", "churn-90d", "identity"}

    contents = source.produce()
    assert {c.kind for c in contents} == {"member"}
    assert [c.metadata["member_id"] for c in contents] == [
        "auth/token-refresh",
        "cache/lru-read",
        "parser/tokenize",
    ]


def test_member_id_and_manifest_fields_land_in_metadata() -> None:
    content = next(
        c for c in ManifestSource(FIXTURE).produce()
        if c.metadata["member_id"] == "auth/token-refresh"
    )
    assert content.metadata["scores"]["correctness"] == 0.9
    assert content.metadata["anchors"][0]["path"] == "src/auth.py"
    assert [a["id"] for a in content.metadata["aspects"]] == [
        "retry-bound",
        "churn-hotspot",
        "failure-modes-distinct",
    ]
    assert "two reverts" in content.metadata["rationale"].lower()


def test_body_ref_is_resolved_and_body_is_inlined() -> None:
    by_id = {c.metadata["member_id"]: c for c in ManifestSource(FIXTURE).produce()}

    # body_ref -> file contents
    assert by_id["auth/token-refresh"].body.startswith("# Token refresh")
    assert by_id["cache/lru-read"].body.startswith("# LRU read path")
    # inline body
    assert by_id["parser/tokenize"].body.startswith("# Tokenizer")


def test_member_without_scores_is_still_produced() -> None:
    """P7: unscored members are eligible under PREP's own selectors."""
    by_id = {c.metadata["member_id"]: c for c in ManifestSource(FIXTURE).produce()}
    assert by_id["parser/tokenize"].metadata["scores"] is None


# --- invariant 1 / P4: state never rides in on the transport ---------------


@pytest.mark.parametrize(
    "field", ["reviewed", "understood", "verified", "known", "mastered", "status"]
)
def test_state_field_rejected_at_top_level(tmp_path: Path, field: str) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    data[field] = True
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == field
    assert "state-shaped" in str(e.value)


def test_state_field_rejected_at_member_level(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    _member(data, "auth/token-refresh")["reviewed"] = True
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "members[0].reviewed"


def test_state_field_rejected_inside_aspects(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    _member(data, "auth/token-refresh")["aspects"][1]["verified"] = True
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "members[0].aspects[1].verified"


def test_state_field_rejection_is_case_insensitive(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    _member(data, "auth/token-refresh")["Understood"] = "yes"
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "members[0].Understood"


def test_state_shaped_key_is_allowed_inside_metadata(tmp_path: Path) -> None:
    """`metadata` is the one opaque bag; the ban is on the transport, not on
    whatever a repo wants to keep for itself."""
    root = _copy_fixture(tmp_path)
    data = _load(root)
    _member(data, "auth/token-refresh")["metadata"] = {"status": "whatever"}
    _write(root, data)

    contents = ManifestSource(root).produce()
    assert contents[0].metadata["metadata"] == {"status": "whatever"}


# --- P3: closed schema -----------------------------------------------------


def test_unknown_top_level_field_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    data["extra_thing"] = 1
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "extra_thing"


def test_unknown_member_field_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    _member(data, "cache/lru-read")["priority"] = 3
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "members[2].priority"


def test_unknown_aspect_field_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    _member(data, "auth/token-refresh")["aspects"][0]["weight"] = 0.5
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "members[0].aspects[0].weight"


# --- P6: body / body_ref ---------------------------------------------------


def test_body_and_body_ref_both_present_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    _member(data, "auth/token-refresh")["body"] = "inline as well"
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "members[0].body_ref"
    assert "not both" in str(e.value)


def test_neither_body_nor_body_ref_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    del _member(data, "auth/token-refresh")["body_ref"]
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "members[0].body"


@pytest.mark.parametrize(
    "escape",
    [
        "../../../etc/passwd",
        "../src/auth.py",
        "members/../../src/cache.py",
    ],
)
def test_body_ref_escaping_the_directory_rejected(tmp_path: Path, escape: str) -> None:
    """Invariant 8: a body_ref is a lookup inside `.load-bearing/`, nothing more."""
    root = _copy_fixture(tmp_path)
    data = _load(root)
    _member(data, "auth/token-refresh")["body_ref"] = escape
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "members[0].body_ref"
    assert "escapes" in str(e.value)


def test_absolute_body_ref_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    _member(data, "auth/token-refresh")["body_ref"] = "/etc/passwd"
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert "absolute" in str(e.value)


def test_missing_body_ref_target_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    _member(data, "auth/token-refresh")["body_ref"] = "members/nope.md"
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert "not found" in str(e.value)


# --- contract versioning ---------------------------------------------------


def test_unknown_major_version_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    data["contract"] = "load-bearing/1.0"
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "contract"
    assert "major version" in str(e.value)


def test_unknown_minor_version_is_tolerated(tmp_path: Path) -> None:
    """Minors are additive; majors are not."""
    root = _copy_fixture(tmp_path)
    data = _load(root)
    data["contract"] = "load-bearing/0.9"
    _write(root, data)

    assert ManifestSource(root).produce()


def test_foreign_contract_id_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    data["contract"] = "something-else/0.1"
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "contract"


# --- P7: scores ------------------------------------------------------------


def test_composite_missing_a_component_score_rejected(tmp_path: Path) -> None:
    """Invariant 3: a composite ordering must be overridable by its parts,
    which requires every member scored on the composite to be scored on
    each component too."""
    root = _copy_fixture(tmp_path)
    data = _load(root)
    del _member(data, "auth/token-refresh")["scores"]["churn-90d"]
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "members[0].scores.churn-90d"
    assert "every declared criterion" in str(e.value)


def test_score_for_undeclared_criterion_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    _member(data, "auth/token-refresh")["scores"]["velocity"] = 0.1
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "members[0].scores.velocity"


def test_composite_naming_undeclared_component_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    data["criteria"][2]["composed_of"] = ["correctness", "not-a-criterion"]
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "criteria[2].composed_of[1]"


def test_default_criterion_must_be_declared(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    data["default_criterion"] = "nonexistent"
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "default_criterion"


# --- invariant 6: content-addressed, per-member freshness ------------------


def test_stale_member_is_excluded_and_reported() -> None:
    source = ManifestSource(FIXTURE)
    assert source.stale_member_ids == ("cache/eviction",)
    assert "cache/eviction" not in {
        c.metadata["member_id"] for c in source.produce()
    }


def test_editing_the_anchored_slice_makes_a_member_stale(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    target = root / "src" / "auth.py"
    lines = target.read_text().split("\n")
    # line 30 (1-based) is `attempt += 1`, inside the anchored 14-32 range
    assert lines[29].strip() == "attempt += 1"
    lines[29] = "            attempt += 2"
    target.write_text("\n".join(lines))

    source = ManifestSource(root)
    assert "auth/token-refresh" in source.stale_member_ids
    assert "auth/token-refresh" not in {
        c.metadata["member_id"] for c in source.produce()
    }


def test_editing_outside_the_anchored_slice_leaves_a_member_fresh(tmp_path: Path) -> None:
    """The whole point of range_hash over blob.

    The file's blob changes; the anchored slice does not; the member
    stays eligible.
    """
    root = _copy_fixture(tmp_path)
    target = root / "src" / "auth.py"
    # anchored range is 14-32; append well past it
    target.write_text(target.read_text() + "\n\nclass LaterAddition(Exception):\n    pass\n")

    source = ManifestSource(root)
    assert "auth/token-refresh" not in source.stale_member_ids
    assert "auth/token-refresh" in {c.metadata["member_id"] for c in source.produce()}


def test_blob_mismatch_alone_does_not_make_a_member_stale() -> None:
    """The fixture records a deliberately stale `blob` for parser/tokenize
    while its range_hash still matches. blob is a short-circuit, never
    authoritative on its own (P2)."""
    source = ManifestSource(FIXTURE)
    assert "parser/tokenize" not in source.stale_member_ids

    manifest = json.loads((FIXTURE / ".load-bearing" / "manifest.json").read_text())
    recorded = _member(manifest, "parser/tokenize")["anchors"][0]["blob"]
    import subprocess

    actual = subprocess.run(
        ["git", "hash-object", str(FIXTURE / "src" / "parser.py")],
        capture_output=True, text=True,
    ).stdout.strip()
    assert recorded != actual, "fixture no longer exercises the blob-mismatch case"


def test_freshness_is_per_member_not_per_file() -> None:
    """cache/eviction and cache/lru-read anchor the same file at different
    ranges; one being stale must not take the other down."""
    source = ManifestSource(FIXTURE)
    assert source.stale_member_ids == ("cache/eviction",)
    assert "cache/lru-read" in {c.metadata["member_id"] for c in source.produce()}


def test_missing_anchor_file_makes_one_member_stale_not_a_global_refusal(
    tmp_path: Path,
) -> None:
    root = _copy_fixture(tmp_path)
    (root / "src" / "parser.py").unlink()

    source = ManifestSource(root)
    assert "parser/tokenize" in source.stale_member_ids
    assert source.produce(), "the rest of the manifest must still be usable"


def test_anchor_escaping_the_repo_root_makes_a_member_stale(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    _member(data, "parser/tokenize")["anchors"][0]["path"] = "../../../etc/hosts"
    _write(root, data)

    source = ManifestSource(root)
    assert "parser/tokenize" in source.stale_member_ids


# --- P2: the hash itself ---------------------------------------------------


def test_crlf_file_hashes_equal_to_lf_file(tmp_path: Path) -> None:
    body = "alpha\nbravo\ncharlie\ndelta\n"
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    lf.write_bytes(body.encode())
    crlf.write_bytes(body.replace("\n", "\r\n").encode())

    assert range_hash(lf, 1, 4) == range_hash(crlf, 1, 4)
    assert range_hash(lf, 2, 3) == range_hash(crlf, 2, 3)


def test_range_hash_covers_only_the_named_lines(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_bytes(b"one\ntwo\nthree\nfour\n")
    g = tmp_path / "g.txt"
    g.write_bytes(b"one\ntwo\nthree\nCHANGED\n")

    assert range_hash(f, 1, 3) == range_hash(g, 1, 3)
    assert range_hash(f, 1, 4) != range_hash(g, 1, 4)


def test_range_hash_does_not_strip_trailing_whitespace(tmp_path: Path) -> None:
    """P2 pins LF normalisation and nothing else."""
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"value = 1\n")
    b.write_bytes(b"value = 1   \n")
    assert range_hash(a, 1, 1) != range_hash(b, 1, 1)


def test_range_hash_is_insensitive_to_a_trailing_newline(tmp_path: Path) -> None:
    """Pins the terminator choice P2 leaves open: selected lines are joined
    with LF and no trailing terminator is appended."""
    with_nl = tmp_path / "with.txt"
    without_nl = tmp_path / "without.txt"
    with_nl.write_bytes(b"one\ntwo\n")
    without_nl.write_bytes(b"one\ntwo")
    assert range_hash(with_nl, 1, 2) == range_hash(without_nl, 1, 2)


# --- invariant 5: agent commentary must not reach the LLM components ------


def test_rationale_does_not_reach_the_reading_generator() -> None:
    """`rationale` is selector-visible only.

    It rides in `RegionContent.metadata`, which travels on the same object
    the reading generator receives, so the guarantee currently rests on
    `reading.py` reading `.body` and nothing else. Pin it, so a future
    change to that module fails here rather than silently leaking the
    agent's reasoning into the reading the engineer is tested against.
    """
    from predictive_review.domain.region import Region
    from predictive_review.reading import ReadingGenerator
    from tests.fakes import CapturingLLMClient

    content = next(
        c for c in ManifestSource(FIXTURE).produce()
        if c.metadata["member_id"] == "auth/token-refresh"
    )
    rationale = content.metadata["rationale"]
    assert rationale, "fixture must carry a rationale for this test to mean anything"

    llm = CapturingLLMClient(response_text="a reading")
    gen = ReadingGenerator(llm=llm, model="m", prompt_template="P")
    gen.generate(
        Region(structural_label="auth/token-refresh", content=content, selector_rationale={})
    )

    prompt = llm.calls[0]["system"] + llm.calls[0]["messages"][0].content
    assert rationale not in prompt
    assert "two reverts" not in prompt.lower()


def test_judge_cannot_receive_a_region_at_all() -> None:
    """The judge's signature is the guarantee: it takes text, not a Region,
    so no metadata of any kind has a traversal path to it."""
    import inspect

    from predictive_review.judge import ClosureJudge

    params = set(inspect.signature(ClosureJudge.judge).parameters)
    assert "region" not in params
    assert "content" not in params


# --- unreadable manifests --------------------------------------------------


def test_missing_manifest_reports_the_expected_location(tmp_path: Path) -> None:
    with pytest.raises(ManifestError) as e:
        ManifestSource(tmp_path)
    assert ".load-bearing/manifest.json" in str(e.value)


def test_malformed_json_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    _manifest_path(root).write_text("{ not json")

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert "invalid JSON" in str(e.value)


def test_empty_members_rejected(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    data = _load(root)
    data["members"] = []
    _write(root, data)

    with pytest.raises(ManifestError) as e:
        ManifestSource(root)
    assert e.value.path == "members"
