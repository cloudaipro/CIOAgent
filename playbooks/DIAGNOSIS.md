# Architect Playbook — Diagnosis
*Load on trigger: entering Diagnose mode — a bug, a regression, unexpected behavior, "why does it do this."*
*Role-neutral by design — renaming the team never touches this file.*

The failure this file prevents: a plausible-sounding explanation delivered confidently,
unverified, from memory of how similar code usually looks. Diagnosis is reading, not
recalling. Follow the protocol in order.

---

## The Protocol

**1. Make it concrete.**
Pin down three things: the exact trigger (input, user action, data state), the exact
observed behavior, the exact expected behavior. Reproduce it if you can. If you cannot
reproduce, trace the code path and state what the code *would* do — labeled as a trace,
not as a confirmed reproduction.

**2. Read the actual code.**
Never describe what code does from memory or from convention. Grep to the entry point,
follow the path, read the exact lines. When you explain behavior to the Project Owner,
cite file:line and quote the decisive line. If you have not read it yet, say "I have not
looked yet" — never fill the gap with what is probably there.

**3. Locate the divergence.**
Follow the data from entry point to symptom. Find the first point where actual behavior
departs from intended behavior — that is the divergence point, and it is often not where
the symptom appears. On long paths, binary-search: check the midpoint, halve the search.

**4. Run the second-cause check.**
When you find a plausible cause, spend one more query looking for a different explanation
of the same symptom before committing. Ask: "if this were fixed, could the symptom still
occur?" The first plausible cause is where diagnosis usually goes wrong — not because it is
never right, but because finding it ends the search too early.

**5. Classify.**
- **Code gap** — the code violates agreed intent. You own the fix.
- **Product gap** — the code does what was specced; the spec does not match what the
  Project Owner wants. That is a decision, not a fix → Project Owner, with options and a
  recommendation.
- **Environment / data** — the code is right; config, data, or deployment is wrong. Fix
  the environment — then decide whether the code should defend against the recurrence
  (validation, migration, guard).

**6. Check the blast radius before proposing the fix.**
Who else calls the broken thing? What else reads the same data? Fix at the cause, not at
the symptom — unless something else depends on the broken behavior. Then: an explicit,
documented shim at the call site, and the real fix logged as a Known Gap with a trigger
for when it must be paid.

**7. Present the diagnosis.**
In this order: what the code currently does (file:line) → why that produces the symptom →
the recommended fix, with a cheaper alternative if a real one exists → effort class:
trivial (fast path) / one step / multiple steps. Then let the Project Owner confirm the
gap matches their intent before any brief is written.

---

## The Two-Strikes Rule

Two failed fix attempts on the same symptom means the diagnosis is wrong — not unlucky.
Stop patching. Return to step 1 with the new evidence: both failed fixes are data, and
what they tell you is what the cause is *not*. The most expensive debugging sessions are
one wrong diagnosis defended by five patches.
