# PREP — Predictive Review and Elicitation Protocol

**A self-PR process for developing human ownership of AI-generated code.**

When you review AI-written code the usual way — skim the diff, nod, click
approve — it is very easy to *feel* like you understood it without actually
having done so. PREP makes that impossible by inverting the order: you commit
your own prediction about a change **before** you are allowed to see any
analysis of it, then reconcile the two. It is prior elicitation for code
review. You can't fool yourself into thinking you understood, because your
guess is on the record before the answer appears.

The Python package is named `predictive-review` and installs a `prep` CLI.

## The protocol

A **session** takes one diff and walks it region-by-region through a state
machine. For each region:

1. **Select.** A *selector* picks the load-bearing regions worth engaging
   with (default `llm_judgment`; a no-LLM `first_n_hunks` selector exists for
   dry runs).
2. **Hypothesize.** Before seeing any AI output, you write your own hypothesis
   for each region. Hypotheses are then **locked** — you cannot revise them
   after the reveal.
3. **Reveal.** The model produces a **reading** of each hunk (accurate,
   specific about error handling / control flow / edge cases, and honest about
   what's unclear). Your locked hypothesis and the reading are shown together.
4. **Choose** how deep to go, per region:
   - **Engage** — write a full reconciliation.
   - **Acknowledge** — close without engaging (a one-line note is required).
   - **Defer** — revisit later. Deferred regions block session completion.
5. **Reconcile & judge.** On engage, you write a teach-back in your own words.
   A **closure judge** checks whether it *covers the load-bearing claims* of
   the reading. Generic statements fail; silence about a claim fails; accurate
   disagreement passes.
6. **Dialogue.** A judge FAIL opens a conversation with the model, with exits:
   *try again* (revised teach-back), *disagree* (the reading itself is wrong),
   or *override* (not worth this depth — tagged value / toil / difficulty).
7. **Dispose.** Accept the code as-is, or flag it for redesign with a
   justification.

A session-level **engagement threshold** (`load_bearing_only` / `default` /
`thorough`) tunes how aggressively the selector picks regions and how strict
the judge's coverage bar is.

## Requirements

- Python ≥ 3.12
- [`uv`](https://docs.astral.sh/uv/) (used for env + running)
- An Anthropic API key

## Setup

```bash
# from the project root
cp .env.example .env
# then edit .env — see Configuration below
uv sync
uv run prep init-db      # apply Alembic migrations to bring the DB to head
```

### Configuration

Set these in `.env`:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy URL. Default: `sqlite:///./predictive_review.db` |
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `SELECTOR_MODEL` | Model for region selection — a cheap/fast model is fine |
| `READING_MODEL` | Model that produces the reading |
| `JUDGE_MODEL` | Model that judges teach-back coverage |
| `DIALOGUE_MODEL` | Model for the dialogue loop |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | Optional HTTP basic auth for the web UI |

Tiered assignment is expected: a small model for the selector, stronger models
for reading, judge, and dialogue. Use current Anthropic model ids.

## Usage

### Web UI (recommended)

```bash
uv run prep serve            # http://127.0.0.1:8000
uv run prep serve --reload   # development auto-reload
```

Submit a diff in the browser and walk the whole protocol. The region surface
is a two-pane layout (context on the left, chat/actions on the right), and
**Pop context to new window** breaks the context out into its own window for a
dual-monitor workflow — the main window then collapses to just the chat.

### CLI (end-to-end in one session)

The `prep run` command walks a single session interactively, opening your
`$EDITOR` for the hypothesis and reconciliation steps. Choose a diff source:

```bash
uv run prep run --commit HEAD~1              # git show <sha>
uv run prep run --range main..HEAD           # git diff <range>
uv run prep run --diff path/to/change.diff   # a diff file ('-' for stdin)
git show 2a396f6 | uv run prep run           # or pipe one in
```

Useful flags:

- `--engineer <id>` — identifier recorded on the session.
- `--selector first_n_hunks` — no-LLM dev selector for a **dry run** (exercises
  the mechanics without spending tokens).
- `--threshold load_bearing_only|default|thorough` — engagement threshold.
- `--layout inline|no-hunk` — whether the reconciliation editor shows the hunk.

## Development

```bash
uv run pytest        # run the test suite
```

Layout:

```
src/predictive_review/
  cli.py            # `prep` CLI: init-db, serve, run
  sessions/         # SessionService — the state machine
  domain/           # diff parsing, regions
  selectors/        # region selection strategies
  llm/              # Anthropic client, prompts (reading / judge / dialogue / selector)
  storage/          # SQLAlchemy models + database wiring
  web/              # FastAPI app, routes, Jinja templates, htmx UI, static assets
migrations/         # Alembic migrations
tests/unit/         # unit tests (LLM is faked)
```

## License

See [LICENSE](LICENSE).
