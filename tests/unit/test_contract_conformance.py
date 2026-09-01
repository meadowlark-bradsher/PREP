"""The contract text and the implementation must not drift apart.

Every incompatibility this format has produced came from the same cause: a
rule that lived somewhere its reader could not see. `.load-bearing/CONTRACT.md`
is the answer to that, and it is only an answer for as long as it describes
what the code actually does.

So these tests read the contract and check the code against it, rather than
the other way round. When one fails, decide which is wrong before changing
either — a contract edited to match a regression is worse than no contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from predictive_review.content_sources.manifest import (
    CONTRACT_NAME,
    STATE_FIELD_NAMES,
    SUPPORTED_MAJOR,
    range_hash,
)

CONTRACT = Path(__file__).parent.parent.parent / ".load-bearing" / "CONTRACT.md"


@pytest.fixture(scope="module")
def text() -> str:
    assert CONTRACT.is_file(), f"contract missing at {CONTRACT}"
    return CONTRACT.read_text()


# --- the reserved-name list lives in the contract, not in a validator -------


def test_reserved_names_match_the_implementation(text: str) -> None:
    sentence = re.search(r"The reserved names are (.+?) — matched", text, re.S)
    assert sentence, "contract no longer states the reserved names"
    declared = set(re.findall(r"`([a-z]+)`", sentence.group(1)))

    assert declared == set(STATE_FIELD_NAMES), (
        f"contract says {sorted(declared)}, code enforces "
        f"{sorted(STATE_FIELD_NAMES)}. Decide which is wrong before editing "
        "either."
    )


def test_contract_declares_the_version_the_code_speaks(text: str) -> None:
    version = re.search(r"\*\*Version `([a-z-]+)/(\d+)\.(\d+)`", text)
    assert version, "contract no longer declares its own version"
    name, major, _minor = version.groups()

    assert name == CONTRACT_NAME
    assert int(major) == SUPPORTED_MAJOR


# --- the consequences the contract promises must actually hold --------------


def test_trailing_newline_is_invisible(tmp_path: Path) -> None:
    """'A range ending at the last line hashes identically whether or not the
    file ends in a newline.'"""
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"one\ntwo\n")
    b.write_bytes(b"one\ntwo")
    assert range_hash(a, 1, 2) == range_hash(b, 1, 2)


def test_all_three_line_endings_agree(tmp_path: Path) -> None:
    """'CRLF, CR and LF checkouts of the same content hash identically.'"""
    lf, crlf, cr = tmp_path / "lf", tmp_path / "crlf", tmp_path / "cr"
    lf.write_bytes(b"alpha\nbravo\ncharlie\n")
    crlf.write_bytes(b"alpha\r\nbravo\r\ncharlie\r\n")
    cr.write_bytes(b"alpha\rbravo\rcharlie\r")

    assert range_hash(lf, 1, 3) == range_hash(crlf, 1, 3) == range_hash(cr, 1, 3)


def test_bom_is_stripped(tmp_path: Path) -> None:
    """'An editor adding a BOM has not changed the code.'"""
    plain, bom = tmp_path / "plain", tmp_path / "bom"
    plain.write_bytes(b"alpha\nbravo\n")
    bom.write_bytes(b"\xef\xbb\xbfalpha\nbravo\n")
    assert range_hash(plain, 1, 2) == range_hash(bom, 1, 2)


def test_trailing_whitespace_is_preserved(tmp_path: Path) -> None:
    """'Trailing whitespace is preserved — it is a real edit to a real byte.'"""
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"value = 1\n")
    b.write_bytes(b"value = 1   \n")
    assert range_hash(a, 1, 1) != range_hash(b, 1, 1)


def test_edit_outside_the_range_leaves_the_member_fresh(tmp_path: Path) -> None:
    """'A file edited outside a member's anchored range leaves that member
    fresh. This is the point of hashing the range rather than the file.'"""
    f = tmp_path / "f"
    f.write_bytes(b"one\ntwo\nthree\n")
    before = range_hash(f, 1, 2)
    f.write_bytes(b"one\ntwo\nthree\nfour\nfive\n")
    assert range_hash(f, 1, 2) == before


def test_overlapping_ranges_hash_independently(tmp_path: Path) -> None:
    """'Overlapping ranges are legal.'"""
    f = tmp_path / "f"
    f.write_bytes(b"one\ntwo\nthree\nfour\n")
    assert range_hash(f, 1, 3) != range_hash(f, 2, 4)
    assert range_hash(f, 1, 3) == range_hash(f, 1, 3)


# --- invariants the contract asserts about PREP itself ----------------------


def test_weights_are_never_applied(text: str) -> None:
    """Invariant 2, and the contract says it in bold. Nothing may read it."""
    assert "**PREP never applies them.**" in text

    import predictive_review.selectors.manifest as selector

    source = Path(selector.__file__).read_text()
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    body = code.split('"""', 2)[-1]  # drop the module docstring
    assert "weights" not in body


def test_contract_states_the_composite_expansion(text: str) -> None:
    """Invariant 7's transitive clause is the fix for a real defect; if the
    sentence goes, the reason goes with it."""
    assert "transitively" in text
    assert "must never surface fewer aspects than a component" in text
