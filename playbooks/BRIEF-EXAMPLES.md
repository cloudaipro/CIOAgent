# Architect Playbook — Brief Examples
*Load on trigger: first brief of a project · any brief after a bounced step · any multi-step feature.*
*These are calibration examples. The annotations are the content — read them, not just the briefs.*

---

## Example 1 — The Same Step, Written Twice

Scenario: the Project Owner says — "Users need to be able to cancel their subscription
themselves. Right now they email us and it's killing support."

### The weak brief

```
## Step 8 — Subscription cancellation
- Add a cancel button to the account page
- Cancel the subscription in Stripe when clicked
- Show a confirmation
- Make sure it handles errors

### Definition of Done
- [ ] Users can cancel their subscription
- [ ] Errors are handled
```

Why this bounces:

- "Cancel the subscription" silently makes a product decision — immediately, or at period
  end? Refund or no? Those change what users pay. That was the Project Owner's call, and
  this brief made it by accident. (Pre-flight #7)
- No boundary drawn. Is downgrade part of this? A win-back offer? A confirmation email?
  The Builder will guess, and every guess is drift. (Pre-flight #4)
- "Handles errors" decides nothing. What does the user see when the Stripe call fails?
  Can they retry? Is local state consistent if Stripe succeeded but the response was lost?
  Failure paths are spec.
- Neither done-criterion is checkable. "Users can cancel" — verified how, on which account,
  with what result observable where?
- The riskiest part — the payment-provider integration and the webhook that confirms the
  cancellation — is not even mentioned.

### The strong brief

```
## Step 8 — Self-serve subscription cancellation

### Decisions
- Cancellation takes effect at period end, no proration — confirmed with Project Owner
  2026-07-04. (End-of-period keeps the refund policy unchanged and the support script valid.)
- Cancel via Stripe `subscriptions.update` with `cancel_at_period_end: true` — not a delete.
  A delete ends access immediately, which contradicts the decision above.
- Local `subscriptions.status` moves to `pending_cancellation` only after the Stripe call
  succeeds. Stripe is the source of truth; the existing webhook handler in
  `billing/webhooks.py` moves it to `cancelled` at period end.
- Confirmation screen states the exact end-of-access date, taken from Stripe's response.
- If the Stripe call fails: status stays unchanged, show the existing retryable-error
  banner, log through the existing billing logger. No partial local state, ever.

### Build Order
1. Backend: cancellation endpoint + status transition + failure path.
2. Frontend: cancel button on account page → confirm dialog → endpoint → confirmation screen.

### Out of Scope
- No win-back / retention offer, no downgrade path, no cancellation-reason survey.
- Do not touch the webhook handler — it already processes `customer.subscription.updated`.
- No email notification in this step — logged as Known Gap KG-12.

### Flags
- Flag: the account page has two render paths (`account.tsx` and the legacy
  `settings_view`). The button goes in `account.tsx` only. If you find users still hitting
  the legacy path, stop and escalate — do not add it in both places.

### Definition of Done
- [ ] Cancel on a test subscription → Stripe dashboard shows cancel_at_period_end=true,
      local status = pending_cancellation, access still works.
- [ ] Stripe failure (mock a 500) → status unchanged, banner shown, error logged.
- [ ] Confirmation screen shows the correct period-end date from the Stripe response.
```

What the strong brief did, mechanically:

- The product decision — period end vs immediate — went to the Project Owner **before** the
  brief, and the brief records the decision with the date and the why. No future session
  reopens it by accident.
- Every constraint carries its reason. "Not a delete" survives because the brief says what
  a delete would break.
- The boundary is explicit — and one boundary line ("do not touch the webhook handler")
  defuses the likeliest scope expansion in advance.
- The failure path is decided, not delegated: exact user-visible behavior, exact state rule.
- The flag points at a real trap found by reading the code — two render paths. That line is
  what "verify before you plan" buys.
- Every done-criterion is a runnable check. The deploy-gate verification is already written.

The weak brief is shorter to write and longer to ship. The strong brief costs minutes of
Architect time and removes two bounce cycles and one accidental product decision.

---

## Example 2 — Cutting Steps: Horizontal vs Vertical

Scenario: add in-app notifications — bell icon, unread count, notification list,
mark-as-read. Events come from three subsystems.

### The horizontal cut (wrong)

```
Step 1 — Notifications DB schema + models for all event types
Step 2 — Notification service: create/read/mark, all three subsystem integrations
Step 3 — API endpoints
Step 4 — Frontend: bell, count, list, mark-as-read
```

Why it fails: nothing works until Step 4. The plan's real risks — does event volume from
subsystem A swamp the table? is the unread-count query fast enough to run on every page
load? — surface at the very end, after everything is already built on top of them.
Steps 1–3 deploy dead code. The Reviewer reviews schema and service against imagined
future use, which is guessing.

### The vertical cut (right)

```
Step 1 — Thinnest full path: subsystem A's single highest-volume event → notification row
         → unread count in the existing header. No bell UI yet — count only.
         Proves the two riskiest assumptions: write volume, count-query cost per page load.
Step 2 — Bell + list UI + mark-as-read, built on Step 1's data.
Step 3 — Subsystems B and C emit into the now-proven pipe.
```

Every step deploys something observable. The riskiest assumptions are tested in Step 1
while the sunk cost is one table and one hook. If Step 1's numbers are bad, the redesign
costs a step — not the feature.

Rule of thumb: cut so that Step 1 would still be worth deploying even if the rest of the
feature were cancelled tomorrow.
