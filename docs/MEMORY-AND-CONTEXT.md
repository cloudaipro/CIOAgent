# CIO Agent — Memory & Context Management

**Status:** current as of 2026-06-09
**Scope:** the durable memory / context subsystem (`cio/memory.py`, `cio/recall.py`,
`cio/context.py`, `cio/committee/agent_memory.py`, `cio/committee/note_sanitizer.py`,
`cio/committee/sanitizer_log.py`, `cio/dedup_notes.py`, and the dashboard memory/sanitizer
views). This document is standalone — it does not assume familiarity with the rest of the
codebase.

---

## 1. Purpose

The CIO Agent runs as a 24/7 assistant (Telegram bot + investment committee). It needs
memory that:

- **survives restarts** — the runtime is long-lived and gets bounced;
- **stays bounded** — months of continuous use must not grow without limit;
- **never serves stale numbers** — a memorized price or ratio is a correctness hazard;
- **is recallable by meaning, not just keywords** — a fact phrased differently from how it
  was stored must still surface;
- **is isolated per agent / per chat** — one committee specialist must never read another's
  notes, and the bot's conversational memory must not leak into committee reasoning;
- **curates itself** — useful notes should rise, unused notes should decay.

The subsystem is internally called **MemCore**. It is qualitative-only: it stores insight,
preferences, plans, watch-items, and procedures — never financial figures.

---

## 2. Design principles

| Principle | Mechanism |
|---|---|
| Durable | SQLite on local disk; everything is a row. |
| Bounded | Per-scope cap with importance×recency×usage eviction. |
| No stale numbers | The **figures firewall** (three layers — §8). |
| Recall by meaning | Hybrid keyword (FTS5/BM25) + semantic (sqlite-vec) search fused with RRF. |
| Isolation | Hard **scope** namespacing; committee notes in a separate DB. |
| Self-improving | Recall bumps a hit counter; frequently-used notes auto-promote; the rest decay out. |
| Offline-stable | Local embedding model cached on disk; the read path needs no network. |
| Fail-safe | Every memory call is best-effort and never breaks a chat/committee turn. |

---

## 3. Storage topology

Two SQLite databases, same schema family:

- **`data/cio.db`** (a.k.a. `db.DB_PATH`) — conversational / portfolio memory: scopes
  `global` and `chat:<id>`, plus the operator profile, session digests, conversation turns,
  the Telegram chat registry, token-usage counters, and runtime meta. *Path fallback:* if
  `data/cio.db` is absent but a legacy `data/cfo.db` exists, `db.DB_PATH` resolves to the
  latter so the owner's existing DB is used in place (the rename left the file untouched).
- **`data/committee.db`** — the investment committee's isolated per-agent memory (scopes
  `committee:<role>`), the committee LLM-call transcript, token usage, and the figures-
  sanitizer audit log. Kept separate so the agents' accruing notes + 768-dim embeddings do
  not bloat the portfolio DB. Runs in **WAL mode** for concurrent readers + one writer
  during the parallel committee.

Both paths self-initialize on `db.connect()` (schema, FTS triggers, vec tables, migrations,
sqlite-vec extension load), so either DB is auto-created.

---

## 4. Data model — `mem_notes`

The central table. One row = one qualitative note.

| Column | Meaning |
|---|---|
| `id` | autoincrement PK |
| `scope` | namespace: `global`, `chat:<id>`, or `committee:<role>` |
| `tier` | `hot` (injected at session start) or `warm` (recalled on demand) — CHECK-constrained |
| `key` | optional upsert key; `UNIQUE(scope, key)` with NULLs non-colliding |
| `value` | the note text (qualitative only) |
| `importance` | author-set weight, default 1.0 |
| `hits` | recall counter — drives promotion and retention |
| `source` | provenance: `agent` \| `user` \| `auto` \| `committee` \| `legacy` |
| `created_at` / `updated_at` | timestamps (recency for decay) |
| `expires_at` | optional TTL (reserved) |

Supporting tables: `user_profile` (per-scope role/stack/prefs/goals), `session_digests`
(rolling checkpoints), `conv_turns` (COLD: every conversation turn), `playbooks` (named
tool procedures), `chats` (registry + SDK `session_id` for resume), `meta`, `token_usage`.
`meta` also holds the per-chat `last_turn_day:<chat_id|global>` marker that drives the
day-boundary session roll, and `last_rollup_month:<chat_id>` that guards the monthly
rollup (§15).

Search indexes (all three memory kinds are hybrid-searchable): `notes_fts` / `turns_fts`
/ `digests_fts` (FTS5 external-content, trigger-synced) and `mem_vec` / `turn_vec` /
`digest_vec` (sqlite-vec `vec0`, `float[768]`).

---

## 5. Scopes and isolation

A **scope** is the hard isolation boundary. Three families:

- `global` — durable facts true across all chats.
- `chat:<id>` — per-conversation memory for a specific Telegram chat.
- `committee:<role>` — one per committee agent (9 specialists + CIO), e.g.
  `committee:risk`, `committee:macro`, `committee:valuation`, `committee:cio`.

Isolation is enforced at query time. Conversational recall reads `scope` + `global`;
committee recall passes `include_global=False`, restricting hits **strictly** to that
agent's own scope — no global leakage, no cross-agent leakage. Conversation turns are
additionally filtered to the chat of the active scope.

---

## 6. Memory tiers (the temperature model)

- **HOT** (`tier='hot'`) — injected into the prompt at session start (the "what the agent
  knows before turn one" file). Bounded by a token budget.
- **WARM** (`tier='warm'`) — not injected; surfaced on demand by hybrid recall when relevant
  to the current query/symbol.
- **COLD** (`conv_turns`) — every conversation turn, never auto-injected but kept searchable,
  so nothing is truly lost even after context compaction or a session fork.

Alongside the tiers: **USER profile** (injected, USER.md-equivalent), **session digests**
(a qualitative summary written before a fork so a fresh thread can be seeded without the full
transcript — the digest prompt also asks for a trailing `Lessons:` line so mistakes/corrections
survive into the next session), and **playbooks** (reusable procedures whose steps reference
*tools*, never cached numbers — so a playbook never goes stale).

**Long-term recall over digests.** Only the *latest* digest is auto-injected (short-term
continuity ≈ yesterday), but **every** digest is also indexed (`digests_fts` + `digest_vec`)
and reachable on demand via `memory_search` — so the agent can answer "what did we conclude
last month?" by meaning, not just keywords. A **monthly rollup** (§15) consolidates a month of
daily digests into one durable HOT note, giving always-in-context month-level memory without
scanning every turn.

---

## 7. Write path

`memory.remember(value, key=None, scope='global', tier='warm', importance=1.0, source='agent')`:

1. Strip the text and run the **figures firewall** (§8) — reject financial figures.
2. Insert, or **upsert** when a `key` is supplied (`ON CONFLICT(scope,key) DO UPDATE`).
3. Index the text into `mem_vec` (semantic) — FTS stays in sync via table triggers.
4. If the scope now exceeds its cap, **evict** (§9).

Keyless inserts do **not** dedup (plain INSERT); this is why the committee's original
keyless `save_note` produced duplicate rows. The fix gives every committee note a
deterministic key (§11).

---

## 8. The figures firewall — three-layer defense

Financial numbers (prices, $ amounts, ratios, percentages) go stale and must be recomputed
from `transactions` / `prices`, never memorized. Enforcement is layered, with the cheap
deterministic check as the *contract* the smart layer must satisfy.

### Layer 1 — Prevention (prompt rule), free
`note_sanitizer.FIGURE_RULE` is appended to every committee agent's system prompt,
instructing the model to keep figures out of the `memory_note` field entirely. No keyword
list — the model judges "a number that goes stale" semantically.

### Layer 2 — LLM sanitize-and-salvage (`note_sanitizer.sanitize`)
For each note the agent does write:

- **Fast path:** if the deterministic check already says "clean", skip the LLM (no cost).
- Otherwise call an LLM (model `CIO_SANITIZER_MODEL`, default `claude-sonnet-4-6`) to
  **rewrite** the note, stripping stale figures while **preserving the qualitative insight**:
  `"AAPL's 141% ROE proves a moat"` → `"AAPL's exceptional profitability proves a moat"`.
- **Verify loop:** the rewrite is checked against the regex firewall. Still dirty → one
  stricter retry with the leak fed back → still dirty → **reject**.
- **Reject** (`None`) when nothing qualitative survives (an all-figure note).
- **Fail-safe:** model unavailable (offline / no key / budget) → return the *original* text
  and defer to Layer 3. A sanitizer hiccup never drops a note silently or breaks a run.

The `asker` and `audit` callbacks are **dependency-injected**, so the module is pure and
testable and carries no import cycle with the engine.

### Layer 3 — Regex firewall (`memory._looks_like_figure`), deterministic backstop
Runs inside `remember()`, so it covers **all** callers (including non-LLM/offline paths).
**Keyword-gated:** a number is blocked only when it sits near a figure keyword
(`price|value|p&l|roe|roa|margins|eps|ebitda|revenue|yield|multiple|p/e|cagr|fcf|…`) or is a
currency amount (`$230`). A bare number or percentage with no nearby keyword
(`"trim 50% on breakout"`) **passes** — the LLM now carries the semantic load, so the regex
stays precise (low false-positive) rather than aggressive.

Why keep the regex at all when an LLM is smarter? Because it is **deterministic** (unit-
testable: "always blocks figures"), **offline** (no network on the write path), and
**fail-safe** (catches what slips when the model ignores the prompt or is unavailable). The
LLM does the fuzzy transformation; the regex guarantees what actually lands in the DB.

---

## 9. Self-improving loop: promotion, eviction, decay

- **Hit counter:** every successful recall bumps `hits` and refreshes `updated_at`.
- **Promotion:** a WARM note recalled ≥ `CIO_PROMOTE_HITS` (default 3) times is auto-promoted
  to HOT, so it begins injecting at session start. Memory curates itself by usefulness.
- **Retention score:** `importance × (1 + ln(1 + hits)) × 0.5^(age_days / 30)` — a 30-day
  half-life on recency, amplified by importance and usage.
- **Eviction:** when a scope exceeds `CIO_MAX_NOTES` (default 500), the lowest-scoring WARM
  `agent`/`auto` notes are trimmed first. **HOT notes and `source='user'` notes are never
  evicted.** Deleting a note also drops its `mem_vec` row.

---

## 10. Recall

### Exact lookup
`memory.recall(key, scope)` — direct key hit, bumps the counter.

### Hybrid search (`recall.search`)
The differentiator. A query is run through two rankers and fused, over any subset of three
**kinds** — `note`, `turn`, `digest` (default `("note","turn")`; the agent's `memory_search`
tool passes all three):

- **Keyword:** FTS5 `bm25()` over `notes_fts` / `turns_fts` / `digests_fts`.
- **Semantic:** sqlite-vec KNN over `mem_vec` / `turn_vec` / `digest_vec` using a local
  embedding model.
- **Fusion:** Reciprocal Rank Fusion (RRF, `k=60`) merges the ranked id-lists per kind; top-k
  returned, best first. Each hit carries its `kind` so the caller can tell a note from a turn
  from a digest.

Notes are scoped (`scope` + `global`, or strictly `scope` when `include_global=False`);
turns **and digests** are limited to the chat of the active scope.

**Embedding model:** fastembed `BAAI/bge-base-en-v1.5`, **768-dim**, ONNX, cached under
`data/models/` so the agent is offline-stable after first download. `EMBED_DIM = 768` in
`db.py` is the single source of truth the `vec0` tables and `recall.py` agree on.

> **Doc-drift note (resolved):** the old "384 / bge-small" comments in `db.py` and the
> `recall.py` module docstring have been corrected to 768 / bge-base. `EMBED_DIM = 768` and
> `recall.MODEL_NAME = "BAAI/bge-base-en-v1.5"` are authoritative.

---

## 11. Committee per-agent memory (`agent_memory.py`)

A thin facade over MemCore for the investment committee, against `data/committee.db`.

- `scope_for(role_key)` → `"committee:<role>"`.
- `recall_block(role_key, symbol, budget=400)` — builds the agent's prompt-injection block:
  its HOT notes plus symbol-relevant WARM/COLD hits, **strictly its own scope**, each hit
  bumped to feed promotion, packed within `CIO_AGENT_MEM_BUDGET` tokens. Returns `''` on any
  error so a failure never breaks a committee run.
- `save_note(role_key, value, symbol, importance=1.0)` — the write path, now deduplicated two
  ways (§12), with the figures firewall enforced inside `remember()`.
- `reflect(role_key)` — promotes that agent's frequently-recalled WARM notes to HOT after a
  run.

### Deduplication
The keyless-insert bug let one agent store the same takeaway as many rows. Two guards now
prevent twins:

1. **Deterministic key** — `save_note` keys each note `f"{SYMBOL}:{sha1(value)}"`, so an
   identical re-save **upserts** onto the existing row via `UNIQUE(scope,key)`.
2. **Semantic dedup** — before inserting, `recall.nearest_in_scope(value, scope)` finds the
   closest existing note *in the same scope* by embedding L2 distance. Within
   `CIO_DEDUP_MAXDIST` (default 0.45 ≈ cosine 0.90) the note is treated as a paraphrase of
   the existing one — that note is **reinforced (bumped)** instead of inserting a twin. Best-
   effort: a recall hiccup never blocks the actual save.

For normalized bge vectors, `distance² ≈ 2(1 − cos)`, so 0.45 ≈ cosine 0.90. The KNN runs on
the vec table alone (proven pattern), then results are filtered to the scope — an out-of-
scope note can never collapse another agent's note.

---

## 12. Maintenance tooling (`cio/dedup_notes.py`)

A standalone, DRY-RUN-by-default cleanup CLI (`python -m cio.dedup_notes`):

- **EXACT** (offline, no model): merge rows with identical `(scope, value)`.
- **SEMANTIC** (`--semantic`, needs the model): union-find paraphrases within `--max-dist`
  per scope.
- **Merge rule:** keep one survivor (HOT > most hits > lowest id); survivor absorbs
  `SUM(hits)` + `MAX(importance)`, gets the best tier, and a deterministic `dedup:<hash>`
  key; losers and their `mem_vec` rows are deleted; `VACUUM`.
- **`--backfill-keys`:** give every keyless legacy note a stable `dedup:<hash>` key (no
  merging), so future identical-text saves upsert rather than duplicate. Idempotent, with a
  per-row `IntegrityError` skip on in-scope collision.

Safety: dry-run prints the plan and changes nothing; `--apply` writes. Operates on a
configurable `--db`. Validated against a copy before any live run.

**Applied to `data/committee.db` (2026-06-03):** 10 exact-twin rows collapsed (56 → 46
notes), then all 39 keyless legacy notes backfilled (`empty-key 39 → 0`). A backup is written
to `data/committee.db.bak` before destructive runs.

---

## 13. Observability — dashboard & capture

The localhost dev dashboard (`cio/dashboard/`, loopback-bound, optional `CIO_DASH_TOKEN`)
exposes:

- **`/memory`** — per-scope note tables (Tier · Key · Value · Hits · Imp · Source · Updated),
  one `<details>` per scope, across both DBs.
- **`/sanitizer`** — the figures-sanitizer **audit trail**: every `cleaned` (rewritten +
  stored) or `rejected` (dropped) decision, with the agent, symbol, the figures removed, the
  original text, and the stored rewrite. Backed by `sanitizer_log` (committee.db, bounded to
  the newest 1000 rows, never raises). The sanitizer feeds it via the injected `audit`
  callback; fast-path and model-unavailable cases are intentionally not logged (no figure
  action taken).

Capture is governed by `CIO_CAPTURE_LEVEL` (1–3, default 1): levels **1 and 3** prune old
committee runs (keeping the newest `CIO_TRANSCRIPT_KEEP_RUNS`); level **2** keeps everything;
levels **1 and 2** record Telegram history while level **3** is committee-only (drops Telegram
turns). So: 1 = prune + keep Telegram, 2 = keep everything, 3 = prune + no Telegram.

---

## 14. Configuration reference

| Env var | Default | Effect |
|---|---|---|
| `CIO_MAX_NOTES` | 500 | per-scope note cap before eviction |
| `CIO_PROMOTE_HITS` | 3 | recalls needed to auto-promote WARM → HOT |
| `CIO_AGENT_MEM_BUDGET` | 400 | token budget for a committee agent's injected memory |
| `CIO_DEDUP_MAXDIST` | 0.45 | semantic-dedup L2 threshold (0 disables; key dedup stays) |
| `CIO_COMMITTEE_DB` | `data/committee.db` | committee memory DB path |
| `CIO_SANITIZER_MODEL` | `claude-sonnet-4-6` | model for the LLM figures-sanitizer |
| `CIO_SANITIZER_SERVICE` | `claude` | backend service for the sanitizer call |
| `CIO_CAPTURE_LEVEL` | 1 | dashboard capture scope/retention (1–3) |
| `DEFAULT_BUDGET` (context.py) | 1000 | token budget for the session-start memory block |
| half-life | 30 days | recency decay in the retention score |
| RRF `k` | 60 | rank-fusion constant |
| `EMBED_DIM` | 768 | embedding dimension (bge-base-en-v1.5) |

(Legacy `CFO_*` fallbacks are read where present — see the rename conventions.)

---

## 15. Context injection (`cio/context.py`)

At every (re)connect — including each rolling-session fork — `compose_system_prompt` rebuilds
a bounded memory block: the operator profile + pinned/HOT notes (ranked by importance ×
recency) + the latest session digest, packed into `DEFAULT_BUDGET` (1000) tokens. Token
counting uses `tiktoken` (`cl100k_base`) as one consistent local estimator, with headroom so
estimator error cannot overflow the window. The budget governs the *injected block*, not the
whole prompt.

### Day-boundary session roll

A session digest fork is normally triggered by transcript growth (`ROLL_TURNS` / `ROLL_TOKENS`),
but that per-process counter resets on every restart, so a long-lived bot that bounces daily
could keep one SDK `session_id` (and one undivided thread) alive for many days. The agent's
`ask()` therefore also rolls on a **local-day boundary**: if the last persisted turn day differs
from today *and* a prior-day thread was resumed, it digests + reseeds **before** the turn. This
(a) guarantees the rolling digest actually persists even across daily restarts, and (b) keeps a
single thread from spanning days — otherwise the agent treats a multi-day thread as "this
conversation" and can mis-date an old mistake as today's. The boundary is detected via the
persisted `last_turn_day:<chat_id|global>` meta marker (`memory.get_last_turn_day` /
`set_last_turn_day`); prior days survive as the injected digest.

### Monthly rollup (digest-of-digests) — long-term memory

The day roll keeps the *latest* digest as short-term continuity, but a chain of daily digests
is not month-level memory (only the newest is injected; older ones are recall-only). So when a
day roll also crosses a **month boundary**, `ask()` calls `CIOAgent._monthly_rollup(prev_month)`
after the day checkpoint:

1. `memory.digests_in_month(chat_id, "YYYY-MM")` gathers that month's daily digests.
2. `_ROLLUP_PROMPT` consolidates them (on the freshly reseeded thread, digests fed in-prompt)
   into a short durable memo — standing decisions, preferences/strategy, recurring themes,
   lessons — figures excluded.
3. The memo is stored as a **HOT note** keyed `monthly_rollup:<YYYY-MM>` (`importance` 4.0,
   `source='auto'`), so it is **injected every session** *and* hybrid-searchable like any note.

Guarded once-per-month by the `last_rollup_month:<chat_id>` meta marker; best-effort (a failure
never breaks the turn) and figures-firewall-safe (a rollup that smuggles a figure is dropped,
not stored).

---

## 16. Guarantees & failure modes

- **Never breaks a turn.** Every memory/recall/sanitize/log call is wrapped best-effort;
  failures log and return a safe default (`''`, `None`, or the original text).
- **Offline read path.** Recall and injection need only the local DB + cached embedding
  model. Only Layers 1–2 of the firewall use the network, and both degrade to the
  deterministic Layer 3 when it is absent.
- **Deterministic acceptance.** Whatever the LLM produces, the regex firewall is the final
  gate on what enters the DB — so the invariant "no figures in memory" is testable.
- **No cross-scope leakage.** Enforced in every recall/dedup query.
- **Bounded growth.** Per-scope eviction + the sanitizer-log row cap.

---

## 17. Testing

- `tests/test_memcore.py` — firewall, scope isolation, injection budget, eviction,
  promotion, cold-boot continuity, rolling-session cadence, reflection loop, and
  **digest hybrid search** (a past digest found by meaning, scoped per chat). (Note: tests that
  exercise the write path require the fastembed model; they are skipped/failed in environments
  where it is not installed.)
- `tests/test_day_roll.py` — `last_turn_day` persistence round-trip, the day-boundary roll
  matrix (rolls on a new day with a resumed session; no roll same-day, no resumed session, or
  brand-new chat; persists today after each turn), and the **monthly rollup** (`digests_in_month`
  filtering; a month boundary writes a HOT `monthly_rollup:<YYYY-MM>` memo; the once-per-month
  guard).
- `tests/test_committee.py` — per-agent isolation, injection, promotion, agent-memory
  firewall.
- `tests/test_note_sanitizer.py` — all sanitizer outcomes (fast-path-no-call, salvage,
  all-figure reject, retry-then-accept, retry-exhausted-reject, unavailable/exception defer,
  unparseable reject) and the audit callback (fires on cleaned/rejected, silent otherwise),
  all with a fake injected `asker` (no network).
- `tests/test_dashboard.py` — route/view rendering including the new `/sanitizer` page.

---

## 18. Known limitations & future work

- **Monthly rollup cost & cadence:** one extra LLM call per chat per month (the digest-of-
  digests), on the first turn after a month boundary. Multi-month downtime rolls up only the
  single most-recent active month, not every skipped month. Acceptable; revisit if multi-month
  catch-up is ever needed.
- **Semantic-dedup threshold** (0.45) is a heuristic on normalized-bge L2 distance; it
  assumes the embeddings are L2-normalized (fastembed bge default). Worth periodic tuning
  against real merge/no-merge cases.
- **Sanitizer cost:** ~1 LLM call per non-clean note saved (0 on the fast path, 2 only on a
  dirty retry) — roughly +9 calls per committee run worst case. Acceptable per current policy.
- **Keyword-gated regex** can let a figure phrased with no nearby keyword through to storage
  if the LLM layer is also unavailable — an accepted trade for low false positives, mitigated
  by Layers 1–2 when online.
- **Backfilled `dedup:<hash>` keys** do not match the live `SYMBOL:<hash>` scheme, so a future
  identical-text save will not upsert onto a legacy row by key — but semantic dedup
  (distance ≈ 0) still collapses it. Cosmetic only.
