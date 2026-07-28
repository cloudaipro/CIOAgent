# Project Rules
*The project's quality contract. Drafted by the Architect from this project's own docs and
tooling — never invented. Role-neutral by design: renaming the team never touches this file.*

Machines check what machines can check. Human review is for judgment — spec fit, drift,
design. Everything below the judgment line lives here, as commands and standing rules,
so the Reviewer's attention is spent only on what no command can verify.

---

## Mechanical Gate
*Commands that must pass before any review request. The Builder runs them last thing before
signaling done and records the results in handoff/REVIEW-REQUEST.md. A failing gate never
reaches the Reviewer — it is the Builder's to fix.*

Drafted at setup from this project's own tooling — test runner, linter, type checker, build.
If the project has no runnable checks yet, keep the handoff-size row below and write
`NO GATE DEFINED` beside it for the project's own checks; a project that cannot verify itself
mechanically pays for it in review cycles.

| Command | Proves |
|---|---|
| `awk 'END{exit (NR>400)}' handoff/BUILD-LOG.md` | BUILD-LOG has not outgrown rotation. Ships with the framework — do not delete it |
| `scripts/check-handoff.sh brief` | The brief this step was built from is structurally complete — sections present, placeholders filled, Definition of Done carries a command. Ships with the framework — do not delete it |
| `.venv/bin/python -m pytest -q` | Full suite green **on the repo interpreter**. Must be `.venv/bin/python` — the PATH `python` is py3.11 without pandas_ta and silently skips every stock/viz test (KG-11) |
| `.venv/bin/python -c "import cio.agent, cio.bot, cio.committee.engine"` | The three modules the bot boots from still import cleanly — catches a broken import before the suite's 1197 tests do |

The project has no linter, type checker or build step (no `pyproject.toml`, no ruff/mypy/black
in `.venv`), so the suite is the gate. Do not add a lint row until a linter is actually configured.

The handoff check runs at three points, and only the third is in the table above:

| Who | When | Command |
|---|---|---|
| Architect | last line of the Pre-Flight Check, before spinning up the Builder | `scripts/check-handoff.sh brief` |
| Builder | session start, before writing any code | `scripts/check-handoff.sh brief` |
| Reviewer | session start, before reading any code | `scripts/check-handoff.sh review-request` |

Catching a malformed handoff early is the whole point — a Builder who discovers at hour three
that the brief never had an Out of Scope section has already spent the context. The gate row is
the backstop for the case where the brief was edited mid-step.

The handoff-size row is the one gate command the framework provides. It is here because the
Builder writes BUILD-LOG and the Architect rotates it — so without a mechanical check, the
person creating the growth never sees the threshold, and the person who can act on it only
notices by accident. Failing this row is not a code defect: signal it to the Architect, who
rotates. Do not "fix" it by trimming your own entry after the fact.

It is written with `awk`, not `test "$(wc -l < …)"`, on purpose: with the file missing, the
`wc` form exits 0 under zsh (empty string compares as an integer) and reports a green gate
for a BUILD-LOG that is not there. The `awk` form exits non-zero for missing, oversized, and
unreadable alike — L-1's rule, applied to the framework's own gate command.

## Standing Rules
*Project-specific rules the Reviewer checks on every step. Each rule carries its source —
a rule that cannot say where it came from gets deleted, not enforced.*

New rules start **advisory** — the Reviewer flags violations but they do not block.
The Project Owner promotes a rule to **blocking** only after it has caught real problems
without false alarms. Observe first, enforce second.

| # | Rule | Source | Fixable by | Status |
|---|---|---|---|---|
| R1 | Tests are run with `.venv/bin/python -m pytest`, never a bare `python`/`pytest`. A green run reported from the PATH interpreter is not evidence. | KG-11 (BUILD-LOG) | builder | advisory |
| R2 | A missing API key or unreachable network degrades to an empty/neutral result and a log line — it never raises out of a tool, a data fn, or a chat turn. | `cio/data/*` + `engine._ask_*` pattern; every step since AICAS 6 | builder | advisory |
| R3 | Never persist figures (prices, P&L, share counts) into memory notes; recompute from the portfolio/stock tools. Enforced by `memory._guard_figures`. | `cio/memory.py:86` + SYSTEM_PROMPT | builder | advisory |
| R4 | Service attribution (`usage.record`, `convlog.log_call`, dashboard capture) takes the service that actually answered — never a hardcoded literal. | This step; the `"claude"` hardcodes at `cio/agent.py:1464,1484` | builder | advisory |
| R5 | A material fact must come from its claim-appropriate authority; Tier-3 sources never back one. Owner-locked policy, additive changes only. | `cio/data/source_policy.py`, `docs/EVIDENCE-INTEGRITY.md` | owner | advisory |

Rules are born two ways: drafted at setup from the project's written docs, or promoted from
BUILD-LOG `## Lessons` when the same lesson lands twice. A rule that keeps flagging things
nobody fixes is noise — revisit its wording or retire it.

## Iron Rules
*Process invariants. These ship with the framework and are not project-specific.
Violating one is a process bug — log a Lesson, then fix the process, not just the instance.*

1. The Builder never edits REVIEW-FEEDBACK.md. The Reviewer never edits code.
2. A failing or missing Mechanical Gate never reaches the Reviewer.
3. Nothing deploys without the Architect's sign-off and the Project Owner's explicit go-ahead.
4. Scope lock: out-of-scope work goes to BUILD-LOG Known Gaps — never into the current step.
5. Handoff files are the record. A decision that lives only in chat does not exist.
