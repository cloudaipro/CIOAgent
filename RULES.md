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
| `[command]` | [What passing means — e.g. "no lint errors", "all tests green", "build compiles"] |

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
| R1 | [Stated so a violation is objectively checkable] | [Doc, decision, or Lesson L-N it comes from] | builder / architect / owner | advisory |

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
