# Architect Playbook — Planning
*Load on trigger: entering Direction mode — before writing or revising any brief, before re-sequencing steps.*
*Role-neutral by design — uses role titles only. Renaming the team never touches this file.*

Planning failures are the expensive ones. A bounced review costs a cycle. A wrong plan costs
a step. A wrong problem costs weeks. The difference between a strong plan and a weak one is
rarely effort — it is a short list of checks that strong planners run by reflex. Run them
deliberately instead.

**Fast path** — for trivial work (single function, bug fix under 10 lines, copy change):
state the expected behavior in one sentence, verify the actual code does what you think it
does, write a one-line brief with one observable done-criterion. Skip the rest of this file.
Everything bigger gets the full discipline.

---

## 1. Frame the Problem Before Touching Solutions

Write the problem as one sentence of observable behavior:

**"When [trigger], the system does [current], and it should do [intended]."**

If you cannot fill all three slots, you do not understand the problem yet. Investigating
comes before planning; asking comes before assuming.

- The reported problem is usually a symptom. Plan against the cause. If you deliberately
  patch a symptom — sometimes right under deadline — say so in the brief and log the cause
  as a Known Gap.
- When the Project Owner hands you a solution ("add a button that..."), extract the outcome
  before accepting the design. Ask what the user should be able to do once it exists. The
  proposed solution is data about intent — not the spec. Check it against alternatives
  before transcribing it.
- Classify the gap:
  - **Code gap** — behavior violates intent everyone already agreed on. You own the fix.
  - **Product gap** — intent itself is undefined or disputed. The decision belongs to the
    Project Owner; bring options plus a recommendation.
  - Test: can you point to a spec line, a prior decision, or documented behavior the current
    code violates? No → product gap.

## 2. Verify Before You Plan

Never write a brief about code you have not looked at. Grep the entry point; read the
specific functions the step will touch. Wrong beliefs about code become wrong constraints in
the brief — and constraints get followed. One grep is cheaper than one wrong build.

- Every claim the brief makes about current behavior must have been verified this session —
  not remembered, not assumed from convention.
- Name the riskiest assumption in the plan — the one that, if false, kills the design.
  If verifying it costs under five minutes, verify it now. If it cannot be verified cheaply,
  make Step 1 the step that tests it (see §4).

## 3. Two Options Minimum

For anything non-trivial, write two genuinely different approaches before choosing — one
line each: approach, main advantage, main cost. If you cannot produce a second approach, you
have not understood the design space yet. Look again.

Choose by, in order:

1. **Fewest new concepts.** No new dependency, pattern, or abstraction without a reason
   written into the brief.
2. **Most reversible.** Prefer the decision you can undo. One-way doors — schema shape,
   public API, URL structure, data deletion, anything users bookmark or pay for — get
   flagged, and escalate when intent is not explicit.
3. **Best fit with existing patterns.** The codebase's way beats the theoretically better
   way, unless the pattern itself is the problem being fixed.

Boring wins ties. Clever needs a reason in writing.

## 4. Cut the Steps

A step is one reviewable unit: **one concern, one review, one deploy.**

Sizing tests — restructure the step if any of these fail:

- Can you title it without "and"?
- Can the Reviewer hold the whole diff in one sitting?
- Does the system still run after the step deploys?

Sequencing rules:

- **Riskiest first.** The step that tests the plan's riskiest assumption goes first. If the
  plan is going to die, find out in Step 1, not Step 4.
- **Dependencies point backward.** Each step builds only on deployed steps — never on
  promises about future ones.
- **Vertical over horizontal.** A thin end-to-end slice that proves the whole path beats a
  complete layer that proves nothing. See BRIEF-EXAMPLES.md, Example 2.
- Every step leaves the system working. If a step cannot avoid breaking things, restructure:
  feature flag, parallel implementation, migrate-then-switch.

Rule of thumb: cut so that Step 1 would still be worth deploying even if the rest of the
feature were cancelled tomorrow.

## 5. Write the Brief as Decisions

Every line either locks a decision or flags a hazard. If a line does not change what the
Builder types, delete it.

- **Close the guessing surface.** For each item ask: could two competent builders implement
  this differently in a way that matters? If yes — decide it now, or flag it explicitly as
  the Builder's call. Undecided-and-unflagged is where drift starts.
- **Draw the boundary.** Write Out of Scope as one or two lines naming what this step must
  not touch. Most drift is not a disobedient Builder — it is a boundary that was never
  drawn.
- **Constraints carry reasons.** "Use the framework validator — a custom regex duplicates
  logic and misses cases." A bare constraint gets reinterpreted the moment it becomes
  inconvenient. A constraint with a why survives contact with obstacles.
- **Failure paths are spec.** Decide what happens on invalid input, missing record, timeout,
  double-submit — for every surface the step touches. A brief that only describes the happy
  path delegates error behavior to whoever cares about it least.
- **Definition of Done is observable.** Every criterion is checkable by a command, a click,
  or a diff — never "works correctly."
  - Bad: "validation works" → Good: "POST /register with a malformed email returns 422 and
    the error JSON matches the existing shape"
  - Bad: "page is faster" → Good: "the list renders with one query regardless of row count —
    verified in the query log"

## 6. Pre-Flight Check — the Gate

Before spinning up the Builder, answer all seven in your reply — one line each, in writing.
A mental checklist is a skipped checklist.

1. **Cause or symptom** — what is the underlying cause, and does this step fix it or
   knowingly patch it?
2. **Verified vs assumed** — for each claim the brief makes about current code: where did
   you verify it this session (file:line or command)? A claim you cannot place is an
   assumption — verify it or cut it.
3. **Risk placement** — what is the riskiest assumption, and does Step 1 test it?
4. **Boundary** — what is out of scope, and is it written in the brief?
5. **Guessing surface** — where could two builders diverge, and is each such point decided
   or flagged?
6. **Verification** — what exact commands or clicks confirm Done at the deploy gate?
7. **Silent product decisions** — list what users will see, pay, or lose differently after
   this step ("nothing user-visible" is an acceptable answer). Anything on the list that is
   not specced goes to the Project Owner before the build, not after.

Then run `scripts/check-handoff.sh brief` as the eighth and last line. The seven above are
judgment; this one is mechanical, and it is the cheapest of the eight — it catches a missing
Out of Scope section or a Definition of Done nobody can run *before* the Builder spends a
context window discovering the same thing.

A shaky answer means fix the plan — not soften the answer. Before finalizing, read
BUILD-LOG `## Lessons`: that list is this project's accumulated scar tissue. Do not repeat
an entry that is already on it.

## 7. While the Step Runs

- If the Builder's plan or an escalation reveals the brief was wrong — stop, fix the brief,
  respin. Do not patch by conversation. The brief is the record; chat is not.
- If the step doubles in size mid-flight — halt, split, re-brief. Sunk cost does not apply
  to plans.
- A Builder question that surprises you is signal: the brief had a hole. Fix the hole in the
  brief, then answer.

## 8. Learn From Every Bounce

When a step bounces — the Reviewer rejects it, or the build misses intent — write one line
to BUILD-LOG `## Lessons` before fixing anything: what went wrong → which pre-flight
question would have caught it. Read Lessons before each new brief.

This loop is the point: every mistake becomes a standing rule, and the team gets smarter
with every step instead of resetting each session.

Lessons graduate. The second time the same shape of lesson lands on the list, stop relying
on memory: promote it to a Standing Rule in RULES.md — advisory first, with the Lesson as
its source. A lesson is a note to yourself; a rule is a check the whole team runs on every
step. The third repetition should be impossible. The reverse also holds: a Standing Rule
whose flags keep getting waved through is noise — reword it or retire it.

## Escalation — Bring Decisions, Not Questions

When a call belongs to the Project Owner, bring: your recommendation, the strongest
alternative, and the one thing you would need to know to be sure. Never bring an open-ended
"what do you want?" Batch escalations at natural boundaries — before a brief, at a review,
at the deploy gate — instead of dripping them one at a time.
