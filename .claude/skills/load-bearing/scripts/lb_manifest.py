#!/usr/bin/env python3
"""Anchor, validate, and re-stamp a .load-bearing/ manifest.

Self-contained on purpose: this runs inside a target repo, where PREP is
not installed. Nothing here imports PREP, and nothing here needs to.

The one rule that must not drift is the anchor hash. PREP recomputes
`range_hash` from the working tree and compares; a producer that hashes
even slightly differently marks every member stale on the first run, and
the failure looks like the code moved rather than like two programs
disagreeing about newlines. `range_hash` below is that rule, stated once:

    sha256 over lines start..end inclusive, 1-based, after: stripping a
    leading UTF-8 BOM, folding CRLF and bare CR to LF, KEEPING trailing
    whitespace, and treating a missing final newline as invisible.
    Selected lines are joined with LF and no trailing terminator is
    appended, so a range ending at the last line hashes the same whether
    or not the file ends in a newline.

Usage
  lb_manifest.py slice   src/auth.py:14-32      show the anchored lines
  lb_manifest.py anchor  src/auth.py:14-32      emit an anchor object
  lb_manifest.py check    [--repo DIR]          validate + report staleness
  lb_manifest.py relocate [--repo DIR]          repair pure moves, in bulk
  lb_manifest.py attest   MEMBER_ID             re-stamp a real content change

A stale member has two possible causes and they are not the same failure.
The bytes MOVED, because something above them grew — nothing said about
that region stopped being true, and repairing it needs no judgement.
Or the bytes CHANGED, and the body may have gone stale with them.

Collapsing the two leaves one remedy, a rehash, which yields a manifest
fresh by hash and wrong by meaning — worse than an openly stale one,
because a stale member at least announces itself. So `relocate` handles
the first mechanically (an equal-length window elsewhere hashing to the
recorded value IS proof nothing changed) and `attest` handles the second
one member at a time, and refuses when the body has not been edited.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

CONTRACT_NAME = "load-bearing"
SUPPORTED_MAJOR = 0
MANIFEST_DIRNAME = ".load-bearing"
MANIFEST_FILENAME = "manifest.json"

STATE_FIELD_NAMES = frozenset(
    {
        "reviewed",
        "understood",
        "verified",
        "known",
        "mastered",
        "status",
        "confidence",
        "mastery",
    }
)

_TOP_FIELDS = frozenset(
    {"contract", "source_tree", "source_commit", "generated_by",
     "criteria", "default_criterion", "members"}
)
_TOP_REQUIRED = frozenset(
    {"contract", "source_tree", "criteria", "default_criterion", "members"}
)
_GENERATED_BY_FIELDS = frozenset({"agent", "model", "at"})
_CRITERION_FIELDS = frozenset({"id", "description", "composed_of", "weights"})
_MEMBER_FIELDS = frozenset(
    {"id", "body", "body_ref", "anchors", "scores", "rationale", "aspects", "metadata"}
)
_ANCHOR_FIELDS = frozenset({"path", "start", "end", "blob", "range_hash"})
_ANCHOR_REQUIRED = frozenset({"path", "start", "end", "range_hash"})
_ASPECT_FIELDS = frozenset({"id", "criteria", "claim"})


# --- the rule that must not drift -------------------------------------------


def read_lines(path: Path) -> list[bytes]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    lines = normalized.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]  # a trailing newline terminates a line, it is not one
    return lines


def range_hash(path: Path, start: int, end: int) -> str:
    lines = read_lines(path)
    return hashlib.sha256(b"\n".join(lines[start - 1 : end])).hexdigest()


def blob_sha(path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "hash-object", str(path)],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


# --- problems ---------------------------------------------------------------


class Problem(Exception):
    def __init__(self, message: str, path: str) -> None:
        self.path = path
        super().__init__(f"{path}: {message}")


def _mapping(value, path):
    if not isinstance(value, dict):
        raise Problem(f"expected an object, got {type(value).__name__}", path)
    return value


def _list(value, path):
    if not isinstance(value, list):
        raise Problem(f"expected a list, got {type(value).__name__}", path)
    return value


def _string(value, path):
    if not isinstance(value, str):
        raise Problem(f"expected a string, got {type(value).__name__}", path)
    return value


def _reject_unknown(node, allowed, path):
    unknown = sorted(set(node) - allowed)
    if unknown:
        raise Problem(
            f"unknown field(s) {', '.join(repr(u) for u in unknown)}; "
            f"allowed here: {', '.join(sorted(allowed))}",
            f"{path}.{unknown[0]}" if path else unknown[0],
        )


def _require(node, required, path):
    missing = sorted(required - set(node))
    if missing:
        raise Problem(
            f"missing required field(s): {', '.join(repr(m) for m in missing)}",
            f"{path}.{missing[0]}" if path else missing[0],
        )


def _reject_state_fields(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and key.lower() in STATE_FIELD_NAMES:
                raise Problem(
                    f"state-shaped field {key!r} is not allowed anywhere in a "
                    "manifest, including `metadata`; a manifest carries content, "
                    "never a judgement about it",
                    child,
                )
            _reject_state_fields(value, child)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _reject_state_fields(item, f"{path}[{i}]")


# --- validation -------------------------------------------------------------


def validate(data, manifest_dir: Path) -> tuple[dict, list]:
    root = _mapping(data, "")
    _reject_state_fields(root)
    _reject_unknown(root, _TOP_FIELDS, "")
    _require(root, _TOP_REQUIRED, "")

    raw = _string(root["contract"], "contract")
    name, _, version = raw.partition("/")
    if name != CONTRACT_NAME or not version:
        raise Problem(f"expected {CONTRACT_NAME}/<version>, got {raw!r}", "contract")
    try:
        major = int(version.partition(".")[0])
    except ValueError:
        raise Problem(f"unparseable contract version: {raw!r}", "contract") from None
    if major != SUPPORTED_MAJOR:
        raise Problem(
            f"unsupported contract major version {major} "
            f"(this producer writes {CONTRACT_NAME}/{SUPPORTED_MAJOR}.x)",
            "contract",
        )

    _string(root["source_tree"], "source_tree")
    if "source_commit" in root:
        _string(root["source_commit"], "source_commit")
    if "generated_by" in root:
        _reject_unknown(
            _mapping(root["generated_by"], "generated_by"),
            _GENERATED_BY_FIELDS, "generated_by",
        )

    criteria = {}
    raw_criteria = _list(root["criteria"], "criteria")
    if not raw_criteria:
        raise Problem("at least one criterion is required", "criteria")
    for i, entry in enumerate(raw_criteria):
        path = f"criteria[{i}]"
        node = _mapping(entry, path)
        _reject_unknown(node, _CRITERION_FIELDS, path)
        _require(node, frozenset({"id"}), path)
        cid = _string(node["id"], f"{path}.id")
        if cid in criteria:
            raise Problem(f"duplicate criterion id {cid!r}", f"{path}.id")
        composed = node.get("composed_of")
        if composed is not None:
            components = _list(composed, f"{path}.composed_of")
            if not components:
                raise Problem(
                    "a composite must list at least one component; omit "
                    "`composed_of` entirely for an atomic criterion",
                    f"{path}.composed_of",
                )
            composed = [
                _string(c, f"{path}.composed_of[{j}]") for j, c in enumerate(components)
            ]
        criteria[cid] = composed or []

    for i, entry in enumerate(raw_criteria):
        cid = entry["id"]
        for j, component in enumerate(criteria[cid]):
            if component not in criteria:
                raise Problem(
                    f"composite {cid!r} names undeclared component {component!r}",
                    f"criteria[{i}].composed_of[{j}]",
                )
            if component == cid:
                raise Problem(
                    f"composite {cid!r} lists itself as a component",
                    f"criteria[{i}].composed_of[{j}]",
                )

    default_criterion = _string(root["default_criterion"], "default_criterion")
    if default_criterion not in criteria:
        raise Problem(
            f"default_criterion {default_criterion!r} is not declared "
            f"(declared: {', '.join(sorted(criteria))})",
            "default_criterion",
        )

    members = _list(root["members"], "members")
    if not members:
        raise Problem("at least one member is required", "members")

    seen_ids = set()
    resolved = []
    for i, entry in enumerate(members):
        path = f"members[{i}]"
        node = _mapping(entry, path)
        _reject_unknown(node, _MEMBER_FIELDS, path)
        _require(node, frozenset({"id", "anchors"}), path)

        mid = _string(node["id"], f"{path}.id")
        if mid in seen_ids:
            raise Problem(f"duplicate member id {mid!r}", f"{path}.id")
        seen_ids.add(mid)

        has_body, has_ref = "body" in node, "body_ref" in node
        if has_body and has_ref:
            raise Problem(
                "exactly one of `body` / `body_ref` is allowed, not both",
                f"{path}.body_ref",
            )
        if not has_body and not has_ref:
            raise Problem(
                "exactly one of `body` / `body_ref` is required", f"{path}.body"
            )
        if has_ref:
            ref = _string(node["body_ref"], f"{path}.body_ref")
            if Path(ref).is_absolute():
                raise Problem(
                    f"body_ref must be relative to {MANIFEST_DIRNAME}/, got an "
                    "absolute path", f"{path}.body_ref",
                )
            target = (manifest_dir / ref).resolve()
            if not target.is_relative_to(manifest_dir.resolve()):
                raise Problem(
                    f"body_ref escapes {MANIFEST_DIRNAME}/: {ref!r}", f"{path}.body_ref"
                )
            if not target.is_file():
                raise Problem(f"body_ref target not found: {ref!r}", f"{path}.body_ref")
        else:
            _string(node["body"], f"{path}.body")

        anchors = _list(node["anchors"], f"{path}.anchors")
        if not anchors:
            raise Problem("at least one anchor is required", f"{path}.anchors")
        for j, item in enumerate(anchors):
            apath = f"{path}.anchors[{j}]"
            anode = _mapping(item, apath)
            _reject_unknown(anode, _ANCHOR_FIELDS, apath)
            _require(anode, _ANCHOR_REQUIRED, apath)
            for key in ("start", "end"):
                value = anode[key]
                if isinstance(value, bool) or not isinstance(value, int):
                    raise Problem(
                        f"{key} must be an integer line number, got "
                        f"{type(value).__name__}", f"{apath}.{key}",
                    )
            if anode["start"] < 1:
                raise Problem("start is 1-based; it must be >= 1", f"{apath}.start")
            if anode["end"] < anode["start"]:
                raise Problem(
                    f"end ({anode['end']}) precedes start ({anode['start']})",
                    f"{apath}.end",
                )
            _string(anode["path"], f"{apath}.path")
            _string(anode["range_hash"], f"{apath}.range_hash")

        scores = node.get("scores")
        if scores is not None:
            snode = _mapping(scores, f"{path}.scores")
            undeclared = sorted(set(snode) - set(criteria))
            if undeclared:
                raise Problem(
                    f"score for undeclared criterion {undeclared[0]!r}",
                    f"{path}.scores.{undeclared[0]}",
                )
            unscored = sorted(set(criteria) - set(snode))
            if unscored:
                raise Problem(
                    f"member scores some criteria but not "
                    f"{', '.join(repr(u) for u in unscored)}; a member that scores "
                    "anything must score every declared criterion",
                    f"{path}.scores.{unscored[0]}",
                )
            for cid, value in snode.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise Problem(
                        f"score must be a number, got {type(value).__name__}",
                        f"{path}.scores.{cid}",
                    )

        if node.get("rationale") is not None:
            _string(node["rationale"], f"{path}.rationale")

        aspect_ids = set()
        for j, item in enumerate(node.get("aspects") or []):
            apath = f"{path}.aspects[{j}]"
            anode = _mapping(item, apath)
            _reject_unknown(anode, _ASPECT_FIELDS, apath)
            _require(anode, _ASPECT_FIELDS, apath)
            aid = _string(anode["id"], f"{apath}.id")
            if aid in aspect_ids:
                raise Problem(f"duplicate aspect id {aid!r}", f"{apath}.id")
            aspect_ids.add(aid)
            _string(anode["claim"], f"{apath}.claim")
            for k, cid in enumerate(_list(anode["criteria"], f"{apath}.criteria")):
                name = _string(cid, f"{apath}.criteria[{k}]")
                if name not in criteria:
                    raise Problem(
                        f"aspect scoped to undeclared criterion {name!r}",
                        f"{apath}.criteria[{k}]",
                    )

        resolved.append((mid, anchors))

    return criteria, resolved


def staleness(resolved, repo_root: Path):
    fresh, stale = [], []
    for mid, anchors in resolved:
        ok = True
        for anchor in anchors:
            target = repo_root / anchor["path"]
            if not target.is_file():
                ok = False
                break
            if range_hash(target, anchor["start"], anchor["end"]) != anchor["range_hash"]:
                ok = False
                break
        (fresh if ok else stale).append(mid)
    return fresh, stale


# --- commands ---------------------------------------------------------------


def parse_target(spec: str) -> tuple[Path, int, int]:
    m = re.fullmatch(r"(.+):(\d+)-(\d+)", spec)
    if not m:
        raise SystemExit(f"expected PATH:START-END, got {spec!r}")
    return Path(m.group(1)), int(m.group(2)), int(m.group(3))


def cmd_slice(args) -> int:
    path, start, end = parse_target(args.target)
    lines = read_lines(path)
    width = len(str(end))
    for n in range(start, min(end, len(lines)) + 1):
        print(f"{n:>{width}}  {lines[n - 1].decode('utf-8', 'replace')}")
    return 0


def cmd_anchor(args) -> int:
    path, start, end = parse_target(args.target)
    total = len(read_lines(path))
    if end > total:
        raise SystemExit(f"{path} has {total} lines; anchor asks for {end}")
    anchor = {
        "path": str(path),
        "start": start,
        "end": end,
        "blob": blob_sha(path),
        "range_hash": range_hash(path, start, end),
    }
    if anchor["blob"] is None:
        del anchor["blob"]
    print(json.dumps(anchor, indent=2))
    return 0


def _load(repo_root: Path):
    manifest_dir = repo_root / MANIFEST_DIRNAME
    manifest_path = manifest_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise SystemExit(f"no manifest at {manifest_path}")
    try:
        return manifest_dir, manifest_path, json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"{manifest_path}: invalid JSON: {e}") from None


def cmd_check(args) -> int:
    repo_root = Path(args.repo)
    manifest_dir, _, data = _load(repo_root)
    try:
        criteria, resolved = validate(data, manifest_dir)
    except Problem as e:
        print(f"INVALID  {e}", file=sys.stderr)
        return 1

    fresh, stale = staleness(resolved, repo_root)
    print(f"valid    contract {data['contract']}")
    print(f"criteria {', '.join(criteria)} (default: {data['default_criterion']})")
    print(f"members  {len(resolved)} declared, {len(fresh)} fresh, {len(stale)} stale")
    for mid in stale:
        print(f"  stale  {mid}")
    if stale:
        print(
            "\nStale members are excluded from selection. Re-read the anchored "
            "slice before re-stamping — if the code moved, the body may no longer "
            "describe it."
        )
    return 0


def _find_moved_window(path: Path, recorded_hash: str, length: int):
    """Locate the anchored bytes elsewhere in the file, if they merely moved.

    A window of the same length hashing to the recorded value *is* proof
    that nothing about those lines changed — only their line numbers did.
    That is why a relocation needs no human judgement and a content change
    does. Returns the new (start, end), or None if the bytes are gone.
    """
    lines = read_lines(path)
    for start in range(1, len(lines) - length + 2):
        end = start + length - 1
        if range_hash(path, start, end) == recorded_hash:
            return start, end
    return None


def _body_edited(repo_root: Path, manifest_dir: Path, member: dict) -> bool:
    """Has this member's body changed against HEAD?

    Checked against committed content rather than against mtime, so an
    editor that touched and reverted a file does not read as an edit.
    Works for both body shapes: a `body_ref` file is diffed directly, and
    an inline `body` is pulled out of HEAD's manifest.
    """
    def _head(rel: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "show", f"HEAD:{rel}"],
                cwd=repo_root, capture_output=True, text=True, check=True,
            )
            return out.stdout
        except (OSError, subprocess.CalledProcessError):
            return None

    if "body_ref" in member:
        body_path = (manifest_dir / member["body_ref"]).resolve()
        rel = str(body_path.relative_to(repo_root.resolve()))
        committed = _head(rel)
        if committed is None:
            return True  # new file, never committed: an edit by definition
        return committed != body_path.read_text()

    committed_manifest = _head(f"{MANIFEST_DIRNAME}/{MANIFEST_FILENAME}")
    if committed_manifest is None:
        return True
    try:
        old = json.loads(committed_manifest)
    except json.JSONDecodeError:
        return True
    previous = next(
        (m for m in old.get("members", []) if m.get("id") == member["id"]), None
    )
    if previous is None:
        return True
    return previous.get("body") != member.get("body")


def cmd_relocate(args) -> int:
    """Repair members whose anchored bytes moved but did not change."""
    repo_root = Path(args.repo)
    manifest_dir, manifest_path, data = _load(repo_root)
    try:
        _, resolved = validate(data, manifest_dir)
    except Problem as e:
        print(f"INVALID  {e}", file=sys.stderr)
        return 1

    _, stale = staleness(resolved, repo_root)
    if not stale:
        print("nothing stale")
        return 0

    moved, changed = [], []
    for member in data["members"]:
        if member["id"] not in stale:
            continue
        member_moved = True
        for anchor in member["anchors"]:
            target = repo_root / anchor["path"]
            if not target.is_file():
                member_moved = False
                break
            if range_hash(target, anchor["start"], anchor["end"]) == anchor["range_hash"]:
                continue
            found = _find_moved_window(
                target, anchor["range_hash"], anchor["end"] - anchor["start"] + 1
            )
            if found is None:
                member_moved = False
                break
            anchor["start"], anchor["end"] = found
            new_blob = blob_sha(target)
            if new_blob:
                anchor["blob"] = new_blob
        (moved if member_moved else changed).append(member["id"])

    if moved:
        manifest_path.write_text(json.dumps(data, indent=2) + "\n")
        for mid in moved:
            print(f"relocated  {mid}")
        print(
            f"\n{len(moved)} member(s) relocated. The bytes are identical — only "
            "their line numbers moved, so no body needed re-reading."
        )
    if changed:
        for mid in changed:
            print(f"CHANGED    {mid} — bytes differ; needs `attest`", file=sys.stderr)
        print(
            f"\n{len(changed)} member(s) have changed content, not just position. "
            "Read the region and update the body, then run `attest <id>`.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_attest(args) -> int:
    """Re-stamp one member whose anchored content actually changed."""
    repo_root = Path(args.repo)
    manifest_dir, manifest_path, data = _load(repo_root)
    try:
        validate(data, manifest_dir)
    except Problem as e:
        print(f"INVALID  {e}", file=sys.stderr)
        return 1

    member = next((m for m in data["members"] if m["id"] == args.member_id), None)
    if member is None:
        raise SystemExit(f"no member with id {args.member_id!r}")

    pending = []
    for anchor in member["anchors"]:
        target = repo_root / anchor["path"]
        if not target.is_file():
            print(f"missing  {anchor['path']} — cannot attest", file=sys.stderr)
            return 1
        actual = range_hash(target, anchor["start"], anchor["end"])
        if actual != anchor["range_hash"]:
            pending.append((anchor, target, actual))

    if not pending:
        print(f"{args.member_id} is already fresh")
        return 0

    for anchor, target, _ in pending:
        print(f"\n--- {anchor['path']}:{anchor['start']}-{anchor['end']} is now:")
        lines = read_lines(target)
        for n in range(anchor["start"], min(anchor["end"], len(lines)) + 1):
            print(f"  {n}  {lines[n - 1].decode('utf-8', 'replace')}")

    # The refusal. A body that did not change cannot describe code that did.
    if not _body_edited(repo_root, manifest_dir, member) and not args.unchanged:
        print(
            f"\nREFUSED  {args.member_id}: the anchored code changed but this "
            "member's body is unchanged against HEAD.\n"
            "         Update the body to describe what is above, then attest "
            "again.\n"
            "         If the bytes changed but nothing the body says stopped "
            'being true\n         (a rename, a reformat), pass '
            '--unchanged "<reason>".',
            file=sys.stderr,
        )
        return 1

    for anchor, target, actual in pending:
        anchor["range_hash"] = actual
        new_blob = blob_sha(target)
        if new_blob:
            anchor["blob"] = new_blob

    manifest_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"\nattested  {args.member_id}")
    if args.unchanged:
        # For the commit message. Never the manifest: a fact about an edit is
        # not a fact about the software, and invariant 1 keeps it out.
        print(f"reason    {args.unchanged}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("slice", help="print the anchored lines")
    p.add_argument("target", metavar="PATH:START-END")
    p.set_defaults(fn=cmd_slice)

    p = sub.add_parser("anchor", help="emit an anchor object with computed hashes")
    p.add_argument("target", metavar="PATH:START-END")
    p.set_defaults(fn=cmd_anchor)

    p = sub.add_parser("check", help="validate the manifest and report staleness")
    p.add_argument("--repo", default=".")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser(
        "relocate", help="repair members whose anchored bytes moved but did not change"
    )
    p.add_argument("--repo", default=".")
    p.set_defaults(fn=cmd_relocate)

    p = sub.add_parser(
        "attest", help="re-stamp one member whose anchored content changed"
    )
    p.add_argument("member_id")
    p.add_argument(
        "--unchanged",
        metavar="REASON",
        help="the bytes changed but nothing the body says stopped being true "
             "(a rename, a reformat). Printed for the commit message; never "
             "written to the manifest.",
    )
    p.add_argument("--repo", default=".")
    p.set_defaults(fn=cmd_attest)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
