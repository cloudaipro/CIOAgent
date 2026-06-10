# CIO Agent — Memory & Context Management

**Status:** current as of 2026-06-09 (incl. the long-term hardening pass: scoped
recall pre-filtering, TTL enforcement, hot cap/demotion, COLD-store retention,
vector self-healing, daily maintenance job)
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
| `expires_at` | optional TTL — set via `remember(ttl_days=…)`; expired notes are invisible to every read path (recall/list/search/injection) and purged by maintenance |

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

`memory.remember(value, key=None, scope='global', tier='warm', importance=1.0, source='agent', ttl_days=None)`:

1. Strip the text and run the **figures firewall** (§8) — reject financial figures.
2. Insert, or **upsert** when a `key` is supplied (`ON CONFLICT(scope,key) DO UPDATE`).
   `ttl_days` sets `expires_at` for time-bound notes (an event/plan that stops
   mattering); the agent's `remember` tool exposes it.
3. Index the text into `mem_vec` (semantic) — FTS stays in sync via table triggers.
   Indexing is **best-effort**: an embedding hiccup can no longer fail a save that
   is already committed (the note would look "failed" yet exist, so a retry would
   duplicate it). `recall.reindex_missing()` re-embeds any such gap during
   maintenance (§16), so the index self-heals.
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

## 9. Self-improving loop: promotion, demotion, eviction, decay

- **Hit counter:** every successful recall bumps `hits` and refreshes `updated_at`.
- **Promotion:** a WARM note recalled ≥ `CIO_PROMOTE_HITS` (default 3) times is auto-promoted
  to HOT, so it begins injecting at session start. Memory curates itself by usefulness.
- **Hot cap & demotion:** hot is a privilege, not a ratchet. Without a cap the bump→promote
  loop slowly turned every note hot, hot notes were never evicted, and a scope whose hot
  count exceeded `CIO_MAX_NOTES` could never be trimmed again (eviction starvation — it
  rescanned the scope on every write, forever). Now at most `CIO_MAX_HOT` (default 30)
  **non-user** hot notes stay hot per scope; the rest are demoted back to WARM by retention
  score (`enforce_hot_cap`, applied after every promotion and during maintenance). Demotion
  does not refresh `updated_at`, so a demoted note keeps decaying; `source='user'` hot notes
  are never demoted and don't count toward the cap. Old monthly rollups age out of the
  injected block this way while staying hybrid-searchable. (Same idea as OpenClaw's gated
  "dreaming" promotion: long-term memory stays high-signal.)
- **Retention score:** `importance × (1 + ln(1 + hits)) × 0.5^(age_days / 30)` — a 30-day
  half-life on recency, amplified by importance and usage.
- **Eviction:** when a scope exceeds `CIO_MAX_NOTES` (default 500), **expired notes go
  first**, then the lowest-scoring WARM `agent`/`auto` notes. **HOT notes, `source='user'`
  notes, and `monthly_rollup:*` notes are never evicted** (rollups are month-level memory
  even after demotion to warm). Deleting a note also drops its `mem_vec` row. If a scope
  is still over cap after a pass (too many protected notes), a warning is logged instead of
  silently rescanning forever.

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

**Scope pre-filtering (long-term correctness).** Candidates are restricted to the allowed
scopes *inside* the SQL of both rankers — the FTS join takes a `WHERE scope IN (…)` and the
KNN uses sqlite-vec's `note_id IN (subquery)` pre-filter (supported since sqlite-vec 0.1.x;
verified on 0.1.9). The previous design ranked across **all** scopes and filtered afterwards,
which starved recall as the DB filled: a fixed pool (~20) got dominated by other scopes'
rows, the target scope surfaced nothing, semantic dedup stopped collapsing twins, and the
resulting duplicates crowded the pool further (a compounding failure). The same pre-filter
applies to turns and digests (per chat) and to `nearest_in_scope` (dedup). Expired notes are
excluded at the same point.

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
| `CIO_MAX_HOT` | 30 | per-scope cap on non-user HOT notes (excess demotes to WARM) |
| `CIO_TURN_RETAIN_DAYS` | 365 | COLD-store retention window for `conv_turns` (0 = keep forever) |
| `CIO_MAX_TURNS` | 50000 | hard row cap on `conv_turns` (0 = no cap) |
| `CIO_MAINT_HOUR` / `CIO_MAINT_MINUTE` | 3 / 30 | daily maintenance slot (local; `off` disables) |
| `CIO_BACKUP` / `CIO_BACKUP_KEEP` / `CIO_BACKUP_DIR` | on / 7 / `data/backups` | nightly pre-maintenance DB snapshots |
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

## 16. Maintenance — the daily upkeep loop

`memory.maintain(db_path, force=False)` is the single upkeep entry point, run for **both**
DBs by the scheduler job `memory_maintenance` (default 03:30 local, plus a boot one-shot
catch-up; `CIO_MAINT_HOUR=off` disables). The job takes a **backup first** (`cio/backup.py`:
SQLite online-backup snapshot of each DB into `data/backups/<stem>.<date>.db`, newest
`CIO_BACKUP_KEEP`=7 kept, one per local day, WAL-safe against a live writer — maintenance
deletes by design, so every night gets a restore point before anything is removed; restore =
stop bot, copy snapshot over the live file, start bot). maintain() itself is guarded once per
local day via the `last_maintenance_day` meta marker, is best-effort throughout (never
raises), and does:

1. **TTL purge** — delete notes past `expires_at` (with their `mem_vec` rows).
2. **COLD-store retention** — `prune_turns`: drop `conv_turns` older than
   `CIO_TURN_RETAIN_DAYS`, then the oldest rows beyond `CIO_MAX_TURNS` (embeddings go with
   them; FTS via triggers). Without this the turn store — and its ~3 KB/row embeddings —
   grew without bound, and the brute-force vec0 KNN scan slowed linearly with it. Long-term
   context still survives in digests and monthly rollups; only raw turns are trimmed.
3. **Hot-cap demotion** — `enforce_hot_cap` per scope (§9).
4. **Vector self-healing** — `recall.reindex_missing`: embed any notes/turns/digests the
   write path failed to index (capped per run).
5. **Runtime invariant checks** (`cio/invariants.py`) — re-verify the design's promises
   against the REAL database, after the steps above: **I1** no recently-active session
   spans more than one local day (the misattribution class), **I2** vector↔row parity in
   both directions, **I3** per-scope note/hot caps hold, **I4** no expired note lingers,
   **I5** no figure-like content sits in `mem_notes`, **I6** the running process is not
   older than the repo HEAD (stale-process class — see below). Violations are logged,
   returned in the summary, persisted to `meta.last_invariant_violations`, and shown on
   the dashboard overview. Tests prove presence of behavior, never absence of bugs;
   the invariants make production a continuous test.

This mirrors what OpenClaw schedules as its "dreaming" sweep and Hermes runs as background
provider sync: curation happens off the conversational hot path, on a clock.

### Boot version stamp (stale-process detection)

`cio/version.py`: at startup the bot stamps the short git commit it booted from (plus boot
time and pid) into `meta`. The dashboard overview shows *running* vs *on-disk* version, and
invariant **I6** flags a mismatch as "restart needed". Motivation (2026-06-10 incident): a
fix was committed at 21:56 with all tests green, but the process serving overnight turns
predated the commit — Python doesn't hot-reload, so production ran pre-fix code for hours.
No test category can catch that; it is an operations property, so it is *observed* instead.
The `+dirty` suffix is ignored in the comparison (in-progress edits would flap the check).

## 17. Guarantees & failure modes

- **Never breaks a turn.** Every memory/recall/sanitize/log call is wrapped best-effort;
  failures log and return a safe default (`''`, `None`, or the original text).
- **Offline read path.** Recall and injection need only the local DB + cached embedding
  model. Only Layers 1–2 of the firewall use the network, and both degrade to the
  deterministic Layer 3 when it is absent.
- **Deterministic acceptance.** Whatever the LLM produces, the regex firewall is the final
  gate on what enters the DB — so the invariant "no figures in memory" is testable.
- **No cross-scope leakage.** Enforced in every recall/dedup query — now *pre-filtered in
  SQL*, so isolation also can't silently degrade into empty recall as the DB grows (§10).
- **Bounded growth.** Per-scope eviction (with hot cap so it can always make progress),
  COLD-store retention, TTL purge, and the sanitizer-log row cap.
- **Recoverable.** Every maintenance night starts with a consistent snapshot of both DBs
  (newest 7 kept), so any purge/prune/demotion — or a bug in one — is reversible for a week.

---

## 18. Testing

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
- `tests/test_memory_longterm.py` — the long-term hardening pass: scoped recall under
  cross-scope crowding (notes + dedup), TTL set/hide/purge, hot cap & demotion (user notes
  exempt; promotion respects the cap), eviction protections (rollups; expired-first),
  COLD-store retention by age and row count, save-survives-embedding-outage +
  `reindex_missing` self-heal, `maintain()` daily guard, the `connect()` init cache, and
  the `\b`-anchored `p/e` firewall keyword regression.
- `tests/test_temporal_simulation.py` — the **temporal simulation harness**: drives the
  real `ask()`/`_checkpoint()`/`_monthly_rollup()` code paths on a temp DB under a virtual
  clock, process restarts, and a fake LLM. Scenarios: 8 days of overnight restarts (the
  misattribution incident shape), same-day restart resumes, midnight crossing without
  restart, multi-day downtime rolls once, growth roll within a day, brand-new chat no-roll,
  month-boundary rollup. Asserts the promised property directly: no session ever serves
  two local days, and every boundary leaves a digest.
- `tests/test_invariants.py` — each invariant (I1–I6) tested by crafting the violating DB
  state directly (as a real bug would), plus the clean-DB-is-silent case and
  `maintain()` persisting violations for the dashboard.

---

## 19. Known limitations & future work

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
- **Turn pruning trades "nothing is ever lost" for boundedness.** Raw turns older than
  `CIO_TURN_RETAIN_DAYS` are gone from COLD recall; digests and monthly rollups carry the
  long-term signal. Set the var to 0 to keep every turn (at unbounded DB growth).
- **Hot-cap demotion is score-based, not semantic** — a rarely-bumped but genuinely durable
  auto note can drop out of injection once the cap is under pressure; it remains warm and
  searchable. Pin truly permanent facts as `source='user'` (the `remember` tool's
  `important` flag) to exempt them.
- **Backfilled `dedup:<hash>` keys** do not match the live `SYMBOL:<hash>` scheme, so a future
  identical-text save will not upsert onto a legacy row by key — but semantic dedup
  (distance ≈ 0) still collapses it. Cosmetic only.
