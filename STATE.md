# STATE — as-built product reality

*Living state doc. Companion to the design handoff (`Expanding_PREP_for_cognitive_debt_ledger`). This file records **perishable reality**; the handoff records **stable design**. When they disagree, the handoff is the intended design and this file is what's true right now — resolve by updating one of them deliberately, never by letting them drift.*

> **Handoff doc not present in this repo.** The only tracked prose is `README.md`. The `[human]` fields below can't be cross-checked against the handoff from here; they're left as `TODO(human)`. All `[repo]` fields are filled from the code at the commit noted below.

---

## How to use this file

*(unchanged from template — see original for the prime directive, the `[repo]`/`[human]` split, and the tier vocabulary: **runs** / **coded** / **specced**.)*

Last updated: `2026-08-10` · by: `Claude Code (Opus 4.8)` · branch: `claude/widen-region-content` · main HEAD at read: `2362ba6`

**Orientation for a fresh instance (one paragraph):** The code in this repo is the *contest-era, code-comprehension* PREP: it reviews a **git diff**, not a knowledge domain. It runs a real FSM (submit → hypothesis → lock/reveal → reconcile/teach-back → dialogue → disposition) with server-side ordering gates, four LLM-backed components (selector, reading, judge, dialogue), a FastAPI web UI, and a Click CLI. **None of the cognitive-debt-ledger new scope exists in code** — no per-user state, no ontology, no estimator, no MCP connector, no provenance tagging. Those are all `specced`. The single migration that has actually started is `Hunk → RegionContent` (§2), and it is *done on an unmerged branch*.

---

## 1. Component status ledger

| Component | Tier | Evidence it's at this tier | Notes / caveats |
|---|---|---|---|
| Judge (PREP closure oracle) | **coded** | `judge.py::ClosureJudge.judge` is complete: strict-JSON verdict, `FAIL`-requires-`missing_aspects`, fresh-context/no-hypothesis independence. Every test uses `FakeClosureJudge` (`tests/fakes.py`). No `.db`, no run logs. | Handoff caveat **still holds**: guarantees read from code, not executed live. Real `AnthropicClient` *is* wired (`wiring.py:33`) but nothing proves it's been invoked. |
| Attribution lock — phase-gate | **runs** | `_require_phase`/`_require_region_status` exercised end-to-end in `test_session_service.py` and `test_web_routes.py` (invalid transitions raise). | Runs in the **test/fake path**. |
| Attribution lock — partial-unique-index | **coded** | Index `uq_hypothesis_locked_per_region` exists in `models.py:295` + migration `3db6ea1…`/`d802…`; DB present in test runs. | The *concurrency* guarantee (≤1 locked under parallel writes) is not exercised — no concurrent test. |
| Attribution lock — type-gate | **runs** | `_to_region_view` rebuilds a `RegionView` with no hypothesis field; `test_contracts.py` pins the component signatures. | Structural; strongest of the three layers. |
| FSM transition endpoints | **runs** (orchestration) / **coded** (real-LLM path) | Web routes + CLI drive `SessionService` end-to-end in tests via `TestClient` + `FirstNHunksSelector`. See §3. | "Runs" = with **fake** LLM components. The four real LLM calls have never run in the real path. |
| Per-user state overlay (tri-state store) | **specced** | No `(user, node)` table anywhere. Closest existing: `RegionStatus` (per-region lifecycle) + `Disposition`/`ClosureMode` — neither is per-user or per-node. | See §3. `known/need-now/set-aside` does not exist. |
| Ontology / topic graph (KST DAG) | **specced** | No graph, no nodes, no `rests_on`/prerequisite edges in code. | Not seeded; nothing to seed into. |
| Estimator (IRT/BKT-style mastery) | **specced** | Only aspirational comment `models.py:4` ("future IRT-style modeling"). No estimator code, no mastery field. | Consistent with handoff: judge stays binary; IRT would live only in selector scheduling (also unbuilt). |
| Selector / resurfacing policy | **coded** (`LLMJudgmentSelector`) / **runs** (`FirstNHunksSelector`) / **specced** (resurfacing) | `LLMJudgmentSelector` (`selectors/llm_judgment.py`) complete, never run live. `FirstNHunksSelector` runs in tests. | The `rests_on` set-aside check and any resurfacing policy are **specced** — selectors today are diff-hunk pickers, not schedulers. |
| MCP connector (adapter) | **specced** | No MCP/connector code in tracked files. The chat↔backend seam is the FastAPI HTTP surface only. | Per-user auth handshake: none (web uses optional HTTP basic-auth env vars, not per-user). |
| Skill (session behavior) | **specced** (not in this repo) | No skill in tracked files. `.claude/` is untracked and out of scope for this codebase. | Lives in plugin runtime by design; can't be asserted from here. |
| Reading generation / depth-tailoring | **coded** (generation) / **specced** (depth-tailoring) | `reading.py::ReadingGenerator` complete, runs via fakes in tests, real path unproven. | **Still server-side in code** (not migrated chat-side). Depth-tailoring absent — prompt is fixed "3 to 6 sentences" (`reading_v1.md`). |
| Discovery/backfill miner (FCA) | **specced** | No code. | Phase-2, as expected. |

**Has the judge run end-to-end against a real Anthropic-keyed `LLMClient`?** `[repo]` — **No evidence it has.** The real client is constructed only when `ANTHROPIC_API_KEY` is set (`anthropic_client.py:34`, `wiring.py:33`); there is no committed session DB, no run artifact, and 100% of tests substitute fakes. Whether a human ran it manually is `TODO(human)` — but nothing in the repo proves it. **This is still the line between attribution *guarantee* and attribution *claim*.**

**Smallest path that executes end-to-end *today*:** `[repo]` — the CLI `run` command (`cli.py:83`): `service.submit` → `service.lock_and_reveal` → `service.submit_reconciliation` (→ dialogue/revise loop) → `service.set_disposition`. **Real** in that path: diff parsing, selector dispatch, the entire FSM, all persistence, all ordering gates. **Stubbed unless a key is present:** the four LLM calls (selector pick, reading, judge verdict, dialogue). With `FirstNHunksSelector` + fakes, the whole path runs green (that is exactly what the test suite does); with the real `AnthropicClient`, it is wired but unproven.

---

## 2. The perishable migration — `Hunk → RegionContent`

**Is the `Hunk → RegionContent` widening done?** `[repo]` — **Done on branch `claude/widen-region-content`; NOT on `main`, NOT merged.** On that branch: new `domain/content.py::RegionContent`; `Hunk.to_content()` adapter; `Region.hunk` → `Region.content`; selectors/reading/service updated; `regions.hunk` column → `regions.content` via expand+contract Alembic migrations. All 131 tests pass; migration chain upgrade+backfill+both downgrades verified on a scratch DB. **Remaining surface:** (1) merge to `main`; (2) the concept *content kind* is defined but has no producer — only `code_hunk` is emitted (no `ConceptContent`/glossary selector yet); (3) the reading prompt still says "Code:" — the kind-dispatched prompt selection is unbuilt (the one real seam). **Design divergence from handoff logged in the Appendix.**

**Is there any live session data yet?** `[repo]` — **No.** No `.db` file in the repo; default DB path (`sqlite:///./predictive_review.db`) is uncreated. `[human]`: whether any throwaway/local DB exists outside the repo is `TODO(human)`. **The migration clock has not started.**

**Migration cost assessment:** `[repo]` — **Low, and it's already paid on the branch.** On `main`, `regions.hunk` is the frozen `JSON` column (`models.py:220`) and is the migration target. Call sites that touched the `Hunk`/`hunk` field before widening: `domain/region.py`, `reading.py`, both selectors, `sessions/service.py` (persist/rehydrate/snapshot), the ORM column — ~8 core sites plus 2 test assertions; the web/CLI presentation layer was left untouched (snapshot still exposes `hunk_text`, sourced from `content.body`). Because there's no live data, backfill is trivial. **Top priority is to merge before any real sessions accrue**, not to (re)do the work.

---

## 3. Contracts as-built

### Tri-state schema `(user, node)`

**As-built:** `[repo]` — **Does not exist.** No `(user, node)` table, no `known/need-now/set-aside` enum, no `set-aside` value (first-class or derived). The persisted lifecycle is *per-region within one session*, not *per-user across sessions*: `RegionStatus` (`awaiting_hypothesis → awaiting_reveal_choice → awaiting_reconciliation → in_dialogue → awaiting_disposition → closed`), orthogonal `ClosureMode` (`judge_passed | engineer_disagreed | acknowledged | engineer_overrode`), and `DispositionStatus` (`accepted_as_is | flagged_for_redesign`). The nearest analog to "set-aside" is `ClosureMode.ACKNOWLEDGED` + `is_deferred` on a region — but both are session-scoped, not a durable per-user node marker.

### Provenance tagging

**As-built:** `[repo]` — **Not built as a tagging system.** No `self-reported | teach-back-verified | behavior-inferred` tag, no confidence field, no promote-slow/demote-fast logic in code. What *is* persisted per closure: `ClosureAttempt{teach_back_statement, verdict, missing_aspects, judge_model_id, judge_prompt_version, attempt_number}` and the region's `closure_mode`. That's an audit trail of *how a region closed*, not a graded provenance/confidence signal — the asymmetry is still policy-on-paper.

### FSM endpoint signatures

*These are `SessionService` methods; the web/CLI are thin adapters over them. Names differ from the handoff sketch.*

| Endpoint (sketch) | Exists? | Actual as-built (service method → HTTP / CLI) | in → out |
|---|---|---|---|
| `start_review` | **yes** | `submit(...)` → `POST /launch` / `cli.py:166` | `(diff_text, engineer_identifier, selector_name, layout, engagement_threshold, source_commit?, source_range?) → session_id` (leaves session in HYPOTHESIS) |
| `submit_hypothesis` | **yes** | `save_hypothesis(...)` → `POST /sessions/{id}/hypothesis` | `(session_id, region_id, body) → None` — **per-region**, not per-session; appends a `HypothesisRevision` |
| `lock_and_reveal` | **yes** | `lock_and_reveal(...)` → invoked *inside* the hypothesis POST once all regions have a hypothesis (`routes.py:263`) / `cli.py:184` | `(session_id) → None` — locks latest revision per region, generates + persists readings as a side effect (readings are **not** returned; fetched separately) |
| `submit_teachback` | **yes** | `submit_reconciliation(...)` → `POST /sessions/{id}/regions/{region_id}/reconcile` / `cli.py:488` | `(session_id, region_id, body) → ClosureAttemptResult{verdict: PASS\|FAIL, missing_aspects: str\|None, attempt_number: int}` |

Additional real endpoints with no sketch counterpart: `dialogue_turn`, `submit_revised_teach_back`, `close_with_disagreement`, `submit_override`, the three-way post-reveal choice (`engage_region`/`acknowledge_region`/`defer_region` + `revisit_deferred_region`), and `set_disposition`.

**Ordering enforcement:** `[repo]` — **True, server-side.** `_require_phase` and `_require_region_status` gate every transition in `SessionService`; the prompt is never trusted for ordering. Exercised by tests → **runs**.

**`missing_aspects` shape:** `[repo]` — **Free-text prose**, `str | None` (`judge_v1.md` asks for "one short sentence naming a specific claim"). Not structured. The `missing_aspects → node-id` mapping that the ledger scope needs has nothing structured to bind to yet (open — §5).

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
- `[repo]` (branch) — **Widening shape:** single concrete `RegionContent{kind, body, metadata}` discriminated by a `kind` string, **not** a `Hunk`+`ConceptContent` type hierarchy. *(Deliberate divergence from the handoff sketch — see Appendix.)*

### Still open — split by what unblocks it
**Blocked on decision (needs the human):** `[human]`
- `unknown → which policy` (intuition vs. surface-the-prerequisite-chain) — `TODO(human)`
- `missing_aspects → node-id` resolution policy — `TODO(human)` (nothing structured in `missing_aspects` to bind to yet; see §3)
- Glossary append format + reply grammar — `TODO(human)`
- Cold-start seeding source (for the not-yet-existent ontology) — `TODO(human)`

**Blocked on labor (no decision needed, just build):** `[repo]`
- Merge `claude/widen-region-content` to `main` (the perishable item — do before live data).
- Kind-dispatched reading/judge prompt selection (the one remaining widening seam).
- Everything net-new for the ledger: `(user, node)` tri-state store, ontology/DAG + seed, provenance tagging + confidence, MCP connector + per-user auth, resurfacing/`rests_on` selector, chat-side reading migration, FCA miner.

### Next unblocked move
`[human]` — `TODO(human)` to confirm. **Repo-informed recommendation:** merge the `Hunk → RegionContent` widening to `main` now — it's the only migration whose cost grows with time, it's built and verified, and it gates nothing but is gated by nothing (no decision required). Everything downstream of the ledger scope is blocked either on a human decision (tri-state policy, node-id mapping) or on greenfield labor with no code to conflict with.

---

## Appendix — deltas from the handoff

- `2026-08-10` — **Widening shape: single discriminated type, not a variant hierarchy.** Handoff sketched `RegionContent` as `Hunk + ConceptContent` variants. As-built (branch `claude/widen-region-content`): one frozen `RegionContent{kind: str, body: str, metadata: Mapping}` with `kind` as the discriminator, plus `Hunk.to_content()`. **Reason:** the core (reading/judge/dialogue) provably reads only `body`; a subclass hierarchy would leak diff geometry into the core type, whereas geometry rides opaquely in `metadata`. Rehydration (`RegionContent.from_dict`) is kind-agnostic, so new kinds add nothing to the core. If the handoff still wants typed variants, that's a reconcile-by-updating-one-doc decision.
- `2026-08-10` — **Naming note contradicts code.** The template's naming note says treat `PREP` in code as *the judge*. In the actual code the judge class is `ClosureJudge` (`judge.py`) and `PREP`/`predictive_review` is the repo/package name. Flagging so the two docs can be reconciled deliberately.
