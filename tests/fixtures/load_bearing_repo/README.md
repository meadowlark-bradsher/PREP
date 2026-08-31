# `load_bearing_repo` — hand-authored test fixture

A miniature target repo: three source files plus a `.load-bearing/`
directory describing them. **Every value in `manifest.json` was written
by hand.** There is no producer skill yet (deliberately out of scope for
0.1), so nothing here was machine-generated and nothing round-trips
through a real generator.

## What each member exercises

| Member | Body | Scores | Freshness | Exercises |
|---|---|---|---|---|
| `auth/token-refresh` | `body_ref` | all three criteria | fresh | the happy path; aspects scoped to different criteria |
| `cache/eviction` | `body_ref` | all three criteria | **stale** | exclusion + reporting, without a global refusal |
| `cache/lru-read` | `body_ref` | all three criteria | fresh | per-member freshness — same file as `cache/eviction` |
| `parser/tokenize` | inline `body` | **none** | fresh | unscored members stay eligible for PREP's own selectors |

## The two deliberately doctored values

Both are load-bearing for tests; do not "fix" them.

- **`cache/eviction`'s `range_hash`** does not match `src/cache.py` lines
  11–22. It is the real sha256 of a plausible earlier version of that
  slice, which is what a manifest looks like after the code moves under
  it. This is the only stale member.

- **`parser/tokenize`'s `blob`** does not match `src/parser.py`. It is
  the real git blob SHA of that file plus a trailing class — a change
  *outside* the anchored range. Its `range_hash` still matches, so the
  member is fresh. This is what makes `blob` a short-circuit rather than
  an authority (P2).

`source_tree` and `source_commit` are synthetic. Neither is consulted for
freshness; `source_tree` is required by the schema and `source_commit` is
provenance only.

## Criteria

`correctness` and `churn-90d` are atomic; `identity` is a composite of
both. The scores are chosen so ordering by `churn-90d` **reverses**
ordering by `identity` across the two fresh scored members — that
disagreement is what the selector's override tests need.

## Regenerating hashes

If you change a source file, recompute the affected `range_hash` with
`predictive_review.content_sources.manifest.range_hash(path, start, end)`
and update the anchor — except for the two doctored values above.
