# STATE — as-built product reality

*Living state doc. Companion to the design handoff (`Expanding_PREP_for_cognitive_debt_ledger`). This file records **perishable reality**; the handoff records **stable design**. When they disagree, the handoff is the intended design and this file is what's true right now — resolve by updating one of them deliberately, never by letting them drift.*

> **Handoff doc not present in this repo.** The only tracked prose is `README.md`. The `[human]` fields below can't be cross-checked against the handoff from here; they're left as `TODO(human)`. All `[repo]` fields are filled from the code at the commit noted below.

---

## How to use this file

*(unchanged from template — see original for the prime directive, the `[repo]`/`[human]` split, and the tier vocabulary: **runs** / **coded** / **specced**.)*

Last updated: `2026-09-01` · by: `Claude Code (Opus 5)` · branch: `main` · main HEAD at read: `5e92fcc` (PR #4 merged)

**Orientation for a fresh instance (one paragraph):** The code in this repo is the *contest-era, code-comprehension* PREP: it reviews a **git diff** or a repo's **`.load-bearing/` member manifest**, not a knowledge domain. It runs a real FSM (submit → hypothesis → lock/reveal → reconcile/teach-back → dialogue → disposition) with server-side ordering gates, four LLM-backed components (selector, reading, judge, dialogue), a FastAPI web UI, and a Click CLI. **None of the cognitive-debt-ledger new scope exists in code** — no per-user state, no ontology, no estimator, no MCP connector, no provenance tagging. Those are all `specced`. The `Hunk → RegionContent` widening (§2) is **merged to `main`**, and on top of it a second content producer now exists: `.load-bearing/` member manifests, with a criterion-ranked selector and a structured judge verdict (§1). That is adapter-side work — the core FSM and the independence guarantees are unchanged.

---

## 1. Component status ledger

| Component | Tier | Evidence it's at this tier | Notes / caveats |
|---|---|---|---|
| Judge (PREP closure oracle) | **coded** | `judge.py::ClosureJudge.judge` is complete: strict-JSON verdict, `FAIL`-requires-`missing_aspects`, fresh-context/no-hypothesis independence. Now two-mode — prose without `aspects`, structured aspect-id list with them (§3). Every test uses `FakeClosureJudge` (`tests/fakes.py`). No `.db`, no run logs. | Handoff caveat **still holds**: guarantees read from code, not executed live. Real `AnthropicClient` *is* wired (`wiring.py:33`) but nothing proves it's been invoked. |
| Attribution lock — phase-gate | **runs** | `_require_phase`/`_require_region_status` exercised end-to-end in `test_session_service.py` and `test_web_routes.py` (invalid transitions raise). | Runs in the **test/fake path**. |
| Attribution lock — partial-unique-index | **coded** | Index `uq_hypothesis_locked_per_region` exists in `models.py:295` + migration `3db6ea1…`/`d802…`; DB present in test runs. | The *concurrency* guarantee (≤1 locked under parallel writes) is not exercised — no concurrent test. |
| Attribution lock — type-gate | **runs** | `_to_region_view` rebuilds a `RegionView` with no hypothesis field; `test_contracts.py` pins the component signatures. | Structural; strongest of the three layers. |
| FSM transition endpoints | **runs** (orchestration) / **coded** (real-LLM path) | Web routes + CLI drive `SessionService` end-to-end in tests via `TestClient` + `FirstNHunksSelector`. See §3. | "Runs" = with **fake** LLM components. The four real LLM calls have never run in the real path. |
| Per-user state overlay (tri-state store) | **specced** | No `(user, node)` table anywhere. Closest existing: `RegionStatus` (per-region lifecycle) + `Disposition`/`ClosureMode` — neither is per-user or per-node. | See §3. `known/need-now/set-aside` does not exist. |
| Ontology / topic graph (KST DAG) | **specced** | No graph, no nodes, no `rests_on`/prerequisite edges in code. | Not seeded; nothing to seed into. |
| Estimator (IRT/BKT-style mastery) | **specced** | Only aspirational comment `models.py:4` ("future IRT-style modeling"). No estimator code, no mastery field. | Consistent with handoff: judge stays binary; IRT would live only in selector scheduling (also unbuilt). |
| Selector / resurfacing policy | **coded** (`LLMJudgmentSelector`) / **runs** (`FirstNHunksSelector`) / **specced** (resurfacing) | `LLMJudgmentSelector` (`selectors/llm_judgment.py`) complete, never run live. `FirstNHunksSelector` runs in tests. | The `rests_on` set-aside check and any resurfacing policy are **specced** — selectors today are diff-hunk pickers, not schedulers. |
| `ContentSource` port / `ManifestSource` / `ManifestSelector` | **runs** (fake path) | `content_sources/{base,diff_source,manifest}.py` + `selectors/manifest.py`. `submit(source=...)` takes the port; `DiffSource` and `ManifestSource` both implement it. Manifest validation, per-member `range_hash` freshness, criterion ordering, and the full CLI walk are exercised end-to-end against `tests/fixtures/load_bearing_repo/` (`test_cli_end_to_end.py`). | Contract delta recorded this as **specced**; it is built. "Runs" = with fake LLM components, as everything else here. Web route is diff-only by design in 0.1 (P1). The fixture manifest is hand-authored — no producer skill exists. |
| MCP connector (adapter) | **specced** | No MCP/connector code in tracked files. The chat↔backend seam is the FastAPI HTTP surface only. | Per-user auth handshake: none (web uses optional HTTP basic-auth env vars, not per-user). |
| Skill (session behavior) | **specced** (not in this repo) | No skill in tracked files. `.claude/` is untracked and out of scope for this codebase. | Lives in plugin runtime by design; can't be asserted from here. |
| Reading generation / depth-tailoring | **coded** (generation) / **specced** (depth-tailoring) | `reading.py::ReadingGenerator` complete, runs via fakes in tests, real path unproven. Prompt selection is now **kind-dispatched** (`reading_v1` for `code_hunk`, `reading_member_v1` for `member`); an unknown kind raises before any LLM call. | **Still server-side in code** (not migrated chat-side). Depth-tailoring absent — both prompts fix "3 to 6 sentences". |
| Discovery/backfill miner (FCA) | **specced** | No code. | Phase-2, as expected. |

**Has the judge run end-to-end against a real Anthropic-keyed `LLMClient`?** `[repo]` — **No evidence it has.** The real client is constructed only when `ANTHROPIC_API_KEY` is set (`anthropic_client.py:34`, `wiring.py:33`); there is no committed session DB, no run artifact, and 100% of tests substitute fakes. Whether a human ran it manually is `TODO(human)` — but nothing in the repo proves it. **This is still the line between attribution *guarantee* and attribution *claim*.**

**Smallest path that executes end-to-end *today*:** `[repo]` — the CLI `run` command (`cli.py:83`): `service.submit` → `service.lock_and_reveal` → `service.submit_reconciliation` (→ dialogue/revise loop) → `service.set_disposition`. **Real** in that path: diff parsing, selector dispatch, the entire FSM, all persistence, all ordering gates. **Stubbed unless a key is present:** the four LLM calls (selector pick, reading, judge verdict, dialogue). With `FirstNHunksSelector` + fakes, the whole path runs green (that is exactly what the test suite does); with the real `AnthropicClient`, it is wired but unproven.

---

## 2. The perishable migration — `Hunk → RegionContent`

**Is the `Hunk → RegionContent` widening done?** `[repo]` — **Yes. Merged to `main` in `a1d223e` (PR #2).** On that branch: new `domain/content.py::RegionContent`; `Hunk.to_content()` adapter; `Region.hunk` → `Region.content`; selectors/reading/service updated; `regions.hunk` column → `regions.content` via expand+contract Alembic migrations. All 131 tests pass; migration chain upgrade+backfill+both downgrades verified on a scratch DB. **All three items of remaining surface are now closed.** (1) merged, above; (2) the second content kind has a producer — `kind="member"` via `.load-bearing/` (`content_sources/manifest.py`), so `code_hunk` is no longer the only thing emitted; (3) prompt selection is kind-dispatched — `reading_v1` vs `reading_member_v1`, with an unknown kind raising rather than defaulting to the code prompt. A *glossary/concept* kind still has no producer; "member" is not that. **Design divergence from handoff logged in the Appendix.**

**Is there any live session data yet?** `[repo]` — **No.** No `.db` file in the repo; default DB path (`sqlite:///./predictive_review.db`) is uncreated. `[human]`: whether any throwaway/local DB exists outside the repo is `TODO(human)`. **The migration clock has not started.** Two further migrations have since been added and verified the same way (`c4e7b1a92f30` session criterion, `e91d3c60ab48` closure-attempt criterion / aspect_scope / JSON `missing_aspects`); single head, clean upgrade and downgrade to base.

**Migration cost assessment:** `[repo]` — **Low, and it's already paid on the branch.** On `main`, `regions.hunk` is the frozen `JSON` column (`models.py:220`) and is the migration target. Call sites that touched the `Hunk`/`hunk` field before widening: `domain/region.py`, `reading.py`, both selectors, `sessions/service.py` (persist/rehydrate/snapshot), the ORM column — ~8 core sites plus 2 test assertions; the web/CLI presentation layer was left untouched (snapshot still exposes `hunk_text`, sourced from `content.body`). Because there's no live data, backfill is trivial. ~~Top priority is to merge before any real sessions accrue~~ — **done**; this paragraph is kept as the record of why it was urgent.

---

## 3. Contracts as-built

### Tri-state schema `(user, node)`

**As-built:** `[repo]` — **Does not exist.** No `(user, node)` table, no `known/need-now/set-aside` enum, no `set-aside` value (first-class or derived). The persisted lifecycle is *per-region within one session*, not *per-user across sessions*: `RegionStatus` (`awaiting_hypothesis → awaiting_reveal_choice → awaiting_reconciliation → in_dialogue → awaiting_disposition → closed`), orthogonal `ClosureMode` (`judge_passed | engineer_disagreed | acknowledged | engineer_overrode`), and `DispositionStatus` (`accepted_as_is | flagged_for_redesign`). The nearest analog to "set-aside" is `ClosureMode.ACKNOWLEDGED` + `is_deferred` on a region — but both are session-scoped, not a durable per-user node marker.

### Provenance tagging

**As-built:** `[repo]` — **Not built as a tagging system.** No `self-reported | teach-back-verified | behavior-inferred` tag, no confidence field, no promote-slow/demote-fast logic in code. What *is* persisted per closure: `ClosureAttempt{teach_back_statement, verdict, missing_aspects, criterion, aspect_scope, judge_model_id, judge_prompt_version, attempt_number}` and the region's `closure_mode`. `criterion` and `aspect_scope` are new and are the first half of the `teach-back-verified@<criterion>` provenance the ledger needs: a PASS now records *at what scope* it was earned, so scope-narrowed evidence is distinguishable from whole-member evidence. Still an audit trail rather than a graded provenance/confidence signal — **nothing consumes these columns**, and promote-slow/demote-fast remains policy-on-paper.

### FSM endpoint signatures

*These are `SessionService` methods; the web/CLI are thin adapters over them. Names differ from the handoff sketch.*

| Endpoint (sketch) | Exists? | Actual as-built (service method → HTTP / CLI) | in → out |
|---|---|---|---|
| `start_review` | **yes** | `submit(...)` → `POST /launch` / `cli.py` | `(source: ContentSource, engineer_identifier, selector_name, layout, engagement_threshold, criterion?, source_commit?, source_range?) → session_id` (leaves session in HYPOTHESIS). **`diff_text` is gone** — the caller builds a `DiffSource` or `ManifestSource` at the boundary. |
| `submit_hypothesis` | **yes** | `save_hypothesis(...)` → `POST /sessions/{id}/hypothesis` | `(session_id, region_id, body) → None` — **per-region**, not per-session; appends a `HypothesisRevision` |
| `lock_and_reveal` | **yes** | `lock_and_reveal(...)` → invoked *inside* the hypothesis POST once all regions have a hypothesis (`routes.py:263`) / `cli.py:184` | `(session_id) → None` — locks latest revision per region, generates + persists readings as a side effect (readings are **not** returned; fetched separately) |
| `submit_teachback` | **yes** | `submit_reconciliation(...)` → `POST /sessions/{id}/regions/{region_id}/reconcile` / `cli.py` | `(session_id, region_id, body) → ClosureAttemptResult{verdict: PASS\|FAIL, missing_aspects: list[str]\|str\|None, attempt_number: int}` |

Additional real endpoints with no sketch counterpart: `dialogue_turn`, `submit_revised_teach_back`, `close_with_disagreement`, `submit_override`, the three-way post-reveal choice (`engage_region`/`acknowledge_region`/`defer_region` + `revisit_deferred_region`), and `set_disposition`.

**Ordering enforcement:** `[repo]` — **True, server-side.** `_require_phase` and `_require_region_status` gate every transition in `SessionService`; the prompt is never trusted for ordering. Exercised by tests → **runs**.

**`missing_aspects` shape:** `[repo]` — **Two modes, discriminated by the runtime type.** *Prose* (`str`) when the region declared no in-scope aspects — every diff region, always; `judge_v1.md` still asks for "one short sentence". *Structured* (`list[str]` of member-local aspect ids) when it did, via `judge_member_v1.md`. A structured `FAIL` must name a non-empty subset of the ids the judge was given; anything else is a parse failure, handled exactly as malformed JSON is. Persisted as `JSON` on `ClosureAttempt`, with `aspect_scope` (non-NULL only in structured mode) recording what the judge was permitted to name.

This is a **partial** close on the ledger's blocker. There is now something structured to bind to — but the ids are *member-local and repo-local*, so `missing_aspects → node-id` still needs a resolution policy (open — §5). Nothing consumes them yet.

---

## 4. Old-scope entanglement map

| Contest-era artifact | dead / needs-porting / blocking | What changes under new scope | `[repo]` location |
|---|---|---|---|
| Diff / hunk parsing | **needs-porting** | Still the *only* input path (`unidiff.PatchSet`). Concept path needs a non-diff content source feeding `RegionContent`. Not blocking (adapter boundary is clean). | `domain/diff.py::parse_diff` |
| `Hunk` domain type | **needs-porting (in progress)** | On branch: retained as the diff parser's output; widened to `RegionContent` via `to_content()` at the Region boundary (§2). | `domain/diff.py`, `domain/region.py` |
| PR-merge framing / gating | **needs-porting** | No actual git-merge/PR gating exists; the framing lives in vocabulary — `DispositionStatus{accepted_as_is, flagged_for_redesign}` and the code-review prompts. Reframe for knowledge assessment. Not blocking. | `models.py::DispositionStatus`, `llm/prompts/*` |
| Region FSM | **needs-porting (minimal) — confirmed reusable** | Operates on opaque text bodies; the pass/fail oracle + state machine port unchanged. Handoff claim **confirmed**. | `sessions/service.py`, `judge.py` |
| `regions.hunk` column | **needs-porting (done on branch)** | Migration target → `regions.content` (expand/contract). Not on `main`. | `storage/models.py:220`; migrations `9f1a7c4e2b10`, `a0b2d5f8c3e1` (branch) |
| Contest-specific prompts | **needs-porting** | Rim, not core. `reading_v1`/`judge_v1`/`selector_v1` are code-framed; swap for concept variants + kind-dispatched selection. | `llm/prompts/*.md`, `reading.py` |
| Naming: "PREP" | **needs-porting (note)** | In code the judge is `ClosureJudge` (`judge.py`); `predictive_review`/`PREP` names the **package/repo**, not the judge. The doc's naming note ("`PREP` in code == the judge") does not match the code. | `judge.py`, package root |

---

## 5. Open decisions & next move

### Closed in code — record the resolution so nobody re-opens it
- `[repo]` — **Ordering enforced server-side** (handoff decision #1): settled; lives in `SessionService` phase/status gates.
- `[repo]` — **Judge stays binary PASS/FAIL**: settled; `JudgeOutcome`/`ClosureVerdict` are two-value; no score/delta anywhere.
- `[repo]` — **Reading independence via type signature**: settled; `ReadingGenerator.generate(region)` has no hypothesis path; `_to_region_view` enforces it.
- `[repo]` — **Widening shape:** single concrete `RegionContent{kind, body, metadata}` discriminated by a `kind` string, **not** a `Hunk`+`ConceptContent` type hierarchy. *(Deliberate divergence from the handoff sketch — see Appendix.)*
- `[repo]` — **Content production is a port, not a branch in `submit`:** `ContentSource.produce() -> list[RegionContent]`, with `DiffSource` and `ManifestSource` as the two adapters. `submit` never parses, reads the filesystem, or shells out.
- `[repo]` — **Selection consumes content, not a `Diff`:** the selector contract takes `list[RegionContent]`; diff geometry is read from `metadata`. Forced by the port — with `submit` holding only a source, there is no `Diff` left to hand a selector.
- `[repo]` — **Criteria are projected, never synthesised:** `ManifestSelector` orders by a recorded score. PREP never computes a score, blends criteria, or applies `weights`. `weights` is accepted by the schema and read by nothing.
- `[repo]` — **Manifest freshness is per-member and content-addressed:** `range_hash` over LF-normalised anchored lines. `blob` is a short-circuit only; commit SHAs are never consulted. A stale member is excluded and named, never a global refusal.
- `[repo]` — **A composite criterion carries its components' aspects, transitively** (2026-09-01). An aspect scoped to a component is in scope under any composite containing it, so a composite never covers less than its own parts. The literal reading of invariant 7 gave the opposite and was a defect — see the Appendix.
- `[repo]` — **`range_hash` normalization is fully pinned** (2026-09-01): strip a leading UTF-8 BOM, fold CRLF *and bare CR* to LF, keep trailing whitespace, treat a missing final newline as invisible.
- `[repo]` — **State-shaped fields are rejected, not ignored:** a manifest carrying `reviewed`/`understood`/`verified`/`known`/`mastered`/`status` fails validation with the offending JSON path — **including inside `metadata`** (corrected 2026-09-01; see the Appendix).

### Still open — split by what unblocks it
**Blocked on decision (needs the human):** `[human]`
- `unknown → which policy` (intuition vs. surface-the-prerequisite-chain) — `TODO(human)`
- `missing_aspects → node-id` resolution policy — `TODO(human)`. **Now unblocked on the data side**: structured mode emits member-local aspect ids (§3). Still needs a policy, because the ids are repo-local and PREP deliberately does not resolve them (contract invariant 4).
- Glossary append format + reply grammar — `TODO(human)`
- Cold-start seeding source (for the not-yet-existent ontology) — `TODO(human)`

**Blocked on labor (no decision needed, just build):** `[repo]`
- ~~Merge `claude/widen-region-content` to `main`~~ — **done** (`a1d223e`).
- ~~Kind-dispatched reading/judge prompt selection~~ — **done**. Reading dispatches on `content.kind`; the judge dispatches on aspect presence instead, which is strictly narrower (see Appendix).
- A **producer** for `.load-bearing/` manifests — a reference skill that writes one. The format is specified and consumed; the only manifest that exists is the hand-authored test fixture.
- Web-route support for manifests (deliberately out of 0.1 per P1; CLI only today).
- Everything net-new for the ledger: `(user, node)` tri-state store, ontology/DAG + seed, provenance tagging + confidence, MCP connector + per-user auth, resurfacing/`rests_on` selector, chat-side reading migration, FCA miner.

### Next unblocked move
`[human]` — `TODO(human)` to confirm. **Repo-informed recommendation:** write the producer skill. PREP can now consume a `.load-bearing/` manifest end-to-end, but the only manifest in existence is the hand-authored fixture, so nothing has ever validated the format against an agent that did not already know the answer. That is the cheapest way to find out whether the contract survives contact with a real repo, and it needs no decision from anyone.

The ledger scope remains blocked as before — on human decisions (tri-state policy, `missing_aspects → node-id`) rather than on labor. Note that `ClosureAttempt.criterion` / `aspect_scope` are now written and consumed by nothing: they are a standing invitation to build the promote/demote path, and also dead weight until someone does.

---

## Appendix — deltas from the handoff

- `2026-08-10` — **Widening shape: single discriminated type, not a variant hierarchy.** Handoff sketched `RegionContent` as `Hunk + ConceptContent` variants. As-built (branch `claude/widen-region-content`): one frozen `RegionContent{kind: str, body: str, metadata: Mapping}` with `kind` as the discriminator, plus `Hunk.to_content()`. **Reason:** the core (reading/judge/dialogue) provably reads only `body`; a subclass hierarchy would leak diff geometry into the core type, whereas geometry rides opaquely in `metadata`. Rehydration (`RegionContent.from_dict`) is kind-agnostic, so new kinds add nothing to the core. If the handoff still wants typed variants, that's a reconcile-by-updating-one-doc decision.
- `2026-09-01` — **A composite criterion carries its components' aspects, transitively.** Invariant 7 read literally says the judge sees aspects whose `criteria` include the *active* criterion, so an aspect scoped to a component did not surface under a composite containing it — making a composite cover strictly less than either of its parts. **Reason for the change:** invariant 3 makes a composite the maintainers' whole account with each component a narrower override, so the literal reading inverts the relationship. Measured on this repo's own manifest, where one member of three had no aspects in scope under its own default criterion and dropped *silently* to prose mode. The second-order cost is worse than the first: the workaround is to scope every aspect universal, which turns invariant 7 into a no-op that still looks like it is working. The criteria graph is snapshotted into each region's metadata at submit, so an edit to the manifest cannot change what an in-flight session is judged against.

- `2026-09-01` — **State-shaped names are rejected inside `metadata` too.** The contract's invariant 1 carries no scope qualifier; the build order's P4 carved `metadata` out; the build order says the contract wins where they disagree. **Reason:** measurement rather than preference. With P3 closing every other object in the schema, a state word anywhere else is already rejected as an unknown field — so the carve-out left the rule redundant everywhere it applied and disabled in the only place a state field can actually land in a valid manifest. Two independently written validators reproduced that before either changed. Opaque means the reading and the judge never see it; it does not mean unexamined at ingestion.

- `2026-09-01` — **`range_hash` strips a leading BOM and folds bare CR.** Adopted from an independent implementation of this contract on interoperability rather than merit — the arguments are thin either way and the producer would otherwise have had to change. **The lesson is the failure mode, not the rule:** neither repo could produce a BOM'd or CR'd file, so the divergence was latent and would have been discovered by whoever first anchored a file an editor touched on Windows, arriving as a hash mismatch with no diff to account for it. P2 named only CRLF; that silence is what let two implementations part company without either making a decision.

- `2026-09-01` — **Divergences all traced to one cause, which is the finding worth keeping.** Three incompatibilities surfaced against an independent producer of this format, and every one came from a rule pinned in a document the other party did not have: the normalization rule lived in a build order that never left this side, while the contract's own Open section still described the matter as unpinned. The producer followed the contract correctly and landed incompatible three times. A specification with an open list gets resolved independently by each implementer, and each then writes a justification describing their own code — which produces confident, well-argued incompatibility rather than an obvious gap.

- `2026-08-31` — **Selector contract widened from `Diff` to `list[RegionContent]`.** The build order for `.load-bearing/` said the `ContentSource` port could land with "existing tests pass unmodified except at the `submit` call sites". It cannot: once `submit` holds only a `ContentSource` there is no `Diff` left to hand a selector, so the selector contract had to widen in the same step, changing 9 selector call sites that are not `submit` call sites. **Reason to keep it:** `ManifestSelector` ranks members that have no diff anywhere, so the widening was required regardless; doing it later would have meant two churns of the same seam.

- `2026-08-31` — **The judge dispatches on aspect presence, not on content kind.** The build order asked for "the same dispatch" as the reading generator. **Reason for the divergence:** a member whose declared aspects all serve other criteria would, under kind-dispatch, get the member prompt with an empty aspect list — and P5 requires a `FAIL` to name a non-empty subset of what the judge was given, so that region could never fail. Aspect-presence dispatch routes it to prose, where `FAIL` still means something. Kind-dispatching the judge would also mean handing it the region it is deliberately blind to.

- `2026-08-31` — **`engagement_threshold` region counts made explicit.** The build order referred to "the existing `engagement_threshold` / count logic the other selectors use"; no such logic existed. The threshold only ever became prose inside LLM prompts ("Select 2-4 regions"), and `FirstNHunksSelector` ignores it entirely with a hardcoded 2-4. `llm/threshold.py::selector_region_bounds` now states the counts — `(1,2)/(2,4)/(3,4)` — beside the prose they must stay in sync with. **`FirstNHunksSelector` remains threshold-blind**; changing it would alter diff-session behaviour and was out of scope.

- `2026-08-31` — **`ClosureAttempt.missing_aspects` widened `Text → JSON`.** The build order specified only the two new columns (`criterion`, `aspect_scope`). **Reason:** SQLAlchemy cannot bind a Python list to a `Text` column at all, so something had to change; `JSON` represents the specified `list[str] | str | None` union natively instead of making every reader know when to `json.loads`. The migration re-encodes existing prose as JSON before altering the type and degrades lists back to comma-joined prose on downgrade; round-trip verified on a seeded row.

- `2026-08-31` — **A member with no in-scope aspects falls back to prose mode.** Neither the contract nor the build order covers the case. **Reason:** the alternative — an empty aspect list — makes `FAIL` unreachable under P5. Consequence worth ratifying: a `PASS` on such a region is unscoped even inside a scoped session.

- `2026-08-31` — **STATE deltas recorded as-built rather than as-written.** The contract's four STATE deltas describe this feature as `specced`, because they were written before it was built. Recording that verbatim would make this file wrong on its own terms — it records perishable reality, and the feature runs in the fake path. §1's new row therefore reads **runs**, and notes the discrepancy inline.

- `2026-08-31` — **Prompt versions distinguish the two prompt families.** `member_v1` vs `v1` for both reading and judge, rather than the build order's "as today" (which would record `v1` for both). **Reason:** `prompt_version` exists to keep historical artifacts comparable; a version that did not say which of two prompts ran would defeat it.

- `2026-08-10` — **Naming note contradicts code.** The template's naming note says treat `PREP` in code as *the judge*. In the actual code the judge class is `ClosureJudge` (`judge.py`) and `PREP`/`predictive_review` is the repo/package name. Flagging so the two docs can be reconciled deliberately.
