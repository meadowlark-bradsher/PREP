# Contract — `.load-bearing/` member manifest

*Defines how a target repo declares its load-bearing members and how PREP finds
and reads them. Companion to `debt_event_schema.md`; same discipline (emitters
name, core resolves; no state in transport).*

**Version `load-bearing/0.2`.** Supersedes 0.1, which was drafted 2026-08-31 and
revised 2026-09-01 after two independent implementations — a consumer (PREP) and
a producer — were reconciled against each other. Every change is listed in
[What changed in 0.2](#what-changed-in-02), with the reason.

This file is the canonical copy. A vendored copy in a producing repo can drift
and nothing detects it; re-copying on revision is a person's job.

---

## Purpose

PREP delineates review units by diff geometry and ranks them with a cold LLM
call. Neither knows the repo. This contract lets the agents working *inside* a
repo do both jobs — designate the regions that bear load, and rank them by
criteria the repo chooses — and hands the result to PREP as content.

A **member** is a region of the software that bears load along one or more named
criteria. The criterion is the load type: a member scored high for `correctness`
bears correctness-load; one scored high for `churn-90d` bears change-load.

Two things are outsourced, separably:

1. **Delineation** — what the unit is (required)
2. **Ranking** — which units matter, by which criterion (optional; PREP's own
   selector runs over members if absent)

---

## Location and discovery

```
<repo-root>/
  .load-bearing/
    manifest.json          required; the index
    members/<id>.md        optional; bodies referenced from the manifest
```

- Fixed path, single index file. PREP never scans; it reads `manifest.json` and
  resolves `body_ref` paths relative to `.load-bearing/`.
- One directory name, no aliases.
- Versioning lives in the file (`contract`), never in the path.
- Everything under `.load-bearing/` is tracked in git so the root tree hash
  covers it.

---

## Freshness — the rule an implementation cannot infer

**Read this before implementing anything else.** It is the only rule two
implementations must agree on byte-for-byte, and the only one whose violation is
invisible: a producer that hashes differently marks every member stale on first
run, and the failure looks like the code moved rather than like two programs
disagreeing about newlines.

`range_hash` is the sha256, as lowercase hex, of the anchored slice, computed as:

1. Read the file's bytes.
2. If the bytes begin with a UTF-8 BOM (`EF BB BF`), remove those three bytes.
   An editor adding a BOM has not changed the code.
3. Replace every `CRLF` with `LF`, then every remaining bare `CR` with `LF`. A
   line ending is an artifact of how the file was checked out.
4. Split on `LF`. If the final element is empty, discard it — a trailing newline
   terminates the last line rather than beginning an empty one.
5. Take elements `start-1` through `end-1` inclusive (`start` and `end` are
   1-based and inclusive).
6. Join them with a single `LF`, appending **no** trailing terminator.
7. sha256 that byte string.

Nothing else is normalized. In particular **trailing whitespace is preserved** —
it is a real edit to a real byte, and a hash that forgives it answers a different
question than the one being asked.

Consequences that follow, and that an implementation should test:

- A range ending at the last line hashes identically whether or not the file
  ends in a newline.
- A file edited **outside** a member's anchored range leaves that member fresh.
  This is the point of hashing the range rather than the file.
- Two members anchoring different ranges of the same file have independent
  freshness. Overlapping ranges are legal.
- CRLF, CR and LF checkouts of the same content hash identically.
- `blob` is a cheap short-circuit only. An implementation always recomputes
  `range_hash` and compares; a `blob` mismatch proves nothing on its own.
- Commit SHAs are never consulted for freshness.

---

## Manifest schema

```json
{
  "contract": "load-bearing/0.2",
  "source_tree": "<git root tree SHA>",
  "source_commit": "<git commit SHA, informational only>",
  "generated_by": { "agent": "claude-code", "model": "...", "at": "2026-09-01T..." },
  "criteria": [
    { "id": "correctness", "description": "..." },
    { "id": "churn-90d",   "description": "..." },
    { "id": "identity",
      "description": "what this software is, per its maintainers",
      "composed_of": ["correctness", "churn-90d"],
      "weights": { "correctness": 0.7, "churn-90d": 0.3 } }
  ],
  "default_criterion": "identity",
  "members": [
    {
      "id": "auth/token-refresh",
      "body": "...",                              // OR body_ref, not both
      "body_ref": "members/auth-token-refresh.md",
      "anchors": [
        { "path": "src/auth.py", "start": 40, "end": 88,
          "blob": "<git blob SHA of the file>",
          "range_hash": "<sha256 of the anchored slice>" }
      ],
      "scores": { "correctness": 0.9, "churn-90d": 0.4, "identity": 0.75 },
      "rationale": "highest churn, two reverts this quarter",
      "aspects": [
        { "id": "retry-bound", "criteria": ["correctness"],
          "claim": "retries are capped at max_attempts and the loop exits on 401" },
        { "id": "cache-path",  "criteria": [],
          "claim": "..." }
      ],
      "metadata": {}
    }
  ]
}
```

### Top-level fields

| Field | Req | Meaning |
|---|---|---|
| `contract` | yes | Contract id + version. An implementation rejects unknown **major** versions and tolerates unknown minors. |
| `source_tree` | yes | Root tree SHA of the snapshot the manifest describes. Content-addressed; survives rebase. Never a freshness gate — see `Open`. |
| `source_commit` | no | Provenance only. Never used for freshness. |
| `generated_by` | no | Provenance only. Exactly the keys `agent`, `model`, `at`. |
| `criteria` | yes | The load types this repo ranks by. At least one. |
| `default_criterion` | yes | Which ordering is used when the user doesn't choose. Must be a declared criterion id. |
| `members` | yes | The units. At least one. |

### Criterion fields

| Field | Req | Meaning |
|---|---|---|
| `id` | yes | Repo-local label. May not be a state-shaped name (invariant 1). |
| `description` | no | Human-readable. |
| `composed_of` | no | Present iff this criterion is a composite. Lists component criterion ids, each of which must be declared, and none of which may be the composite itself. |
| `weights` | no | Informational. **PREP never applies them.** |

### Member fields

| Field | Req | Meaning |
|---|---|---|
| `id` | yes | Repo-local, stable across regenerations. PREP keys regions on `(repo, member.id)`. |
| `body` / `body_ref` | exactly one | Raw material the reading is generated from. Both present, or neither, is an error. |
| `anchors` | yes | Where in the tree the body came from. At least one. |
| `scores` | no | Number per criterion id. Higher = bears more load. **Optional per member.** A member that carries `scores` must score every declared criterion; a member that carries none is valid and sits beside members that do. |
| `rationale` | no | Why the agent selected or ranked it. Selector-visible; never reaches the reading or the judge. |
| `aspects` | no | What a correct account of this member must include. |
| `metadata` | no | Opaque to PREP's reading and judging. Not exempt from invariant 1. |

### Anchor fields

`path` (repo-relative), `start`, `end` (1-based, inclusive, `end >= start >= 1`),
`range_hash` (required — see Freshness), `blob` (optional, file-level
short-circuit only).

### Aspect fields

| Field | Req | Meaning |
|---|---|---|
| `id` | yes | Member-local. This is what `missing_aspects` binds to — **and what a human reads when a teach-back fails.** By the time a verdict reaches a screen the `claim` text is no longer in hand, so the id is the error message. Write ids a person can act on. |
| `criteria` | yes | Criterion ids this aspect serves. Empty list = applies under every criterion. |
| `claim` | yes | One sentence stating what the member does, in the terms the judge should check for. |

---

## Invariants

**1. Content, never state.** The manifest carries no field naming a judgement
about a member. The reserved names are `reviewed`, `understood`, `verified`,
`known`, `mastered`, `status`, `confidence`, `mastery` — matched
case-insensitively, at any depth, **including inside `metadata`**, and including
as a criterion or aspect id. Any such field is rejected at ingestion with the
JSON path of the offence, not ignored.

*`metadata` is opaque to the reading and the judge; it is not unexamined at
ingestion. It is the only object in the schema that accepts arbitrary keys,
which makes it the only place a state field could otherwise land — exempting it
would leave this invariant with nothing to do.*

**2. Projection, not synthesis.** PREP selects an ordering by criterion id. It
never computes a new score, blends criteria, or applies `weights`.

**3. Criteria stay labeled.** A composite must declare `composed_of`, and every
member scored on a composite must also be scored on each component. This is what
makes override possible: the agent's composite is authoritative *as the default*,
but a user whose role responds to one load type can select that component
ordering.

**4. Members are named, not resolved.** `id` is repo-local. PREP never maps it to
a ledger node-id at read time; resolution is core-side policy.

**5. Body is raw material.** Agent commentary lives in `rationale` or `metadata`.
The reading generator sees `body` only; the judge sees
`(reading_body, teach_back, aspects?)`.

**6. Freshness is content-addressed and per-member.** A member whose
`range_hash` still matches the working tree is fresh and eligible; one that
doesn't is stale, excluded from selection, and reported by id. **No global
refusal.** A member whose anchor file is missing or unreadable counts as stale
rather than as an error.

**7. Judge scope follows the criterion, and a composite carries its parts.** The
judge receives only aspects whose `criteria` include the active criterion, are
empty, or **name any criterion the active criterion is composed of, transitively.**
A PASS is a PASS *at that scope*; provenance records
`teach-back-verified@<criterion>`, never bare.

*A composite must never surface fewer aspects than a component it contains. By
invariant 3 a composite is the maintainers' whole account and each component a
narrower view a user may override to; the reverse inverts that relationship. It
also fails silently — a member with nothing in scope does not error, it degrades
to unstructured prose and reads as working — and it teaches authors to scope
every aspect empty, which turns this invariant into a no-op that still looks
like it is enforcing something.*

**8. Untrusted input.** The manifest is written by agents in a repo PREP doesn't
control. It is validated against this schema and treated as data; nothing in it
is executed or followed as instruction. `body_ref` resolves inside
`.load-bearing/` and may not be absolute or escape it; an anchor `path` that
escapes the repo root makes its member stale.

**9. Tolerant on read, strict on write.** Invariant 6's tolerance is correct for
a consumer: one stale member must not refuse a usable manifest. The same
tolerance in the *producing* repo is what lets a manifest rot. A producer should
fail its own test suite on any stale member. A producer that inherits invariant
6 and nothing else inherits nothing that keeps the file honest.

---

## What the gate does and does not do

A member is **documentation**. The gate checks that documentation stays attached
to the code it describes. It does not read a value, re-run a computation, or
assert anything about behaviour, and no member should be written as though it
did.

Coverage is `members[].anchors[].path` and nothing else. A filename appearing in
a `body`, a `rationale`, or a `metadata` value is prose or is opaque; it is
compared against nothing and makes that file covered by nothing.

This is stated because the mistake runs in the dangerous direction: believing the
manifest covers a file invites skipping a guard that was never there. A member
whose body *discusses* a generated data file, anchored to the script that writes
it, does not protect that file's contents. If something needs checking, it needs
a check — a test, a digest, a re-run. A member is not one.

---

## Repairing a stale member

A `range_hash` mismatch has two causes with nothing in common, and a producer
that collapses them has one available remedy — a rehash — which yields a manifest
**fresh by hash and wrong by meaning**. That is worse than an openly stale one,
because a stale member announces itself and invariant 6 is built to handle it.

- **MOVED.** The bytes are intact and the line numbers slid because something
  above them grew. Nothing said about that region stopped being true. This is
  detectable rather than asserted: an equal-length window elsewhere in the file
  hashing to the recorded value *is* proof nothing changed, so repair needs no
  human judgement and can be done in bulk.
- **CHANGED.** The bytes are gone and the account may have gone with them. Repair
  is one member at a time, shows the new region, and should refuse when the
  member's body has not been edited — a body that did not change cannot describe
  code that did.

A deliberate exception (a rename, a reformat: bytes changed, every word still
true) is a reason recorded in the commit message, never in the manifest.
Invariant 1 keeps facts about edits out of this file.

---

## Where it lands in PREP

**Built and on `main` as of 2026-09-01.** All adapter-side; the core is untouched.

- **`ContentSource` port** with two adapters: `DiffSource` (wraps `parse_diff`,
  emits `kind: "code_hunk"`) and `ManifestSource` (reads `.load-bearing/`, emits
  `kind: "member"`). Both yield `list[RegionContent]`.
- **`ManifestSelector(criterion)`** alongside the existing two. `None` →
  `default_criterion`. The chosen criterion is recorded on the session.
- **Kind-dispatched prompts.** `reading_v1` for a hunk, `reading_member_v1` for a
  member; an unknown kind is an error, not a fallback.
- **Verdict discriminator.** `missing_aspects` is a list of aspect ids when the
  region carried in-scope aspects, prose otherwise. A `FAIL` naming an id the
  judge was not given is a parse failure, not a soft signal.
- **`ClosureAttempt` carries `criterion` and `aspect_scope`** so invariant 7's
  provenance has somewhere to live.
- **Staleness report** on session launch, with ids.

---

## What changed in 0.2

Every change below came from reconciling two independent implementations. **All
three divergences traced to the same cause: a rule pinned in a document the other
party did not have.** 0.1's Open list said normalization was unpinned while the
consumer's build order had in fact pinned it, so the producer followed 0.1
correctly and landed incompatible three times. A specification with an open list
gets resolved independently by each implementer, and each then writes a
justification describing their own code — which produces confident, well-argued
incompatibility rather than an obvious gap. **Anything genuinely undecided is
listed in Open below and marked as unsafe to guess at.**

| # | Change | Reason |
|---|---|---|
| 1 | **Freshness is fully specified**, step by step, including BOM, bare CR, trailing whitespace and the missing-final-newline case. | 0.1 named only CRLF. Two implementations diverged on BOM and bare CR without either making a decision. Both were latent — neither repo could produce such a file — so the rule would have been settled by whoever first anchored a file an editor touched on Windows, arriving as a hash mismatch with no diff to explain it. |
| 2 | **Invariant 1 applies inside `metadata`.** | 0.1's invariant 1 had no scope qualifier while the consumer's build order exempted `metadata`. With the schema closed everywhere else, the exemption left the rule redundant where it applied and disabled in the only place a state field can land in a valid manifest. Two independent validators reproduced that before either changed. |
| 3 | **Invariant 7 expands composites transitively.** | The literal reading made a composite cover strictly *less* than its own components, inverting invariant 3. Measured on a real manifest: one member of three had no aspects in scope under its own default criterion and degraded silently to prose. |
| 4 | **`scores` is optional per member**, stated in the field row itself. | 0.1's field table and its Open item were read as contradicting each other, and each implementation disambiguated toward its own code. The strict reading rejected manifests the format intends to be valid. |
| 5 | **Invariant 9 added** (tolerant on read, strict on write). | Both implementations arrived at this independently. A producer inheriting only invariant 6 inherits nothing that keeps the file honest. |
| 6 | **Reserved-name list extended** to `confidence` and `mastery`, and stated to cover criterion and aspect ids. | Each implementation's list caught a word the other missed. The list belongs here, not in either validator. |
| 7 | **Overlapping anchor ranges declared legal.** | Used in practice, unaddressed by 0.1. |
| 8 | **Aspect ids documented as human-facing.** | On failure the id *is* the error message a person reads; the `claim` is no longer in hand. 0.1 described ids only as ledger keys, which invites unreadable slugs. |
| 9 | **"What the gate does and does not do" added.** | The coverage inference was got wrong twice in one repo, both times by matching a filename that appeared in a member's prose. The error invites skipping a guard that was never there. |
| 10 | **"Repairing a stale member" added.** | A producer that treats every mismatch as a rehash produces manifests that are fresh by hash and wrong by meaning. |

Manifests declaring `load-bearing/0.1` remain readable — implementations reject
unknown majors only. A 0.1 manifest that relies on the `metadata` exemption, or
that anchors a BOM'd or CR-delimited file, will behave differently under 0.2;
both known implementations conform to 0.2 and no such manifest is known to exist.

---

## Ratified

- Directory `.load-bearing/`, noun `member`, one name, no aliases — 2026-08-31
- Functional over branded name; consumer identity lives in `contract` — 2026-08-31
- Criteria projection with labeled composites (invariant 3) — 2026-08-31
- Optional criterion-scoped aspects (invariant 7) — 2026-08-31
- Content-addressed per-member freshness replaces commit-bound fail-closed
  (invariant 6) — 2026-08-31
- `range_hash` normalization pinned in full (Freshness) — 2026-09-01
- Invariant 1 covers `metadata` — 2026-09-01
- Composites expand transitively for judge scope (invariant 7) — 2026-09-01
- `scores` optional per member — 2026-09-01
- Tolerant on read, strict on write (invariant 9) — 2026-09-01

---

## Open

*Everything here is genuinely undecided. Do not resolve one of these by
implementing it and then reasoning backwards from your own code — that is how
0.1 produced three incompatibilities. Raise it instead.*

- **Member identity across rewrites.** `id` is "stable across regenerations" and
  PREP keys regions on `(repo, id)`. Nothing says what happens when a member is
  legitimately renamed, split in two, or merged. A producer that renames one is
  emitting a new member as far as any consumer can tell, and per-member history
  keyed on the old id is orphaned silently. A `supersedes` field is the obvious
  shape; it is not specified here because nobody has needed it yet, and it is
  much cheaper to add before a ledger accrues history than after.
- **What `source_tree` is for.** Required, and no implementation reads it —
  freshness is per-member by design. Either give it a job or make it optional.
- **`suggested_terms` on a member.** Not adopted: a member is a review unit and a
  debt event is a surrender record. Keep the contracts separate until there is a
  reason not to.
- **Depth of the aspect list.** No guidance on how many aspects a member should
  carry. Three to five is what practice suggests; one rarely distinguishes
  understanding from vagueness, and ten suggests the member is too big.
