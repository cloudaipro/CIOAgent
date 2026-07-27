# Token Optimization

## What Actually Costs Money

Context you accumulate is re-sent — and re-billed — on every turn that follows. An agent's
cost grows with the *square* of how long it runs, not with how many files it opened.

Measured on a real multi-hour Three Man Team session: **94.6%** of the bill was re-processing
accumulated context (cache reads + cache writes). Output was 5.3%. Every file read that day,
combined, was about **0.1%**.

Read what changes a decision. Bound how long you run. Checkpoint and respawn rather than
continuing a swollen context.

Three levers move the 94.6%, in order of impact:

1. **Bound each role's context** — cap and respawn (see Context Budget in the role files).
2. **Keep the cache warm across handoffs** — the 1-hour TTL (below).
3. **Run execution roles on cheaper tiers** — Builder on Sonnet, Reviewer on Haiku (below).

There is a fourth lever, and it does not look like a token lever at all: **a bounced step is
the most expensive event in this methodology.** A rejected step re-runs the Reviewer, the
Builder fix, and the Architect re-brief — re-billing every agent's accumulated context across
the whole loop. Any ambiguity that makes a step bounce costs more than every file read in the
project's lifetime. That is why the Mechanical Gate and `scripts/check-handoff.sh` exist:
they are token optimizations wearing a quality-control hat.

## Judgment, Not Rules

Earlier versions of this file carried a five-line rule block that every role recited at
session start. It is gone. Current models already batch parallel calls, skip speculative
reads, and avoid restating the prompt back at you — rules for those cost tokens every session
and buy nothing. What is worth writing down is the part a model cannot infer from your repo:

- **Skills and memories are pointers, not proof.** They record what was true when written.
  Verify a `file:line` before acting on one. The rule this replaces said "trust it, skip the
  file read" — that traded correctness for a rounding error, and a wrong build is a bounce.
- **Grep before Read.** Not because reading is slow — because the lines you did not need stay
  in context and get re-billed on every turn after. Use `offset`/`limit` for surrounding
  context. Do not re-read a file already in context this session.
- **Filter bash output at the call site.** `| tail -20`, `--quiet`, grep for the assertion you
  actually care about. A full test log costs nothing to produce and everything to carry.
- **Route a genuinely large verification to a throwaway subagent** that returns a verdict, not
  a log. Judge that by whether the log is large, not by a line count — spawning an agent to
  dodge twenty lines of output costs more than the twenty lines.

## Cache TTL — Keep It Warm Across Handoffs

Claude Code's prompt cache defaults to a **5-minute** TTL. Three Man Team's whole loop runs on
gaps longer than that — Architect writes a brief, you review it, Builder waits on a Monitor,
Reviewer reads a diff. Every handoff pause lets the cache expire, and the next call re-uploads
the entire accumulated prompt at **1.25× input price**. On a real session those cold rewrites
alone were $25 of a $114 day.

Turn on the 1-hour cache. One line in `~/.claude/settings.json`:

```json
{ "env": { "ENABLE_PROMPT_CACHING_1H": "1" } }
```

The 1-hour write costs 2× instead of 1.25×, so it pays for itself by the third read — and TMT
sessions average hundreds of reads per stream. For this methodology specifically it is pure
profit, because the handoff gaps are exactly the 5-to-60-minute window the short TTL misses.

## Model Routing — Match the Tier to the Job

The Architect makes the decisions that are expensive to get wrong; Builder and Reviewer execute
against a written brief. Route accordingly:

| Role | Model | Why |
|---|---|---|
| Architect | `opus` | judgment, planning, deploy sign-off — never lower |
| Builder | `sonnet` | bounded execution against a brief; ~40% cheaper on every axis, gate-backed |
| Reviewer | `haiku` | fast judgment over a small listed diff the gate already vetted |

Raise a tier for the floors — irreversible steps (migration, deletion, external side effect)
run Builder on `opus`; security-sensitive or architecturally load-bearing diffs raise Reviewer.
Full policy lives in `ARCHITECT.md` → Model Allocation.

**One caveat, and it is easy to get wrong:** "let the model use its judgment instead of giving
it rules" is a finding about current-generation frontier models. The `haiku` alias resolves to
a smaller, older-generation model. The explicit checklists in `REVIEWER.md` are load-bearing
there — do not strip them to save tokens. Deconstrain a role only when the tier it runs on
justifies it.

## RTK — Bash Output Compression

This skill controls how the team thinks, reads, and responds. RTK handles what comes back from
bash commands.

[RTK](https://github.com/rtk-ai/rtk) is a separate tool that compresses bash command output
before it hits the context window. Tools like `find`, `ls`, and `grep` can return hundreds of
lines nobody needs — RTK intercepts that output and compresses it, often cutting 50-70% of
bash output tokens automatically.

RTK is not part of Three Man Team and we don't manage it. Install it separately if you use
Claude Code CLI heavily. The combination of RTK (bash layer) plus this skill (behavior layer)
is where the savings compound.

Install: https://github.com/rtk-ai/rtk

## Role-Scoped Loading

Each role loads only what their job requires:

- **Architect** — checkpoint + BUILD-LOG + ARCHITECT-BRIEF, plus the playbook for the current
  mode (PLANNING at brief time, DIAGNOSIS at debug time) — at mode entry, never at session start
- **Builder** — ARCHITECT-BRIEF + the reference files the brief names
- **Reviewer** — REVIEW-REQUEST + the specific files and line ranges Builder listed

The full project spec, schema, and flow docs stay on disk until explicitly needed.

## Checkpoint-First

`handoff/SESSION-CHECKPOINT.md` replaces reading BUILD-LOG and the full spec when it is
current. Architect writes it at the end of every session: where we stopped, what was decided,
what is open, and the exact resume prompt.

Auto-memory does not replace it. Subagents do not inherit the main thread's memory — **anything
a subagent must know cannot live in auto-memory.** Cross-agent state belongs in the handoff
files; durable preferences that follow you between projects belong in memory.

## CLAUDE.md as Router

CLAUDE.md is a routing file, not a knowledge base — every byte costs tokens on every session.
Keep it under ~50 lines, and spend those lines on the two things a model cannot work out by
reading the repo: where to go next, and the gotchas that have already bitten someone here.
