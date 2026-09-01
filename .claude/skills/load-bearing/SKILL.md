---
name: load-bearing
description: Writes or updates a repo's `.load-bearing/` manifest — the file that declares which regions of a codebase bear load, along which named criteria, and what a correct account of each one must include. Use when asked to create, regenerate, extend, or repair a `.load-bearing/` manifest or its members; when a manifest reports stale members after code has moved; when someone asks "what's load-bearing here" or wants a repo prepared for PREP review. Also use when reviewing a manifest someone else wrote, to check it declares content rather than judgements.
---

# Writing a `.load-bearing/` manifest

A manifest names the regions of a repo that bear load, and says what a correct
account of each one has to include. PREP reads it, picks regions by a criterion,
and tests an engineer's understanding against the account.

**The repo owns the criteria. You only own the format.** What counts as load in
this codebase is a question for the people who maintain it. Your job is to
elicit that, record it faithfully, and get the anchors right — not to decide it.

## The one thing that breaks everything

PREP recomputes every anchor's `range_hash` from the working tree and compares.
Compute it any other way and every member reads as stale on the first run — and
the failure looks like the code moved, not like two programs disagreeing about
newlines.

**Never hand-compute a hash. Always use the script:**

```
python .claude/skills/load-bearing/scripts/lb_manifest.py anchor src/auth.py:14-32
```

Run it from the repo root, so the `path` it emits is repo-relative. That is what
PREP resolves against.

## What a member is

A region where a **decision** lives — somewhere a different reasonable engineer
would have chosen differently, and where being wrong about it costs something.

Not: every public function. Not: one member per file. Not: the parts that were
easiest to describe. A repo with 400 files might have 12 members, and a manifest
listing 80 is one that hasn't chosen.

Ask of each candidate: *if someone misunderstood this, what would break?* If the
answer is "nothing much", it isn't a member.

Anchor to the lines the decision actually lives in — the loop with the bound in
it, not the whole class. A tight anchor stays fresh through unrelated edits to
the same file; a whole-file anchor goes stale every time anyone touches it.

## Criteria belong to the repo

A criterion is a *type of load*. `correctness` is one. `churn-90d` is another.
They are not interchangeable and they are not universal.

Derive them from evidence, then confirm with the human:

- **Ask first.** "What kinds of load matter in this repo?" is a better opening
  than a guess. If they have no answer, propose two or three from evidence and
  ask them to correct you.
- **Ground them.** `git log --since=90.days` for churn. Incident or postmortem
  history for risk. Test density for what's already trusted. A criterion you
  cannot point at evidence for is one you invented.
- **Don't ship the example set.** `correctness` / `churn-90d` / `identity` is
  what the contract uses to illustrate the shape. Reaching for it unchanged in
  every repo is the tell that nobody asked.

A **composite** criterion (`composed_of`) is the maintainers' own answer to
"what is this software?" — it is the default ordering. Declaring its components
separately is what lets a user whose work responds to one kind of load select
that component instead and get a different ordering. That override only works
if the composite and its components genuinely disagree somewhere. If every
ordering comes out the same, the criteria aren't measuring different things.

`weights` is informational. PREP never applies it. Record it if it explains the
composite to a human; expect nothing to read it.

## The body is raw material, not an argument

The body is what a reading is generated from, and the engineer is tested against
that reading. So it describes; it does not advocate.

**In the body:** what the region does, what is bounded, what is ordered, what is
guaranteed, what it assumes, and what a careful reader would get wrong.

**Not in the body:** why you selected it (that is `rationale`), how important it
is, praise, or anything about who has read it. A body that argues produces a
reading that argues, and then the engineer is reconciling against an advocate
instead of against the software.

Write it as plain technical exposition, in the vocabulary the repo already uses.
Two or three short paragraphs is usually right. Put anything of that length in
`members/<id>.md` and reference it with `body_ref` — inline `body` is for the
genuinely short, and a file is easier to review and diff.

`rationale` is the place for "two reverts this quarter, both in the 401 path".
It reaches the selector and nothing else — never the reading, never the judge.

## Aspects are checkable claims

An aspect is one thing a correct account **must** include. It is what a failure
gets reported as, so it has to be specific enough to act on.

- ✅ `eviction runs before insertion so occupancy never exceeds capacity`
- ❌ `the caching logic is correct`

`criteria` scopes the aspect. An aspect that only matters when reviewing for
correctness should say `["correctness"]`; one that matters no matter why you are
looking should say `[]`. A composite criterion carries its components' aspects,
so scoping to a component does not hide it from the composite — you do not need
`[]` to make something visible by default. Scoping is not decoration — PREP shows the judge only
the aspects in scope, and a PASS means "covered *at that scope*". An aspect
list that is all `[]` throws that away.

Three to five aspects per member is usually right. One is rarely enough to
distinguish understanding from vagueness; ten means the member is too big.

## Scores must discriminate

`scores` is optional per member — but if a member scores anything, it must score
**every** declared criterion. Partial scoring is rejected.

Scores exist to produce an ordering. If everything is 0.8, there is no ordering
and the criterion is doing no work. Spread them, and be prepared to say why one
member outranks another.

A member with no scores is still useful — PREP's own selectors can pick it. It
just cannot be ranked by criterion.

## What gets rejected

PREP validates strictly and reports the JSON path of the first offending field.
No lenient mode, no partial acceptance.

- **State-shaped fields.** `reviewed`, `understood`, `verified`, `known`,
  `mastered`, `status` — at any depth, any capitalisation, **including inside
  `metadata`**. A manifest carries content; whether anyone understands it is not
  the manifest's business. This also means **you cannot name a criterion
  `status`** or any of the others.
- **Unknown fields**, anywhere. `metadata` is the only open bag.
- **Both `body` and `body_ref`**, or neither. Exactly one.
- **`body_ref` outside `.load-bearing/`** — no absolute paths, no `..`.
- **Partial `scores`**, or a score for a criterion that isn't declared.
- **A composite naming an undeclared component.**

## Workflow

1. **Read the repo before deciding anything.** You cannot find load in code you
   have not understood. Look at what changes, what has tests, what has comments
   that sound defensive.
2. **Settle the criteria with the human.** Propose, cite evidence, let them
   correct you. Everything downstream depends on this being theirs.
3. **Choose members.** Fewer than feels comfortable. Anchor tightly.
4. **Stamp the anchors with the script** — one `anchor` call per region. Paste
   the emitted object in verbatim.
5. **Write bodies**, then **aspects**, then **scores**.
6. **Validate**:
   ```
   python .claude/skills/load-bearing/scripts/lb_manifest.py check
   ```
   Fix what it names. Re-run until clean.
7. **Commit `.load-bearing/` to git.** The whole directory is tracked, so the
   root tree hash covers it.

## What the gate does not do

A member is **documentation**. The gate checks that documentation stays attached
to the code it describes. It does not read a value, re-run a computation, or
assert anything about behaviour, and no member should be written as though it
did.

Coverage is `members[].anchors[].path` and nothing else. A filename appearing in
a body, in a `rationale`, or in a `metadata` value is prose or is opaque — PREP
compares it against nothing, and it makes that file covered by nothing.

This is worth stating because the mistake runs in the dangerous direction.
Believing the manifest covers a file invites skipping a guard that was never
there: a member whose body *discusses* a generated data file, anchored to the
script that writes it, does not protect that file's contents. If something needs
checking, it needs a check — a test, a digest, a re-run. A member is not one.

## When code moves

Stale is a signal, not a chore — but it has **two causes, and they are not the
same failure**:

- **The bytes moved.** Something above them grew and the line numbers slid.
  Nothing said about that region stopped being true.
- **The bytes changed.** The account may have gone with them.

Collapsing the two leaves one remedy — a rehash — and a command that re-blesses
everything at once produces a manifest that is *fresh by hash and wrong by
meaning*. That is worse than an openly stale one, because a stale member
announces itself and PREP is built to handle it.

So the two are separated, and the separation is mechanical rather than a
judgement call. An equal-length window elsewhere in the file hashing to the
recorded value **is** proof that nothing changed:

```
python .claude/skills/load-bearing/scripts/lb_manifest.py relocate
```

That repairs every pure move in bulk and needs no one to read anything. What it
cannot repair, it names — and those go one at a time:

```
python .claude/skills/load-bearing/scripts/lb_manifest.py slice src/cache.py:11-22
python .claude/skills/load-bearing/scripts/lb_manifest.py attest cache/eviction
```

`attest` prints the region and then **refuses** if the member's body is unchanged
against HEAD. Update the body first; the refusal is the point, because a body
that did not change cannot describe code that did.

For an edit that changed bytes and left every word true — a rename, a reformat —
pass `--unchanged "<reason>"`. The reason goes to stdout for your commit message
and never into the manifest: a fact about an edit is what commit messages are
for, and reader-facing state is what invariant 1 keeps out of this file.

## Reference

`references/format.md` — the full field-by-field schema, the freshness rule
stated precisely, and a worked example.
