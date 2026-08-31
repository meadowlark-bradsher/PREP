"""`.load-bearing/` manifest content source.

Reads a target repo's `.load-bearing/manifest.json`, validates it against
the contract, and emits one `RegionContent` of kind `"member"` per fresh
member. The manifest is written by agents working inside a repo PREP does
not control, so it is treated as untrusted data throughout: validated
against a closed schema, never executed, and never followed as
instruction (contract invariant 8).

WHAT THIS MODULE REFUSES TO DO
==============================

  - **No state.** Fields that assert a user has reviewed/understood/
    verified something are rejected outright rather than ignored
    (invariant 1). A manifest is content; the ledger owns state.

  - **No synthesis.** Scores are carried through verbatim into metadata
    for a selector to order by. This module never computes a score,
    blends criteria, or applies `weights` (invariant 2).

  - **No silent repair.** A manifest that violates the schema raises with
    the JSON path of the offending field. There is no lenient mode.

  - **No global refusal on staleness.** A member whose anchored slice has
    moved is excluded from the emitted content and reported by id;
    the rest of the manifest is still usable (invariant 6).

FRESHNESS
=========

Per-member and content-addressed. `range_hash` is recomputed from the
working tree and compared; `blob` is only ever a cheap short-circuit and
is never authoritative on its own, so a file edited *outside* a member's
anchored slice leaves that member fresh. Commit SHAs are never consulted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..domain.content import RegionContent

MANIFEST_DIRNAME = ".load-bearing"
MANIFEST_FILENAME = "manifest.json"

CONTRACT_NAME = "load-bearing"
SUPPORTED_MAJOR = 0

CONTENT_KIND = "member"

# P4. Rejected by name at any depth outside `metadata`, case-insensitively.
# These name a *judgement about* a member rather than the member itself,
# and the transport is not allowed to carry judgements.
STATE_FIELD_NAMES = frozenset(
    {"reviewed", "understood", "verified", "known", "mastered", "status"}
)

# P3. `metadata` is the only opaque bag; every other object is closed.
_TOP_FIELDS = frozenset(
    {
        "contract",
        "source_tree",
        "source_commit",
        "generated_by",
        "criteria",
        "default_criterion",
        "members",
    }
)
_TOP_REQUIRED = frozenset({"contract", "source_tree", "criteria", "default_criterion", "members"})
_GENERATED_BY_FIELDS = frozenset({"agent", "model", "at"})
_CRITERION_FIELDS = frozenset({"id", "description", "composed_of", "weights"})
_MEMBER_FIELDS = frozenset(
    {"id", "body", "body_ref", "anchors", "scores", "rationale", "aspects", "metadata"}
)
_ANCHOR_FIELDS = frozenset({"path", "start", "end", "blob", "range_hash"})
_ANCHOR_REQUIRED = frozenset({"path", "start", "end", "range_hash"})
_ASPECT_FIELDS = frozenset({"id", "criteria", "claim"})


class ManifestError(ValueError):
    """A manifest violated the contract.

    Carries the JSON path of the offending field so the repo owner can
    find it without diffing the whole file.
    """

    def __init__(self, message: str, path: str) -> None:
        self.path = path
        self.reason = message
        super().__init__(f"{path}: {message}")


@dataclass(frozen=True)
class Criterion:
    id: str
    description: str | None = None
    composed_of: tuple[str, ...] = ()

    @property
    def is_composite(self) -> bool:
        return bool(self.composed_of)


def range_hash(path: Path, start: int, end: int) -> str:
    """P2: sha256 over lines `start..end` inclusive, 1-based, CRLF->LF.

    No trailing-whitespace stripping and no BOM handling — a manifest and
    PREP must agree byte-for-byte, so every extra normalisation rule is a
    chance to disagree.

    Selected lines are joined with LF and no trailing terminator is
    appended, so a range ending at the last line hashes the same whether
    or not the file ends in a newline. P2 does not pin this; see the
    module's tests for the pinned behaviour.
    """
    normalized = path.read_bytes().replace(b"\r\n", b"\n")
    lines = normalized.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]  # a trailing newline terminates a line, it is not one
    return hashlib.sha256(b"\n".join(lines[start - 1 : end])).hexdigest()


# --- validation -------------------------------------------------------------


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestError(f"expected an object, got {type(value).__name__}", path)
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(f"expected a list, got {type(value).__name__}", path)
    return value


def _require_str(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"expected a string, got {type(value).__name__}", path)
    return value


def _reject_state_fields(node: Any, path: str) -> None:
    """P4. Walk everything outside `metadata` looking for state-shaped names.

    Recursive rather than field-list-driven: a state field smuggled three
    levels down is the same violation as one at the top, and the closed
    field lists alone would not catch it inside a value that is allowed
    to be a free-form object.
    """
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and key.lower() in STATE_FIELD_NAMES:
                raise ManifestError(
                    f"state-shaped field {key!r} is not allowed outside `metadata`; "
                    "a manifest carries content, never a judgement about it",
                    child,
                )
            if key == "metadata":
                continue  # the one opaque bag
            _reject_state_fields(value, child)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _reject_state_fields(item, f"{path}[{i}]")


def _reject_unknown_fields(node: Mapping[str, Any], allowed: frozenset[str], path: str) -> None:
    unknown = sorted(set(node) - allowed)
    if unknown:
        raise ManifestError(
            f"unknown field(s) {', '.join(repr(u) for u in unknown)}; "
            f"allowed here: {', '.join(sorted(allowed))}",
            f"{path}.{unknown[0]}" if path else unknown[0],
        )


def _require_present(node: Mapping[str, Any], required: frozenset[str], path: str) -> None:
    missing = sorted(required - set(node))
    if missing:
        raise ManifestError(
            f"missing required field(s): {', '.join(repr(m) for m in missing)}",
            f"{path}.{missing[0]}" if path else missing[0],
        )


def _validate_contract_version(raw: str, path: str) -> None:
    """Reject unknown major versions; tolerate unknown minors."""
    name, _, version = raw.partition("/")
    if name != CONTRACT_NAME or not version:
        raise ManifestError(
            f"expected a {CONTRACT_NAME}/<version> contract id, got {raw!r}", path
        )
    major_text = version.partition(".")[0]
    try:
        major = int(major_text)
    except ValueError:
        raise ManifestError(f"unparseable contract version: {raw!r}", path) from None
    if major != SUPPORTED_MAJOR:
        raise ManifestError(
            f"unsupported contract major version {major} "
            f"(this PREP speaks {CONTRACT_NAME}/{SUPPORTED_MAJOR}.x)",
            path,
        )


def _validate_criteria(raw_criteria: list[Any]) -> dict[str, Criterion]:
    if not raw_criteria:
        raise ManifestError("at least one criterion is required", "criteria")

    criteria: dict[str, Criterion] = {}
    for i, raw in enumerate(raw_criteria):
        path = f"criteria[{i}]"
        node = _require_mapping(raw, path)
        _reject_unknown_fields(node, _CRITERION_FIELDS, path)
        _require_present(node, frozenset({"id"}), path)

        cid = _require_str(node["id"], f"{path}.id")
        if cid in criteria:
            raise ManifestError(f"duplicate criterion id {cid!r}", f"{path}.id")

        composed_of = node.get("composed_of")
        if composed_of is not None:
            components = _require_list(composed_of, f"{path}.composed_of")
            if not components:
                raise ManifestError(
                    "a composite must list at least one component; omit "
                    "`composed_of` entirely for an atomic criterion",
                    f"{path}.composed_of",
                )
            composed = tuple(
                _require_str(c, f"{path}.composed_of[{j}]")
                for j, c in enumerate(components)
            )
        else:
            composed = ()

        criteria[cid] = Criterion(
            id=cid,
            description=node.get("description"),
            composed_of=composed,
        )

    # Invariant 3: composites stay labelled, and their components must be
    # real criteria a user can select on their own. This is what makes the
    # composite overridable rather than a black box.
    for i, raw in enumerate(raw_criteria):
        cid = raw["id"]
        for j, component in enumerate(criteria[cid].composed_of):
            if component not in criteria:
                raise ManifestError(
                    f"composite {cid!r} names undeclared component {component!r}",
                    f"criteria[{i}].composed_of[{j}]",
                )
            if component == cid:
                raise ManifestError(
                    f"composite {cid!r} lists itself as a component",
                    f"criteria[{i}].composed_of[{j}]",
                )
    return criteria


def _validate_scores(
    node: Mapping[str, Any], criteria: dict[str, Criterion], path: str
) -> dict[str, float] | None:
    """P7. Scores are optional per member, but partial scoring is not.

    A member that scores anything must score every declared criterion.
    That is what keeps a composite ordering and each of its component
    orderings comparable over the same set of members — the property
    invariant 3 needs in order to let a user override the default.
    """
    raw = node.get("scores")
    if raw is None:
        return None

    scores_node = _require_mapping(raw, f"{path}.scores")
    scored = set(scores_node)
    declared = set(criteria)

    undeclared = sorted(scored - declared)
    if undeclared:
        raise ManifestError(
            f"score for undeclared criterion {undeclared[0]!r}",
            f"{path}.scores.{undeclared[0]}",
        )

    unscored = sorted(declared - scored)
    if unscored:
        raise ManifestError(
            f"member scores some criteria but not {', '.join(repr(u) for u in unscored)}; "
            "a member that scores anything must score every declared criterion",
            f"{path}.scores.{unscored[0]}",
        )

    result: dict[str, float] = {}
    for cid, value in scores_node.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ManifestError(
                f"score must be a number, got {type(value).__name__}",
                f"{path}.scores.{cid}",
            )
        result[cid] = float(value)
    return result


def _validate_aspects(
    node: Mapping[str, Any], criteria: dict[str, Criterion], path: str
) -> list[dict[str, Any]]:
    raw = node.get("aspects")
    if raw is None:
        return []

    aspects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, item in enumerate(_require_list(raw, f"{path}.aspects")):
        apath = f"{path}.aspects[{i}]"
        anode = _require_mapping(item, apath)
        _reject_unknown_fields(anode, _ASPECT_FIELDS, apath)
        _require_present(anode, _ASPECT_FIELDS, apath)

        aid = _require_str(anode["id"], f"{apath}.id")
        if aid in seen:
            raise ManifestError(f"duplicate aspect id {aid!r}", f"{apath}.id")
        seen.add(aid)

        scope = []
        for j, cid in enumerate(_require_list(anode["criteria"], f"{apath}.criteria")):
            name = _require_str(cid, f"{apath}.criteria[{j}]")
            if name not in criteria:
                raise ManifestError(
                    f"aspect scoped to undeclared criterion {name!r}",
                    f"{apath}.criteria[{j}]",
                )
            scope.append(name)

        aspects.append(
            {
                "id": aid,
                "criteria": scope,  # empty list = applies under every criterion
                "claim": _require_str(anode["claim"], f"{apath}.claim"),
            }
        )
    return aspects


def _validate_anchors(node: Mapping[str, Any], path: str) -> list[dict[str, Any]]:
    anchors = _require_list(node["anchors"], f"{path}.anchors")
    if not anchors:
        raise ManifestError("at least one anchor is required", f"{path}.anchors")

    result: list[dict[str, Any]] = []
    for i, item in enumerate(anchors):
        apath = f"{path}.anchors[{i}]"
        anode = _require_mapping(item, apath)
        _reject_unknown_fields(anode, _ANCHOR_FIELDS, apath)
        _require_present(anode, _ANCHOR_REQUIRED, apath)

        start, end = anode["start"], anode["end"]
        for name, value in (("start", start), ("end", end)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ManifestError(
                    f"{name} must be an integer line number, got {type(value).__name__}",
                    f"{apath}.{name}",
                )
        if start < 1:
            raise ManifestError("start is 1-based; it must be >= 1", f"{apath}.start")
        if end < start:
            raise ManifestError(f"end ({end}) precedes start ({start})", f"{apath}.end")

        result.append(
            {
                "path": _require_str(anode["path"], f"{apath}.path"),
                "start": start,
                "end": end,
                "blob": anode.get("blob"),
                "range_hash": _require_str(anode["range_hash"], f"{apath}.range_hash"),
            }
        )
    return result


def _contained(child: Path, parent: Path) -> bool:
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except (OSError, ValueError):
        return False


def _resolve_body(
    node: Mapping[str, Any], manifest_dir: Path, path: str
) -> str:
    """P6. Exactly one of `body` / `body_ref`; a ref may not escape the dir.

    The manifest is untrusted (invariant 8), so `body_ref` is a lookup
    inside `.load-bearing/` and nothing else — not a path into the repo,
    not an absolute path, and not a `..` walk out of the directory.
    """
    has_body = "body" in node
    has_ref = "body_ref" in node
    if has_body and has_ref:
        raise ManifestError(
            "exactly one of `body` / `body_ref` is allowed, not both",
            f"{path}.body_ref",
        )
    if not has_body and not has_ref:
        raise ManifestError(
            "exactly one of `body` / `body_ref` is required", f"{path}.body"
        )

    if has_body:
        return _require_str(node["body"], f"{path}.body")

    ref = _require_str(node["body_ref"], f"{path}.body_ref")
    if Path(ref).is_absolute():
        raise ManifestError(
            f"body_ref must be relative to {MANIFEST_DIRNAME}/, got an absolute path",
            f"{path}.body_ref",
        )
    target = manifest_dir / ref
    if not _contained(target, manifest_dir):
        raise ManifestError(
            f"body_ref escapes {MANIFEST_DIRNAME}/: {ref!r}", f"{path}.body_ref"
        )
    try:
        return target.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ManifestError(f"body_ref target not found: {ref!r}", f"{path}.body_ref") from None
    except OSError as e:
        raise ManifestError(f"body_ref unreadable: {ref!r} ({e})", f"{path}.body_ref") from None


# --- the source -------------------------------------------------------------


@dataclass(frozen=True)
class _Member:
    id: str
    body: str
    anchors: list[dict[str, Any]]
    scores: dict[str, float] | None
    rationale: str | None
    aspects: list[dict[str, Any]]
    metadata: dict[str, Any]


class ManifestSource:
    """Produces one `member` region per fresh member of a repo's manifest.

    Reading, validation and freshness all happen in the constructor, so a
    bad manifest fails at the boundary where the launch command can still
    report it — not midway through session creation.
    """

    def __init__(self, repo_root: str | Path) -> None:
        self._repo_root = Path(repo_root)
        self._manifest_dir = self._repo_root / MANIFEST_DIRNAME
        manifest_path = self._manifest_dir / MANIFEST_FILENAME

        try:
            self.raw_text = manifest_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ManifestError(
                f"no manifest at {MANIFEST_DIRNAME}/{MANIFEST_FILENAME} "
                f"under {self._repo_root}",
                MANIFEST_FILENAME,
            ) from None

        try:
            data = json.loads(self.raw_text)
        except json.JSONDecodeError as e:
            raise ManifestError(f"invalid JSON: {e}", MANIFEST_FILENAME) from None

        members, criteria, default_criterion = _parse(data, self._manifest_dir)
        self._criteria = criteria
        self._default_criterion = default_criterion

        self._fresh, self._stale_member_ids = _partition(members, self._is_fresh)

    # --- port ---------------------------------------------------------------

    def produce(self) -> list[RegionContent]:
        return [
            RegionContent(
                kind=CONTENT_KIND,
                body=member.body,
                metadata={
                    "member_id": member.id,
                    "anchors": member.anchors,
                    "scores": member.scores,
                    "rationale": member.rationale,
                    "aspects": member.aspects,
                    **({"metadata": member.metadata} if member.metadata else {}),
                },
            )
            for member in self._fresh
        ]

    # --- manifest facts a selector and the launch report need ---------------

    @property
    def stale_member_ids(self) -> tuple[str, ...]:
        """Members excluded from selection because their slice moved."""
        return tuple(self._stale_member_ids)

    @property
    def default_criterion(self) -> str:
        return self._default_criterion

    @property
    def criterion_ids(self) -> tuple[str, ...]:
        return tuple(self._criteria)

    # --- freshness ----------------------------------------------------------

    def _is_fresh(self, member: _Member) -> bool:
        """Invariant 6. Every anchor must still hash to what was recorded.

        A member with an unreadable anchor file counts as stale rather
        than as an error: the file may simply have been moved, and one
        member losing its anchor must not refuse the whole manifest.
        """
        for anchor in member.anchors:
            target = self._repo_root / anchor["path"]
            if not _contained(target, self._repo_root) or not target.is_file():
                return False
            try:
                actual = range_hash(target, anchor["start"], anchor["end"])
            except OSError:
                return False
            if actual != anchor["range_hash"]:
                return False
        return True


def _partition(members, predicate):
    fresh, stale = [], []
    for member in members:
        (fresh if predicate(member) else stale).append(member)
    return fresh, [m.id for m in stale]


def _parse(data: Any, manifest_dir: Path):
    root = _require_mapping(data, "")
    _reject_state_fields(root, "")
    _reject_unknown_fields(root, _TOP_FIELDS, "")
    _require_present(root, _TOP_REQUIRED, "")

    _validate_contract_version(_require_str(root["contract"], "contract"), "contract")
    _require_str(root["source_tree"], "source_tree")
    if "source_commit" in root:
        _require_str(root["source_commit"], "source_commit")
    if "generated_by" in root:
        gb = _require_mapping(root["generated_by"], "generated_by")
        _reject_unknown_fields(gb, _GENERATED_BY_FIELDS, "generated_by")

    criteria = _validate_criteria(_require_list(root["criteria"], "criteria"))

    default_criterion = _require_str(root["default_criterion"], "default_criterion")
    if default_criterion not in criteria:
        raise ManifestError(
            f"default_criterion {default_criterion!r} is not a declared criterion "
            f"(declared: {', '.join(sorted(criteria))})",
            "default_criterion",
        )

    raw_members = _require_list(root["members"], "members")
    if not raw_members:
        raise ManifestError("at least one member is required", "members")

    members: list[_Member] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_members):
        path = f"members[{i}]"
        node = _require_mapping(raw, path)
        _reject_unknown_fields(node, _MEMBER_FIELDS, path)
        _require_present(node, frozenset({"id", "anchors"}), path)

        mid = _require_str(node["id"], f"{path}.id")
        if mid in seen:
            raise ManifestError(f"duplicate member id {mid!r}", f"{path}.id")
        seen.add(mid)

        rationale = node.get("rationale")
        if rationale is not None:
            rationale = _require_str(rationale, f"{path}.rationale")

        members.append(
            _Member(
                id=mid,
                body=_resolve_body(node, manifest_dir, path),
                anchors=_validate_anchors(node, path),
                scores=_validate_scores(node, criteria, path),
                rationale=rationale,
                aspects=_validate_aspects(node, criteria, path),
                metadata=dict(_require_mapping(node.get("metadata") or {}, f"{path}.metadata")),
            )
        )
    return members, criteria, default_criterion
