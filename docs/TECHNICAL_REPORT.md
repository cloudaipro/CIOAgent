# CFO Agent — Technical Report

A personal CFO chat agent for a solo operator. It answers stock-portfolio
questions over Telegram, imports CSVs, renders charts, and runs 24/7 with a
tiered, self-improving memory layer (MemCore). It is built on
`claude-agent-sdk` using Claude Code Pro authentication — **no `ANTHROPIC_API_KEY`
and no external services** (embeddings and search are fully local).

- **Stack**: Python 3.11, `claude-agent-sdk`, `python-telegram-bot`, pandas,
  SQLite (+ `sqlite-vec`), `fastembed` (ONNX), `tiktoken`, APScheduler, matplotlib.
- **Source of truth**: the `transactions` and `prices` tables. All portfolio
  figures are *derived* and recomputed, never cached.
- **Process model**: one long-lived asyncio process (the Telegram bot) that owns
  one `CFOAgent` (SDK session) per chat.

---

## 1. Component architecture

```mermaid
flowchart TD
    TG["Telegram"] <--> BOT["cfo/bot.py<br/>handlers, prewarm, scheduler wiring"]
    BOT --> AGENT["cfo/agent.py<br/>CFOAgent (SDK client) + 14 MCP tools"]
    AGENT --> CTX["cfo/context.py<br/>session-start memory injection"]
    AGENT --> PORT["cfo/portfolio.py<br/>average-cost engine"]
    AGENT --> CHARTS["cfo/charts.py<br/>matplotlib PNGs"]
    AGENT --> MEM["cfo/memory.py<br/>MemCore store"]
    MEM --> RECALL["cfo/recall.py<br/>hybrid search + embeddings"]
    CTX --> MEM
    SCHED["cfo/scheduler.py<br/>daily digest"] --> PORT
    BOT --> SCHED
    PORT --> DB[("cfo/db.py<br/>SQLite: cfo.db")]
    MEM --> DB
    RECALL --> DB
    CHARTS --> PORT
    AGENT -. "Pro auth, subprocess" .-> CLAUDE["claude CLI / SDK"]
```

| Module | Responsibility |
|---|---|
| `bot.py` | Telegram I/O; routes text/photo/CSV to the per-chat agent; `/subscribe`; boot-time reindex, scheduler start, eager session pre-warm |
| `agent.py` | `CFOAgent` wraps one SDK session; defines 14 in-process MCP tools; rolling sessions, PreCompact hook, nudge, reflection loop |
| `context.py` | Assembles the injected "hot" memory block within a token budget |
| `recall.py` | fastembed embeddings + `sqlite-vec` ANN + FTS5, fused with RRF; (re)indexing |
| `memory.py` | Tiered note store, profile, digests, playbooks, eviction, chat registry, figures firewall |
| `portfolio.py` | Average-cost basis, positions, realized P&L, summary; idempotent CSV ingest |
| `charts.py` | Allocation pie / P&L bar PNGs |
| `scheduler.py` | APScheduler daily digest (DB-direct, idempotent, reboot catch-up) |
| `db.py` | One SQLite file; schema, `sqlite-vec` loader, dim-migration, legacy migration |

---

## 2. Data model

Two disjoint domains share one SQLite file. The **figures firewall** is the rule
that keeps them separate: monetary numbers live only in the financial domain.

```mermaid
erDiagram
    transactions ||--o{ prices : "valued by (symbol)"
    mem_notes ||--o| mem_vec : "note_id"
    mem_notes ||--o| notes_fts : "rowid"
    conv_turns ||--o| turn_vec : "turn_id"
    conv_turns ||--o| turns_fts : "rowid"
    chats ||--o{ session_digests : "chat_id"
    chats ||--o{ conv_turns : "chat_id"

    transactions { int id PK }
    prices { text symbol PK }
    mem_notes { int id PK }
    user_profile { text scope PK }
    session_digests { int id PK }
    conv_turns { int id PK }
    playbooks { int id PK }
    chats { int chat_id PK }
    meta { text key PK }
```

**Financial domain (figures — source of truth):**
- `transactions` — every BUY/SELL/DIV; positions and P&L derive from this.
- `prices` — latest manual close per symbol.
- `imported_files` — sha256 of each ingested CSV (idempotency ledger).

**MemCore domain (qualitative — never figures):**
- `mem_notes` — tiered notes (`scope`, `tier` hot/warm, `importance`, `hits`, `source`).
- `user_profile` — per-scope role/stack/prefs/goals (injected).
- `session_digests` — rolling-session checkpoints.
- `conv_turns` — full conversation history (cold, searchable).
- `playbooks` — distilled reusable procedures.
- `notes_fts` / `turns_fts` — FTS5 keyword indexes (kept in sync by triggers).
- `mem_vec` / `turn_vec` — `sqlite-vec` `vec0` tables, `float[768]` embeddings.

**Runtime:** `chats` (subscription + per-chat SDK `session_id`), `meta`
(bookkeeping: `embed_dim`, `vec_reindex_needed`, `last_digest_date`, migration flags).

---

## 3. Memory & context (MemCore)

Designed to be ≥ Hermes & OpenClaw (see [COMPARISON.md](COMPARISON.md)). Three
tiers by access pattern:

```mermaid
flowchart LR
    subgraph HOT["HOT — injected every session start"]
        P["user_profile"]
        HN["mem_notes tier=hot"]
        PB["playbook names"]
        DG["latest session_digest"]
    end
    subgraph WARM["WARM — recalled on demand"]
        WN["mem_notes tier=warm"]
    end
    subgraph COLD["COLD — hybrid-searchable"]
        CT["conv_turns (every turn)"]
        DGS["session_digests"]
    end
    HOT --> INJ["context.compose_system_prompt<br/>(tiktoken budget)"]
    WARM --> SRCH["memory_search (RRF)"]
    COLD --> SRCH
    INJ --> SP["SDK system prompt"]
```

### 3.1 Write path

```mermaid
sequenceDiagram
    participant A as agent (remember tool / auto-capture)
    participant M as memory.remember
    participant FW as figures firewall
    participant DB as mem_notes (+ FTS trigger)
    participant R as recall.index_note
    A->>M: value, key?, scope, tier, source
    M->>FW: _guard_figures(value)
    alt looks like a figure
        FW-->>A: FiguresFirewallError (use portfolio tools)
    else qualitative
        M->>DB: upsert row (FTS5 synced by trigger)
        M->>R: embed(value) -> mem_vec
        M->>M: if count(scope) > cap -> evict()
    end
```

A note is rejected if its value contains a currency amount (`$123`) or a number
adjacent to a valuation keyword (`worth`, `price`, `P&L`, `value`, …). This is the
guarantee no rival has: a number can never become stale "memory".

### 3.2 Injection at session start

On every `_make_client` (init, fresh-session fallback, and rolling-session fork),
`context.build_memory_block` packs **profile → hot notes (importance × recency) →
playbook names → latest digest** into a `tiktoken`-measured budget
(`DEFAULT_BUDGET = 1000`, hard bound — the joined block is re-measured at each add).
The block is appended to the base system prompt, so the agent *knows* its context
before turn one.

### 3.3 Hybrid recall (`memory_search`)

```mermaid
flowchart LR
    Q["query"] --> E["fastembed bge-base 768d"]
    Q --> F["FTS5 tokens (len>=3)"]
    E --> KNN["sqlite-vec KNN<br/>mem_vec / turn_vec"]
    F --> BM["FTS5 bm25<br/>notes_fts / turns_fts"]
    KNN --> RRF["Reciprocal Rank Fusion<br/>score = sum 1/(60+rank)"]
    BM --> RRF
    RRF --> TOPK["top-k notes + turns"]
```

The vector side finds matches phrased differently from how they were stored
(where keyword search alone misses); FTS catches exact terms. RRF merges both.
Both layers are required — there is no keyword-only degraded mode.

### 3.4 Bounding for 24/7

- **Eviction** (`memory.evict`): when a scope exceeds `MAX_NOTES_PER_SCOPE` (500),
  the lowest-scoring **warm, non-user** notes are dropped. Score =
  `importance × (1+log1p(hits)) × 0.5^(age_days/30)`. Hot and user notes are never
  evicted; vectors are removed in sync.
- **Rolling sessions** (`CFOAgent._checkpoint`): bounds transcript growth (see §4.2).

### 3.5 Durability (facts survive every lossy boundary)

- **PreCompact hook** (`_on_precompact`): the SDK fires it before lossy
  compaction; the agent flags a checkpoint so notable facts are durably digested.
- **Nudge**: every `NUDGE_TURNS` (8) the user prompt is augmented with a reminder
  to persist anything notable (cheap; no extra call).
- **Deterministic auto-capture**: `set_price`/`ingest` write `source=auto` event
  notes (event references, never numbers).

### 3.6 Self-improving reflection loop

At each checkpoint, while the session is still live:
- **Auto-promote**: warm notes with `hits ≥ PROMOTE_HITS` (3) become hot → injected
  next session (memory curates itself by usefulness).
- **Auto-distill**: the agent is asked whether a repeatable procedure occurred; a
  parseable `NAME:/STEPS:` reply is saved as a playbook (`source=auto`, figure-free).

---

## 4. Control flow

### 4.1 A message turn

```mermaid
sequenceDiagram
    participant U as Telegram user
    participant B as bot.py
    participant A as CFOAgent.ask
    participant Q as _run_query (under _LOCK)
    participant SDK as Claude SDK session
    participant T as MCP tools
    U->>B: text / photo / CSV
    B->>A: ask(prompt)
    A->>A: _ensure() (connect/resume, inject memory)
    A->>A: nudge every N turns
    A->>Q: locked turn, set _ACTIVE_SCOPE to chat scope
    Q->>SDK: client.query(prompt)
    SDK->>T: tool calls (portfolio / memory / charts)
    T-->>SDK: results (figures from DB, charts to _PENDING)
    SDK-->>Q: AssistantMessage (text) + session_id
    Q-->>A: (text, image paths)
    A->>A: bump counters, checkpoint if compaction_pending or over thresholds
    A-->>B: text + images
    B-->>U: reply (chunked) + photos
```

Turns are serialized per process by `_LOCK`; `_ACTIVE_SCOPE` is set under that lock
so the module-level MCP tools read/write the correct per-chat namespace.

### 4.2 Rolling-session checkpoint

```mermaid
sequenceDiagram
    participant A as CFOAgent._checkpoint
    participant SDK as current session
    participant M as memory
    A->>SDK: digest query (no figures)
    SDK-->>A: digest text
    A->>M: add_digest(...)
    Note over A,M: digest persisted BEFORE fork
    A->>M: promote_hot(scope) reflection
    A->>SDK: playbook distillation query
    SDK-->>A: NAME / STEPS or NONE
    A->>M: add_playbook if parsed (firewall-guarded)
    A->>A: reset counters, disconnect old session
    A->>A: _make_client(resume=None)
    Note over A: fresh thread re-injects digest + memory
    A->>SDK: reconnect
```

Because the digest is written **before** the fork, a crash mid-fork loses nothing.
Worst case is some un-digested conversational nuance — still recoverable from
`conv_turns` via search. Financial data is untouched (it is not in the transcript).

### 4.3 Reboot recovery

```mermaid
sequenceDiagram
    participant S as systemd (Restart=always)
    participant B as bot.main / _post_init
    participant DB as cfo.db (durable)
    S->>B: process start
    B->>DB: reindex embeddings if vec_reindex_needed
    B->>B: scheduler.start (daily digest + catch-up)
    B->>DB: all_chats()
    loop each known chat
        B->>B: CFOAgent(resume=session_id, chat_id).warm()
        Note over B,DB: resume transcript + inject memory (eager, no first-msg lag)
    end
```

All durable state (portfolio, notes, profile, digests, subscriptions, per-chat
`session_id`) survives in SQLite. Stale `session_id` degrades gracefully to a fresh
session. Redelivered Telegram messages are safe: CSV ingest is idempotent.

---

## 5. Financial data flow

```mermaid
flowchart LR
    CSV["CSV upload"] --> ING["portfolio.ingest_transactions_csv<br/>sha256 dedup + atomic txn"]
    ING --> TX[("transactions")]
    PRICE["set AAPL 230"] --> PR[("prices")]
    TX --> POS["positions / realized_pl / summary<br/>(average-cost, derived)"]
    PR --> POS
    POS --> ANS["agent answer / chart / digest"]
```

- **Average-cost**: a BUY blends into the running average; a SELL realizes P&L
  against it without changing the average; DIV adds to dividends.
- **Idempotent ingest**: identical CSV bytes are rejected (`DuplicateImport`); the
  rows and the hash commit in one transaction, so a crash rolls back both and a
  replay re-imports cleanly.
- Numbers are **always recomputed** here — never read from memory.

---

## 6. Correctness & security guarantees

| Concern | Mechanism |
|---|---|
| Stale numbers | Figures firewall: memory/playbooks refuse monetary values; figures recomputed from `transactions`/`prices` |
| Duplicate import on replay | Content-hash idempotency ledger, atomic with row inserts |
| Transcript blowup (24/7) | Rolling sessions (digest + reseed) + importance-decay eviction |
| Fact loss at compaction | PreCompact hook + nudge + auto-capture |
| Reboot data loss | All durable state in SQLite; eager resume; graceful stale-session fallback |
| Tool blast radius | `disallowed_tools`: Bash/Write/Edit/WebFetch/WebSearch off; only portfolio/memory/chart tools + Read |
| Auditability | Every note carries `source` (user/agent/auto/legacy) + timestamps + `importance`/`hits` |
| Offline / no key | Pro auth; embeddings/search fully local (fastembed + sqlite-vec) |

---

## 7. Configuration (environment)

| Var | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Bot token (required) |
| `CFO_MODEL` | SDK default | Pin the Claude model |
| `CFO_DIGEST_HOUR` / `_MINUTE` | `8` / `0` | Daily digest time (`off` to disable) |
| `CFO_ROLL_TURNS` / `_TOKENS` | `40` / `16000` | Rolling-session checkpoint thresholds |
| `CFO_NUDGE_TURNS` | `8` | Persist-reminder cadence |
| `CFO_MAX_NOTES` | `500` | Per-scope note cap (eviction) |
| `CFO_PROMOTE_HITS` | `3` | Hit count that promotes a warm note to hot |

---

## 8. Verification

`tests/test_memcore.py` — 13 checks, **offline** (`HF_HUB_OFFLINE=1`): schema +
`vec0`, figures firewall, scope isolation, injection budget (hard bound), hybrid
recall (vector hit where FTS misses), eviction + protection, rolling cadence,
playbooks, parser, promote-to-hot, reflection-loop wiring, cold-boot continuity,
offline embedding. Live-verified: rolling reseed, PreCompact-hooked connect, and
playbook distillation emitting a parseable procedure.

Run: `PYTHONPATH=. .venv/bin/python tests/test_memcore.py`

---

## Appendix — embedding & search internals

- **Model**: `BAAI/bge-base-en-v1.5`, 768-dim, ONNX via fastembed, cached under
  `data/models/` (gitignored; `recall.warmup()` fetches once → offline-stable).
- **Storage**: `sqlite-vec` `vec0` virtual tables, `float[768]`; vectors serialized
  with `sqlite_vec.serialize_float32`. The extension is loaded on every `connect`.
- **Dim migration**: `db._drop_stale_vec` detects an `embed_dim` change, drops the
  `vec0` tables (schema recreates them), and flags `vec_reindex_needed`;
  `recall.reindex_all` re-embeds all notes and turns.
- **Fusion**: Reciprocal Rank Fusion, `K = 60`, over the FTS and vector rank lists
  for notes and turns independently, then merged and truncated to `k`.
