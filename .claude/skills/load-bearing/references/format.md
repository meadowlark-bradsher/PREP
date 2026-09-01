# `.load-bearing/` format reference

Contract `load-bearing/0.1`. PREP rejects unknown **major** versions and
tolerates unknown minors.

## Layout

```
<repo-root>/
  .load-bearing/
    manifest.json          required; the index
    members/<id>.md        optional; bodies referenced by body_ref
```

Fixed path, single index. PREP never scans the directory — it reads
`manifest.json` and resolves `body_ref` relative to `.load-bearing/`.

## Freshness — the rule that must match exactly

`range_hash` is:

> sha256 over lines `start..end` inclusive, 1-based, after: stripping a leading
> UTF-8 BOM, folding CRLF **and bare CR** to LF, **keeping** trailing
> whitespace, and treating a missing final newline as invisible. Selected lines
> are joined with LF and **no trailing terminator is appended**, so a range
> ending at the last line hashes the same whether or not the file ends in a
> newline.

Consequences worth knowing:

- A file edited **outside** a member's anchored range leaves that member fresh.
  This is the point of hashing the range rather than the file.
- Two members anchoring different ranges of the same file have independent
  freshness.
- `blob` is a cheap short-circuit only. PREP always recomputes `range_hash` and
  compares; a `blob` that no longer matches proves nothing on its own.
- Commit SHAs are never consulted for freshness. `source_commit` is display-only.
- CRLF and LF checkouts of the same content hash identically.

Use `scripts/lb_manifest.py anchor PATH:START-END`. Do not reimplement this.

## Top level

| Field | Req | Meaning |
|---|---|---|
| `contract` | yes | `load-bearing/0.1` |
| `source_tree` | yes | Root tree SHA of the snapshot described. Content-addressed; survives rebase. |
| `source_commit` | no | Provenance only. Never used for freshness. |
| `generated_by` | no | `{agent, model, at}` — those three keys only. |
| `criteria` | yes | The load types this repo ranks by. At least one. |
| `default_criterion` | yes | Must be a declared criterion id. |
| `members` | yes | At least one. |

## Criterion

| Field | Req | Meaning |
|---|---|---|
| `id` | yes | Repo-local label. Cannot be a state-shaped name (see below). |
| `description` | no | Human-readable. |
| `composed_of` | no | Present iff composite. Every component must be declared, and none may be the composite itself. |
| `weights` | no | Informational. PREP never applies it. |

## Member

| Field | Req | Meaning |
|---|---|---|
| `id` | yes | Repo-local, stable across regenerations. PREP keys regions on `(repo, id)`. |
| `body` / `body_ref` | exactly one | Raw material the reading is generated from. |
| `anchors` | yes | Where in the tree it came from. At least one. |
| `scores` | no | Number per criterion. Higher = bears more load. All-or-nothing: a member that scores anything must score every declared criterion. |
| `rationale` | no | Why it was selected. Selector-visible only — never reaches the reading or the judge. |
| `aspects` | no | What a correct account must include. |
| `metadata` | no | Opaque to PREP. The only place state-shaped keys are legal. |

## Anchor

`path` (repo-relative), `start`, `end` (1-based, inclusive, `end >= start >= 1`),
`range_hash` (required), `blob` (optional).

## Aspect

| Field | Req | Meaning |
|---|---|---|
| `id` | yes | Member-local. This is what a failure reports. |
| `criteria` | yes | Criterion ids this aspect serves. `[]` = applies under every criterion. |
| `claim` | yes | One sentence stating what the member does, in terms the judge can check. |

A **composite** criterion carries its components' aspects, transitively: an
aspect scoped to `correctness` is in scope under `identity` when `identity` is
composed of it. A composite never covers less than its own parts.

A member whose aspects all belong to unrelated criteria is judged in prose mode
for that session — the structured path needs at least one aspect in scope.

## Rejected outright

Validation fails with the JSON path of the offending field. There is no lenient
mode.

- Any of `reviewed`, `understood`, `verified`, `known`, `mastered`, `status` —
  at any depth, case-insensitive, **including inside `metadata`**. Opaque means
  the reading and the judge never see it; it does not mean unexamined at
  ingestion, and `metadata` is the only place a state field can otherwise land.
- Unknown fields in any object except `metadata`.
- `body` and `body_ref` both present, or both absent.
- `body_ref` that is absolute or escapes `.load-bearing/`.
- Partial `scores`, or a score for an undeclared criterion.
- A composite naming an undeclared component, or itself.
- `default_criterion` that is not declared.
- Empty `criteria` or empty `members`.

## Worked example

```json
{
  "contract": "load-bearing/0.1",
  "source_tree": "9c1f4a7b2e8d05366a4bb1c7f0e39d2a5b8c4e17",
  "generated_by": { "agent": "claude-code", "model": "claude-opus-5", "at": "2026-08-31T09:14:22Z" },
  "criteria": [
    { "id": "correctness", "description": "How much of the system's correctness rests on this being right." },
    { "id": "churn-90d",   "description": "How much this has moved in the last 90 days." },
    { "id": "identity",
      "description": "What this software is, per its maintainers.",
      "composed_of": ["correctness", "churn-90d"],
      "weights": { "correctness": 0.7, "churn-90d": 0.3 } }
  ],
  "default_criterion": "identity",
  "members": [
    {
      "id": "auth/token-refresh",
      "body_ref": "members/auth-token-refresh.md",
      "anchors": [
        { "path": "src/auth.py", "start": 14, "end": 32,
          "blob": "fc10af10f46dd67b192ff7ab15dacdeeb41da318",
          "range_hash": "77a670373c2990248075687e3ac1bdaec2da6d2d46a687d358468b70703dc677" }
      ],
      "scores": { "correctness": 0.9, "churn-90d": 0.3, "identity": 0.72 },
      "rationale": "Two reverts this quarter; the 401 path was wrong twice.",
      "aspects": [
        { "id": "retry-bound", "criteria": ["correctness"],
          "claim": "retries are capped at MAX_ATTEMPTS and the loop exits immediately on 401" },
        { "id": "failure-modes-distinct", "criteria": [],
          "claim": "exhaustion and expiry raise different exceptions because callers respond differently" }
      ],
      "metadata": {}
    }
  ]
}
```

A complete multi-member example, including a deliberately stale member and one
with no scores, lives in PREP at `tests/fixtures/load_bearing_repo/`.
