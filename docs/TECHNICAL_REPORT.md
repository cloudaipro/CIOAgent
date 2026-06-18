# CIO Agent — Technical Report

**AI Investment Committee Agent System (AICAS).** A personal investment agent for a
solo operator. Over Telegram it answers stock-portfolio questions, imports CSVs,
renders charts, fetches live quotes / runs 38 technical strategies, and — on demand —
convenes a **multi-agent investment committee** that produces an institutional-grade
research report. A scheduled **Watchlist Monitoring Agent** delivers a pre-market
briefing on the watchlist every trading morning. It runs 24/7 with a tiered,
self-improving memory layer (MemCore).

The conversational agent is built on `claude-agent-sdk` using **Claude Code Pro
authentication — no `ANTHROPIC_API_KEY`**. Embeddings and search are fully local. The
committee's agents are pluggable per-role across three backends — the **Claude
subscription**, **NVIDIA NIM** (OpenAI-compatible), and the **OpenAI API** — and every
agent runs a **named daily-token-budget fallback chain**, so a backend outage or budget
exhaustion degrades gracefully instead of silencing a role. The
final report is delivered as a **PDF**, with an on-request **Traditional Chinese** version.

- **Stack**: Python 3.12, `claude-agent-sdk`, `openai`, `python-telegram-bot`,
  `httpx` (NIM + Firecrawl web), `weasyprint` + `markdown` (PDF), pandas / numpy (`<2.3`),
  `pandas_ta` + a vendored TA engine, SQLite (+ `sqlite-vec`), `fastembed` (ONNX),
  `tiktoken`, APScheduler, `pandas_market_calendars` (NYSE trading-day calendar),
  matplotlib, `yfinance`, PyYAML.
- **Source of truth**: the `transactions` and `prices` tables. All portfolio figures
  are *derived* and recomputed, never cached.
- **Process model**: one long-lived asyncio process (the Telegram bot) that owns one
  `CIOAgent` (SDK session) per chat; the committee runs as an in-process pipeline.

> Heritage: the project was renamed from **CFOAgent → CIOAgent**. Env vars use the
> `CIO_*` namespace with a `CFO_*` fallback; the db defaults to `data/cio.db` but falls
> back to an existing `data/cfo.db`. The vendored TA engine under `cio/stock/engine/`
> retains a `cfo` token — that is the *Chande Forecast Oscillator* indicator, unrelated
> to the project name.

---

## 1. Component architecture

```mermaid
flowchart TD
    TG["Telegram"] <--> BOT["cio/bot.py<br/>handlers, access gate, /stop, single-flight, scheduler"]
    BOT --> AGENT["cio/agent.py<br/>CIOAgent (SDK) + 46 MCP tools"]
    BOT --> COMM["cio/committee/*<br/>investment committee pipeline"]
    BOT --> WMA["cio/watchlist_monitor/*<br/>pre-market watchlist briefing"]
    AGENT --> CTX["cio/context.py<br/>memory injection"]
    AGENT --> PORT["cio/portfolio.py<br/>average-cost engine"]
    AGENT --> CHARTS["cio/charts.py + stock/panel.py"]
    AGENT --> MEM["cio/memory.py<br/>MemCore store"]
    AGENT --> STK["cio/stock/*<br/>quotes, cache, 38 TA strategies"]
    AGENT --> WEB["cio/web.py<br/>web search + scrape"]
    MEM --> RECALL["cio/recall.py<br/>hybrid search + embeddings"]
    CTX --> MEM
    COMM --> STK
    COMM --> AMEM["committee/agent_memory.py<br/>per-agent MemCore"]
    COMM --> MODELS["committee/models.py + engine.ask_role<br/>named-chain router: every agent → 3-link fallback chain"]
    COMM --> PDF["committee/render_pdf.py + translate.py<br/>PDF + 繁體中文"]
    AMEM --> CDB[("data/committee.db<br/>WAL: per-agent memory + token_usage")]
    PORT --> DB[("cio/db.py<br/>data/cio.db")]
    MEM --> DB
    RECALL --> DB
    SCHED["cio/scheduler.py<br/>digest + price refresh + 06:00 WMA briefing"] --> PORT
    SCHED -. "trading-day gate" .-> TU["cio/timeutil.py<br/>is_trading_day (NYSE cal)"]
    SCHED --> WMA
    WMA --> STK
    WMA --> WEB
    WMA --> MODELS
    AGENT -. "Pro auth, subprocess" .-> CLAUDE["claude CLI / SDK"]
    WEB -. "httpx /v2" .-> FC["Firecrawl instance"]
    MODELS -. "Bearer key, httpx" .-> NIM["NVIDIA NIM API"]
    MODELS -. "openai SDK" .-> OAI["OpenAI API"]
```

| Module | Responsibility |
|---|---|
| `bot.py` | Telegram I/O; **access-control gate**; routes text/photo/CSV to the per-chat agent; `/committee`, **`/briefing`**, `/subscribe`, **`/stop`**; **per-chat single-flight** (block=False long handlers); boot reindex, scheduler start, eager pre-warm |
| `agent.py` | `CIOAgent` wraps one SDK session; 46 in-process MCP tools (incl. `web_search`/`web_scrape`/`watchlist_prices` and 4 harness checks); rolling sessions, PreCompact hook, nudge, reflection loop |
| `web.py` | Firecrawl-backed `search` / `scrape` (async `httpx` → `/v2`); output-capped, offline-safe; powers the agent's web tools |
| `harness/` | Deterministic, zero-LLM check layer (§12): trade-plan consistency (V1), fetch-before-cite (V2), event-study distribution (V3), plus a self-authoring skill gate (`registry`/`store`/`admin`). Exposes 4 agent tools |
| `context.py` | Assembles the injected "hot" memory block within a token budget (`build_memory_block` chat-scoped, `build_scope_block` arbitrary-scoped) |
| `recall.py` | fastembed embeddings + `sqlite-vec` ANN + FTS5, fused with RRF; strict-scope recall (`include_global`) |
| `memory.py` | Tiered note store, profile, digests, playbooks, eviction, chat registry, **figures firewall** |
| `portfolio.py` | Average-cost basis, positions, realized P&L, summary; idempotent CSV ingest; live-price refresh |
| `charts.py` / `stock/panel.py` | Allocation pie / P&L bar PNGs; one-stop single-stock panel image |
| `scheduler.py` | APScheduler daily digest + EOD price-refresh + **06:00 watchlist briefing** (DB-direct, idempotent, reboot catch-up); WMA job is **trading-day gated** via `timeutil.is_trading_day` |
| `timeutil.py` | Local-TZ helpers + **`is_trading_day`** — Nasdaq trading-day check via the NYSE calendar (`pandas_market_calendars`), weekday fallback if the lib is absent |
| `watchlist_monitor/*` | Watchlist Monitoring Agent (WMA) — per-security overnight assessment + consolidated morning briefing (see §7) |
| `db.py` | SQLite schema, `sqlite-vec` loader, dim-migration, legacy migration; self-initializes any db path |
| `stock/data.py` | yfinance fetch + per-symbol cache (**sanitized paths**) + fundamentals (incl. **forward P/E**) + symbol normalization |
| `stock/engine/**` | Vendored TA engine: 38 strategies + indicators (pandas 3 / numpy 2 refactor) |
| `committee/*` | The investment committee (see §6) |

---

## 2. Data model

Two SQLite files. **`cio.db`** holds the portfolio + conversational MemCore.
**`committee.db`** holds the committee's per-agent memory (WAL mode), kept separate so
the agents' accruing notes + 768-dim vectors never bloat the portfolio db. The
**figures firewall** keeps monetary numbers out of every memory domain.

```mermaid
erDiagram
    transactions ||--o{ prices : "valued by (symbol)"
    watchlists ||--o{ watchlist_items : "watchlist_id"
    mem_notes ||--o| mem_vec : "note_id"
    mem_notes ||--o| notes_fts : "rowid"
    conv_turns ||--o| turn_vec : "turn_id"
    chats ||--o{ session_digests : "chat_id"
    chats ||--o{ conv_turns : "chat_id"
    transactions { int id PK }
    prices { text symbol PK }
    watchlists { int id PK }
    watchlist_items { int watchlist_id FK }
    mem_notes { int id PK }
    user_profile { text scope PK }
    playbooks { int id PK }
    meta { text key PK }
```

**Financial domain (figures — source of truth, `cio.db`):**
- `transactions` — every BUY/SELL/DIV; positions and P&L derive from this.
- `prices` — latest close per symbol (manual or refreshed).
- `imported_files` — sha256 of each ingested CSV (idempotency ledger).
- `watchlists` / `watchlist_items` — named symbol lists (one `is_active` at a time) and
  their members. `watchlist_items.position` holds the drag-to-reorder display order
  (`ORDER BY position, symbol`); items are not financial figures, so they live here as
  plain membership. Single-active is enforced in `watchlist.set_active()` (clears all
  other rows in one txn — SQLite can't express "at most one active"); every list is
  seeded with the NASDAQ Composite `^IXIC`, which `remove_symbol` refuses to drop. The
  `position` column is back-filled on existing DBs by an `ALTER TABLE` migration in
  `db.connect()` (idempotent; `CREATE TABLE IF NOT EXISTS` can't add a column).

**MemCore domain (qualitative — never figures):**
- `mem_notes` — tiered notes (`scope`, `tier` hot/warm, `importance`, `hits`, `source`).
- `user_profile`, `session_digests`, `conv_turns`, `playbooks`.
- `notes_fts` / `turns_fts` — FTS5 keyword indexes (trigger-synced).
- `mem_vec` / `turn_vec` — `sqlite-vec` `vec0` tables, `float[768]` embeddings.
- `conv_turns` — COLD store of every Telegram user/assistant turn (`bot._run` →
  `memory.log_turn`); feeds hybrid recall and the dev dashboard (§8). Gated by capture level.
- In **`committee.db`**: the same `mem_notes` / `mem_vec` / `notes_fts` tables, scoped
  `committee:{role}` — one logical namespace per agent (see §6.6) — plus `token_usage`
  (`service`, `day`, `tokens`) for the CIO fallback chain's daily budget (§6.4), and
  `committee_transcript` (every LLM call's sent prompt + returned text, grouped by `run_id`)
  for the dev dashboard (§8).

**Runtime:** `chats` (per-chat SDK `session_id` + digest/briefing subscription flag),
`meta` (`embed_dim`, migration flags, and the per-day idempotency stamps
`last_digest_date` / `last_wma_date` so a same-day reboot never re-sends).

---

## 3. Memory & context (MemCore)

Three tiers by access pattern. The same machinery serves the conversational agent
(scopes `global` / `chat:{id}`) and each committee agent (scope `committee:{role}`).

```mermaid
flowchart LR
    subgraph HOT["HOT — injected at session start"]
        P["user_profile"]
        HN["mem_notes tier=hot"]
        PB["playbook names"]
        DG["latest session_digest"]
    end
    subgraph WARM["WARM — recalled on demand"]
        WN["mem_notes tier=warm"]
    end
    subgraph COLD["COLD — hybrid-searchable"]
        CT["conv_turns / session_digests"]
    end
    HOT --> INJ["context.build_*_block<br/>(tiktoken budget)"]
    WARM --> SRCH["recall.search (RRF)"]
    COLD --> SRCH
```

### 3.1 Write path & figures firewall
`memory.remember(value, scope, tier, source)` rejects any value containing a currency
amount (`$123`) or a number adjacent to a valuation keyword (`worth`, `price`, `P&L`,
…) via `_guard_figures` → `FiguresFirewallError`. A qualitative note is upserted (FTS5
synced by trigger), embedded into `mem_vec`, and eviction runs if the scope exceeds the
cap. **A number can never become stale "memory."**

### 3.2 Injection at session start
`context.build_memory_block` packs **profile → hot notes (importance × recency) →
playbook names → latest digest** into a `tiktoken` budget (`DEFAULT_BUDGET = 1000`,
hard bound). `build_scope_block(scope, budget)` does the same for a single arbitrary
scope (no global, no chat) — the committee uses it for per-agent injection.

### 3.3 Hybrid recall (RRF)
`recall.search(query, k, scope, kinds, include_global)` fuses a `sqlite-vec` KNN over
768-d embeddings with FTS5 bm25 via Reciprocal Rank Fusion (`K = 60`). `include_global`
(default True) lets committee callers pass **False** for strict per-agent isolation.

### 3.4 Bounding for 24/7
- **Eviction**: when a scope exceeds `MAX_NOTES_PER_SCOPE` (500), the lowest-scoring
  warm, non-user notes drop (`importance × (1+log1p(hits)) × 0.5^(age/30d)`); vectors
  removed in sync. Hot and user notes are never evicted.
- **Rolling sessions** (`CIOAgent._checkpoint`): bound transcript growth (digest +
  reseed; digest written *before* the fork, so a crash mid-fork loses nothing).

### 3.5 Durability & self-improvement
PreCompact hook + periodic nudge + deterministic auto-capture persist facts across
lossy boundaries. At each checkpoint: **auto-promote** warm notes with `hits ≥
PROMOTE_HITS` (3) to hot; **auto-distill** a repeatable procedure into a (figure-free)
playbook.

---

## 4. Stock subsystem (`cio/stock/`)

- **`data.py`** — `load_or_download_stock_data` fetches OHLCV from yfinance and caches
  one joblib pickle per symbol under `data/stock_cache/`. **Cache paths are sanitized**
  (`safe_symbol` + realpath containment) so a hostile ticker cannot traverse the dir or
  name an arbitrary pickle (see §9). `fundamentals()` pulls PE/**forward-PE**/PB/yield/
  EPS/ROE/margin/market-cap/52-week/quarterly-revenue + `quoteType` (ETF detection).
  `normalize_symbol` resolves a bare 4-digit code to `.TW`/`.TWO`. `latest_quote()`
  returns the latest close + OHLC/volume **and** the day change (`prev_close`, `change`,
  `change_pct`, vs the prior session's close) — used by the `/watchlist` quote-board.
- **`engine/`** — a vendored strategy engine: 38 technical strategies + indicators,
  refactored for pandas 3 / numpy 2. Exposed via the `cio.stock` facade
  (`list_strategies`, `run_strategy`).
- **`panel.py`** — `render_panel()` composes a portrait PNG (price candles + MA, basic
  fundamentals incl. **forward P/E** (`預估P/E`), quarterly revenue, links) with CJK fonts
  and TW color convention.

Agent tools: `stock_quote`, `stock_history`, `list_stock_strategies`,
`run_stock_strategy`, `refresh_prices`, `stock_panel`, `watchlist_prices`.

**Watchlists (`cio/watchlist.py`)** — named symbol lists, exactly one active (see §2 for
the schema + invariants). `prices()` is the shared read path (active list by default,
fetched via `latest_quote`, injectable `quote_fn` for tests) used by both the
`watchlist_prices` agent tool (returns JSON) and the deterministic Telegram `/watchlist`
command. The command renders a **broker-style quote-board PNG** (`charts.watchlist_table`):
Instrument / Last / Change / Change % / Volume, green/red by sign with an up/down dot, the
NASDAQ index (`^IXIC` → `COMP`) highlighted on top, K/M/B volume; it falls back to a plain
text table if rendering fails. List membership order (drag-to-reorder) is the display
order in both the board and the tool output.

### 4.1 Web access (`cio/web.py`)
`web.search(query, limit)` and `web.scrape(url)` call a Firecrawl instance's `/v2/search`
and `/v2/scrape` over async `httpx` — base URL from `CIO_FIRECRAWL_URL` (falls back to
`FIRECRAWL_API_URL`, then a self-hosted `http://localhost:3002`; optional
`FIRECRAWL_API_KEY` bearer). Markdown is capped to `CIO_WEB_MAX_CHARS` (6000) to protect
the prompt budget; both are offline-safe (any failure → empty/error result, never raises).
Exposed as the agent tools **`web_search`** / **`web_scrape`** for qualitative context
(news, analyst pages, filings) — the system prompt forbids treating web text as
authoritative figures; numbers always come from the portfolio/stock tools.

---

## 5. Control flow — a message turn

```mermaid
sequenceDiagram
    participant U as Telegram user
    participant G as _gate (access control)
    participant B as bot.py
    participant A as CIOAgent.ask
    participant SDK as Claude SDK session
    participant T as MCP tools
    U->>G: update
    G-->>U: drop if chat not in CIO_ALLOWED_CHATS
    G->>B: (authorized) route text/photo/CSV/command
    B->>A: ask(prompt)
    A->>A: _ensure() connect/resume + inject memory, nudge every N turns
    A->>SDK: locked turn (client.query)
    SDK->>T: tool calls (portfolio / memory / stock / charts)
    T-->>SDK: results (figures from DB, charts to _PENDING)
    SDK-->>A: AssistantMessage + session_id
    A->>A: checkpoint if compaction_pending or over thresholds
    A-->>B: text + images
    B-->>U: reply (chunked) + photos
```

Turns are serialized per process by `_LOCK`; `_ACTIVE_SCOPE` is set under that lock so
the module-level MCP tools read/write the correct per-chat namespace. All durable state
survives reboot in SQLite; stale `session_id` degrades to a fresh session; CSV ingest is
idempotent so redelivered messages are safe.

### 5.1 Cancellation & per-chat single-flight (`/stop`)
Telegram delivers no deletion events, so a message that has reached the bot is already in
flight. To make that work cancellable, the long handlers (text/photo/document/`/committee`)
are registered **`block=False`** — the dispatcher keeps reading updates while one runs, so a
follow-up `/stop` is actually heard. `bot._running` maps `chat_id → {live handler tasks}`;
**`/stop`** cancels this chat's task(s), marking them in `_stopping` so a genuine shutdown
cancellation (not in the set) still propagates. A stopped turn is **not** logged and the
agent is reset (`close` + drop) so a half-consumed SDK stream can't corrupt the next turn;
the saved `session_id` rebuilds it lazily. `_try_acquire` enforces **per-chat single-flight**:
it claims the chat's one slot synchronously (no `await` between the busy-check and register,
so two back-to-back `block=False` handlers can't both win); a second message while one runs is
**rejected** with a notice pointing at `/stop`, rather than racing the first on the same agent.
`/stop` is exempt and frees the slot on cancel. **Caveat**: only *pending* steps halt — side
effects already committed (DB writes, spent model/NIM credits) before the stop are permanent.

---

## 6. Investment committee (`cio/committee/`)

`/committee SYMBOL [zh]` (Telegram), a plain-language ask in chat ("convene the committee
on META" → the agent's `run_committee` tool), or `python -m cio.committee SYMBOL [zh]`
(CLI) runs a simulated buy-side process and delivers a 14-section **PDF** report (or `.md`
on render failure) to `data/reports/`; add `zh` for a **Traditional Chinese** version (§6.7).

All three entry points funnel through one helper, **`delivery.produce_report(symbol, lang,
reports_dir, source)`** (`cio/committee/delivery.py`): run → translate → render PDF (md
fallback) → summary, returning a `CommitteeArtifact`. The `source` arg (`command` /
`chat` / `cli`) tags the run for the dashboard (§8). The chat agent is told in its system
prompt **never to fabricate or simulate a committee** — it must call the tool so the real
subsystem produces the verdict; it confirms the symbol first since this is the one
cost-bearing tool (~10-20 model calls).

```mermaid
flowchart TD
    BD["bundle.gather_bundle<br/>price + fundamentals (incl. forward P/E) + 38 TA + ETF flag"] --> SP
    subgraph SP["Round 1 — 9 specialists (parallel)"]
        M[market]; MAC[macro]; E[equity]; I[industry]; V[valuation]
        Q[quant]; ETF[etf*]; R[risk]; C[catalyst]
    end
    SP --> DEB["Round 2-3 — debate<br/>bear vs bull + risk vs valuation,<br/>then revised votes"]
    DEB --> CON["moderator consensus<br/>+ deterministic vote tally"]
    CON --> CIO["CIO final decision<br/>(Strong Buy … Strong Sell, scenarios,<br/>macro/geopolitical risk scores)"]
    CIO --> REP["report.build_report<br/>14 sections + §11 confidence band"]
```

### 6.1 Pipeline (`engine.py`)
`gather_bundle` → **Round 1** specialists (each emits role fields + `vote/confidence/
reason`; the ETF agent runs only for ETFs) → **debate** (`debate.py`, §6.2) → moderator
consensus + a deterministic Python vote tally → **CIO** final decision (5-band rating,
risk rating, horizon, bull/base/bear scenarios with probability + price target) →
report. `run_committee` never raises; missing data degrades to a clean "no data" result.
`format_bundle` surfaces `FWD_PE` (forward P/E) in the DATA block; the **valuation** and
**equity** specialists' prompts instruct them to weigh forward vs trailing P/E.

### 6.1b Geopolitical & Macro Intelligence specialist (`role_key="macro"`)
The 9th specialist provides early warning of **external** risks/opportunities. Its prompt
covers the macro backdrop (rates, inflation/CPI/PPI, growth/GDP/PMI, Treasury yields,
liquidity), geopolitics (conflicts, US-China, trade wars, sanctions, export controls),
commodities (crude / gas / gold / copper / lithium / rare earths), currencies (USD/EUR/JPY/
CNY/CAD), and supply-chain stress — then judges how they cut for **this** name's sector. It
must answer three questions in its reason (macro / geopolitical / commodity impact on the
thesis) and emits `macro_environment` (supportive|neutral|restrictive), `geopolitical_risk`/
`commodity_risk`/`currency_risk`/`regulatory_risk` (low|medium|high), `major_events`, and
`affected_sectors_positive`/`_negative`. The **moderator** weighs this external-risk debate,
and the **CIO** yaml adds `macro_alignment_score`, `geopolitical_risk_score`, and
`external_risk_adjustment` so the final call integrates fundamentals + valuation + macro +
geopolitical + risk. Cost: +2 calls/run (Round-1 + Round-3), within the ~20-call ceiling.

### 6.2 Debate (PRD §7.2)
`select_debate_pairs` deterministically picks the most-bearish-vs-most-bullish pair plus
a risk-vs-valuation pair (capped by `CIO_DEBATE_MAX_PAIRS`, default 2; skipped entirely
when all agents agree). Each pair runs a free-text challenge → response; then **all**
specialists may revise their vote given the transcript. Consensus, tally and CIO run on
the **revised** votes. Private agent memory (`memory_note`) is stripped from the raw
text so it cannot leak into the report or debate transcript.

### 6.3 Model services (`models.py`, `config/committee_models.yaml`)
Every call goes through `engine.ask_role(system, user, role_key)`, which resolves a
per-agent **named fallback chain** from the config file (optional; missing → built-in
defaults) and dispatches via `_dispatch` to one of three backends:
- **`_ask_claude`** — `claude-agent-sdk` one-shot (subscription, no key).
- **`_ask_nim`** — NVIDIA NIM, OpenAI-compatible `chat/completions` via `httpx`, Bearer
  `NVIDIA_API_KEY`.
- **`_ask_openai`** — OpenAI API via the `openai` SDK (`OPENAI_API_KEY`, default model
  `gpt-5.5-2026-04-23`).
Each backend returns `(text, tokens)` — real usage from the API (`usage.total_tokens`,
`AssistantMessage.usage`), estimated via `tiktoken` only when omitted. A missing key →
`("", 0)` so the run degrades gracefully. Shipped default chains: 9 specialists +
moderator → **`standard`** (OpenAI head → Claude Opus → NIM); CIO + WMA →
**`premium`** (Claude Opus head → OpenAI → NIM); translator → **`translation`**
(Claude Sonnet head → OpenAI mini → NIM). The resolved chain name and service/model are
logged per call (`agent <role> uses chain setting <name>; → <service>:<model>`).

**Output-token caps are configurable per backend** (priority **env > yaml > default**,
resolved by `models._int_setting`):
- OpenAI — value `CIO_OPENAI_MAX_TOKENS` / `openai.max_tokens` (2048), **and the param
  name** `CIO_OPENAI_TOKEN_PARAM` / `openai.token_param`: gpt-5.x requires
  `max_completion_tokens` (and only the default temperature, so no temperature override),
  older chat models want `max_tokens`.
- NIM — `CIO_NIM_MAX_TOKENS` / `nim.max_tokens` (2048).
- Claude — the agentic SDK has **no** plain output cap; its only token knob,
  `max_thinking_tokens`, is exposed via `CIO_CLAUDE_MAX_THINKING_TOKENS` /
  `claude.max_thinking_tokens` (unset → SDK default).

**Backend robustness.** `_ask_nim` parses tolerantly: reasoning models (minimax) may omit
`content` or return `null` and carry the answer in `reasoning_content`; when the cap is
spent on reasoning the response is `finish_reason="length"` with empty content. It recovers
`reasoning_content`, else returns `("", 0)` with a warning that names `finish_reason` (a cue
to raise `CIO_NIM_MAX_TOKENS`) — a malformed shape never raises.

### 6.4 Named fallback chains (all agents)
Every agent references a **named chain setting** defined in the top-level `chains:` section
of `config/committee_models.yaml`. `ask_role` walks the chain's ordered links, skipping any
whose **daily token use** (`usage.py`, per-service, `CIO_TZ`-local-day bucketed in
`committee.db`) is at or above its `daily_limit`, and falling through on an empty result
(error / missing key / rate-limit notice). Shipped named settings:

| Name | Links | Used by |
|---|---|---|
| `premium` | Claude Opus 200k → OpenAI gpt-5.5 200k → NIM kimi | cio, wma |
| `standard` | OpenAI gpt-5.5 200k → Claude Opus 200k → NIM kimi | all specialists, moderator, defaults |
| `translation` | Claude Sonnet → OpenAI gpt-5.4-mini → NIM kimi | translator |

Limits, models, order, and per-agent assignments are all editable in the yaml or from the
dashboard **Configure** page. Budget counters reset at `CIO_TZ` midnight. Budget accounting
is per-service, so an over-budget `openai` is skipped in every chain that references it.
`resolve_chain` has a 6-step fallback: named → legacy inline list → legacy
`{service,model}` agent → defaults chain → defaults legacy → hard-coded `[claude-opus-4-8]`;
it never raises and never returns an empty list.

### 6.5 Parallel execution
`CIO_PARALLEL` (default **on**) runs the independent groups — Round-1 specialists,
debate cross-exam pairs, Round-3 revisions — concurrently under a semaphore
(`CIO_MAX_CONCURRENCY`, default 8). Moderator consensus and CIO stay serial (they
consume prior outputs). `off` = fully sequential.

### 6.6 Per-agent isolated memory
Each of the 10 agents (9 specialists + CIO) has its own MemCore namespace
`committee:{role}` in **`committee.db`** (WAL), via `agent_memory.py`:
- **recall_block** injects the agent's *own* hot block + a symbol-scoped RRF recall
  (`include_global=False`), bumping hit counts to drive promotion.
- **save_note** persists the agent's `memory_note` through the figures firewall (a
  figure is rejected, logged, never stored).
- **reflect** promotes that scope's hot notes after each run.
Isolation is proven: a note in `committee:risk` never surfaces for `committee:valuation`,
`global`, or any `chat:*`.

### 6.7 Report output — PDF + Traditional Chinese
`build_report` produces the 14-section markdown; `render_pdf.markdown_to_pdf` converts it
(`markdown` → HTML → **WeasyPrint**) to a PDF with a single CJK CSS font stack
(`Noto Sans CJK TC`) that renders English and Chinese alike (fonts embedded/subset). When
the user adds a language token (`zh`/`tc`/`中文`/`繁中`), `translate.translate_report` first
translates the report into Traditional Chinese via `ask_role(role_key="translator")` (default
NIM minimax). Two safety nets: a failed translation keeps the English markdown, and a failed
PDF render falls back to sending the `.md`.

---

## 7. Watchlist Monitoring Agent (`cio/watchlist_monitor/`)

The first layer of the architecture: before market open it scans the watchlist,
produces a one-security assessment for each name, and renders a consolidated
**morning briefing** — far cheaper than the committee (one model call per security),
so the operator can triage what deserves a full committee run.

```mermaid
flowchart TD
    WL["watchlist.active()<br/>symbols"] --> MS
    subgraph MS["monitor_watchlist — per security (bounded parallel)"]
        BD["bundle.gather_bundle<br/>price + fundamentals + 38 TA"]
        NW["web.search<br/>overnight headlines (Firecrawl)"]
        BD --> ASK["engine.ask_role(role_key='wma')<br/>premium chain: Claude Opus → OpenAI → NIM"]
        NW --> ASK
        ASK --> ASS["normalized assessment<br/>(PRD §7: status, conviction, rec,<br/>events, risks, catalysts, thesis Δ,<br/>external-risk score + sensitivities)"]
    end
    WL --> GMS["global_macro_snapshot<br/>ONE shared macro/geopolitical<br/>headline read per run"]
    MS --> BR["report.build_briefing<br/>PRD §8: Global Market Intelligence,<br/>exec summary, macro/geopolitical alerts,<br/>exposure analysis, per-security review"]
    GMS --> BR
    BR --> OUT["PDF (+ 繁中 on request) → Telegram"]
```

### 7.1 Per-security assessment (`agent.py`)
`monitor_symbol` reuses the committee `gather_bundle` (price / fundamentals / TA) and
pulls overnight headlines via `cio.web.search` (offline-safe), then calls
`engine.ask_role(system, user, role_key="wma")` for a single fenced-yaml verdict that is
**normalized** into a fixed schema: `overall_status` (bullish/neutral/bearish),
`conviction_score` (0–100, clamped), `recommendation` (Buy/Add/Hold/Monitor/Reduce/Sell),
`analyst_sentiment`, `event_importance` (low/medium/high/critical),
`investment_thesis_change` (unchanged/positive/negative), the
positive/negative-event, new-risk and upcoming-catalyst lists, plus **external-risk
exposure** — `external_risk_score` (0–100) and `macro_sensitivity` /
`geopolitical_sensitivity` / `commodity_sensitivity` / `currency_sensitivity`
(low|medium|high) — all parsed in the same single call (no extra model calls). Invalid
model values fall back to safe defaults; a symbol with no data is **skipped without
spending a model call**. `monitor_watchlist` fans out across the active list under a
semaphore (`CIO_WMA_CONCURRENCY`, default 4), preserving input order; it never raises.

### 7.1b Global macro snapshot (`global_macro_snapshot`)
One **shared** call per briefing run (not per security, so the first layer stays cheap)
reads the morning's macro/geopolitical headlines via `cio.web.search` and emits a compact
top-of-briefing read: `market_sentiment` (risk-on|cautious|risk-off), `geopolitical_risk`
and `commodity_risk` (low|elevated|high), `key_events`, and a one-line `summary`
(`role_key="macro"`, prompt `MACRO_SNAPSHOT_SYSTEM`). It is offline-safe — no headlines or
an unparseable answer degrades to a neutral read. `build_briefing` / `briefing_summary` take
it as an **optional** `macro=` arg (backward compatible: `monitor_watchlist` still returns a
plain `list[dict]`), rendering the **Global Market Intelligence** section, a **Macro &
Geopolitical Alerts** block, and a **Watchlist Exposure Analysis** table (names ranked by
external-risk sensitivity — derived, no extra call). Callers `bot.py` / `scheduler.py` /
`__main__.py` fetch the snapshot and pass it; a snapshot failure leaves the briefing intact
minus the global section.

### 7.2 Committee escalation (PRD §11)
A high/critical `event_importance` or a negative `investment_thesis_change` sets an
`escalate` flag. v1 **flags** those names in the briefing (`/committee SYMBOL`) rather than
auto-running the committee — keeping the daily briefing cheap and respecting the committee's
per-run cost ceiling.

### 7.3 Model chain
The `wma` role uses the `premium` named chain, the same setting as the CIO: **Claude Opus**
`claude-opus-4-8` (daily 200k) → **OpenAI** `gpt-5.5-2026-04-23` (daily 200k) → **NVIDIA
NIM** `moonshotai/kimi-k2.6` (last resort), editable in `config/committee_models.yaml` or
from the dashboard Configure page. It runs through the identical `ask_role` budget/fallback
machinery as the committee (§6.4).

### 7.4 Scheduling — trading days only
`scheduler.watchlist_briefing` runs at **06:00 local** on stock days. APScheduler's
`day_of_week` (default `mon-fri`, `CIO_WMA_DAYS`) is a cheap pre-filter; the authoritative
gate is `timeutil.is_trading_day()`, which checks the **NYSE calendar**
(`pandas_market_calendars`, mirroring `AI4StockMarket/.../build_stocks_data.is_trading_day`)
so **Nasdaq holidays *and* weekends are skipped**; it degrades to a Mon–Fri check if the
calendar lib is unavailable. The run is **idempotent per day** (`meta.last_wma_date`, set
only after a successful send) with a boot-time catch-up if the machine was down at 06:00 on a
trading day. `CIO_WMA_HOUR=off` disables it. The briefing is pushed (PDF + short summary, `.md`
fallback) to every **subscribed** chat (`memory.subscribed_chats`, the same opt-in as the
daily digest; §8 lists subscribers).

### 7.5 Output — PDF + on-request 繁體中文 + manual invoke
`build_briefing` renders the PRD §8 markdown (executive summary with environment + bullish/
neutral/bearish counts + highest-priority pick, high/critical alerts, aggregated new risks,
upcoming catalysts, escalation list, then a priority-ordered per-security review);
`briefing_summary` is the short Telegram recap. The scheduled push is English; the manual
paths take a language token for a Traditional-Chinese briefing (reusing the committee
`translate_report` + OpenCC pipeline, §6.7): **Telegram** `/briefing [SYMBOL…] [zh]`
(no symbols = active watchlist) and **CLI** `python -m cio.watchlist_monitor [SYMBOL…] [zh]`.
Both are `/stop`-aware and under the per-chat single-flight lock (§5.1).

---

## 8. Developer dashboard (`cio/dashboard/`)

A localhost-only web view for the operator to **verify the agent is correct** — what
each model received, what it returned, token spend, and chat history. Read-only except
for `/watchlist`, the one write surface. Stdlib `http.server.ThreadingHTTPServer` (no web
framework, zero new deps), bound `127.0.0.1`. Launch: `python -m cio.dashboard`.

**Routes** (server-rendered HTML, no client JS except the one `/watchlist` drag script):
- `/usage` — tokens per backend per UTC day (`usage.recent` over `committee.db.token_usage`).
- `/telegram` — full conversation history (`memory.conv_history` over `cio.db.conv_turns`).
- `/subscribers` — chats opted in to the daily digest + 06:00 watchlist briefing
  (`memory.list_subscribers`: `chat_id` + subscribed-since), so the operator can see exactly
  who receives the scheduled pushes.
- `/watchlist` — the **write surface**: manage symbol lists (`cio/watchlist.py`).
  GET renders the manager (lists, the selected list's symbols, search, CSV-paste import);
  `do_POST` dispatches one `action` field (create/activate/rename/delete/add_symbol/
  remove_symbol/import_csv/reorder) → mutate → **303 redirect** back with a flash
  (Post/Redirect/Get, so a refresh can't resubmit). Same auth gate as GET. All
  mutations funnel through `cio/watchlist.py` (single source of truth for the
  single-active and NASDAQ-index invariants). Drag-to-reorder is one scoped vanilla
  script that writes the dragged symbol order into a hidden field and submits; with JS
  off the list still renders and every other action still works.
- `/committee` → `/committee/<run_id>` — each run lists its **Trigger** (`chat` / `command`
  / `cli`, from the transcript `source` column) and drills into every LLM call: content
  **sent** (system + user prompt) and content **returned**, per role, in order.
- `/memory` — **per-agent / per-chat memory contents** for debugging: `memory.list_scopes`
  enumerates every scope across **both** stores (`chat:*` / `global` in `cio.db`,
  `committee:<role>` in `committee.db`); each scope lists its notes with tier, key, value,
  hits, importance, source, and update time. Read-only, like the rest of the dashboard.
- `/skills` — **self-authored harness-skill approval queue** (§12). GET renders the
  governance table (status badge, trigger/spec, gate-aware action buttons); `do_POST`
  dispatches `verify`/`approve`/`activate`/`reject`/`retire` through the same
  `store.transition` / `admin._verify` gate as the CLI (approve refused before VERIFIED,
  activate before APPROVED), PRG back. The agent can only *propose*, never approve here.
- `/` — overview of all three.

**Capture funnels** (one per source, so nothing is missed and nothing is double-counted):
- Committee transcript: `engine.ask_role` is the single LLM entry point; a `_capture()`
  call sits beside each `usage.record`, tagged by a `_RUN_ID` ContextVar set at the top of
  `run_committee` (propagates into the parallel tasks). Each row also stores a `source`
  (trigger) from the `_RUN_SOURCE` ContextVar — set by `engine.set_run_source()` inside
  `delivery.produce_report`, so `command`/`chat`/`cli` runs are distinguishable; `_connect`
  ALTER-migrates DBs predating the column. Before this, committee prompts/responses were
  persisted nowhere — `_raw` on opinion dicts is not durable.
- Telegram turns: `bot._run` is the single funnel for text/photo/document turns →
  `memory.log_turn` writes the user + assistant rows to `conv_turns` (which also,
  finally, populates the COLD recall layer that was defined but unwritten).

**Capture level** — `CIO_CAPTURE_LEVEL` (default `1`, clamped 1–3, in `cio/devcapture.py`):

| Level | Committee transcript | Telegram history |
|------:|----------------------|------------------|
| 1 | full, pruned to last `CIO_TRANSCRIPT_KEEP_RUNS` (200) | on |
| 2 | full, kept forever | on |
| 3 | full, pruned | off (committee only) |

All capture is best-effort and never raises — a logging hiccup cannot break a chat
or a committee run. Pruning runs inline on insert (level 1/3). Auth: loopback bind
needs none; an optional `CIO_DASH_TOKEN` adds a `?token=…` gate that sets a session
cookie so nav stays clean.

---

## 9. Correctness & security guarantees

| Concern | Mechanism |
|---|---|
| Stale numbers | Figures firewall: memory/playbooks/agent-notes refuse monetary values; figures recomputed from `transactions`/`prices` |
| **Unauthorized access** | `CIO_ALLOWED_CHATS` access gate (`ApplicationHandlerStop`, group −1) drops updates from any non-allowlisted chat; unset logs a startup warning |
| **Path traversal / pickle sink** | `safe_symbol()` + realpath containment on the stock cache path; `_safe_name()` on report filenames — a hostile symbol cannot escape the cache/report dir or load an arbitrary pickle |
| Tool blast radius | `disallowed_tools`: Bash/Write/Edit and the **built-in** WebFetch/WebSearch off (conversational agent); web access is only via the controlled `web_search`/`web_scrape` MCP tools (read-only public web, output-capped). Committee agents run with `allowed_tools=[]` (no tools, reason over injected DATA only) |
| SQL injection | All queries parameterized; the one dynamic-column statement (`set_profile`) whitelists columns against `_PROFILE_FIELDS` |
| Secrets | `TELEGRAM_BOT_TOKEN` / `NVIDIA_API_KEY` / `OPENAI_API_KEY` from env only; `.env` gitignored; the key *name* (not value) is the only thing logged |
| Duplicate import on replay | Content-hash idempotency ledger, atomic with row inserts |
| Transcript blowup (24/7) | Rolling sessions (digest + reseed) + importance-decay eviction; dev-dashboard committee transcript pruned to `CIO_TRANSCRIPT_KEEP_RUNS` (level 1/3) |
| Dashboard exposure | Read-only except the `/watchlist` write page; binds `127.0.0.1` by default; optional `CIO_DASH_TOKEN` cookie gate guards GET **and** POST; warns if bound off-loopback without a token. Writes go only through `cio/watchlist.py` (parameterized, invariant-enforcing); POST mutations use PRG redirects |
| Reboot data loss | All durable state in SQLite; eager resume; graceful stale-session fallback |
| Dependency CVEs | `pip-audit`: no known-vulnerable dependencies at audit time |
| Auditability | Every note carries `source` (user/agent/auto/committee/legacy) + timestamps + `importance`/`hits` |

**Residual notes.** The stock cache uses joblib *pickle* files; the traversal guard
plus app-only writes bound the risk, but a switch to a non-executable cache format
(parquet/csv) would remove pickle deserialization entirely. NIM's `base_url` comes from
the local (trusted) config, not user input, so it is not an SSRF vector. The web tools'
`web_scrape` URL, by contrast, originates from the model and is fetched by the Firecrawl
instance — a potential SSRF reach into internal services; it is mitigated by pointing
`CIO_FIRECRAWL_URL` at an isolated/egress-filtered Firecrawl deployment and by the
allowlisted-chat gate that bounds who can drive the agent at all.

---

## 10. Configuration (environment)

All `CIO_*` vars honor a `CFO_*` fallback for back-compat.

| Var | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Bot token (required) |
| `CIO_ALLOWED_CHATS` | unset (open) | Comma-separated chat ids allowed to use the bot — **set this** |
| `NVIDIA_API_KEY` | — | NVIDIA NIM key (required for `service: nim` agents) |
| `OPENAI_API_KEY` | — | OpenAI key (`standard` chain head, `premium` chain 2nd link; absent → those links skipped) |
| `CIO_OPENAI_TOKEN_PARAM` | `max_completion_tokens` | OpenAI output-cap param name (`max_tokens` for older models); also `openai.token_param` in yaml |
| `CIO_OPENAI_MAX_TOKENS` | `2048` | OpenAI output-cap value; also `openai.max_tokens` in yaml |
| `CIO_NIM_MAX_TOKENS` | `2048` | NIM output-cap value; also `nim.max_tokens` in yaml |
| `CIO_CLAUDE_MAX_THINKING_TOKENS` | SDK default | Claude thinking-token budget (no plain output cap in the agentic SDK); also `claude.max_thinking_tokens` in yaml |
| `CIO_MODELS_CONFIG` | `config/committee_models.yaml` | Named chain settings + per-agent chain assignment + provider caps + daily limits |
| `CIO_PARALLEL` | `on` | Committee parallel vs sequential execution |
| `CIO_MAX_CONCURRENCY` | `8` | Parallel agent semaphore |
| `CIO_DEBATE` / `CIO_DEBATE_MAX_PAIRS` | `on` / `2` | Debate toggle + pair cap |
| `CIO_COMMITTEE_DB` | `data/committee.db` | Per-agent memory db path |
| `CIO_AGENT_MEM_BUDGET` | `400` | Token budget for per-agent memory injection |
| `CIO_MODEL` | SDK default | Pin the conversational / Claude default model |
| `CIO_DIGEST_HOUR` / `_MINUTE` | `8` / `0` | Daily digest time (`off` to disable) |
| `CIO_PRICE_REFRESH_HOUR` / `_MINUTE` | `17` / `0` | EOD price refresh (`off` to disable) |
| `CIO_WMA_HOUR` / `_MINUTE` | `6` / `0` | Watchlist briefing time, local TZ (`off` to disable) |
| `CIO_WMA_DAYS` | `mon-fri` | Cron `day_of_week` pre-filter (Nasdaq holidays/weekends always skipped) |
| `CIO_WMA_CONCURRENCY` | `4` | Securities assessed in parallel per briefing |
| `CIO_TZ` | `America/Vancouver` | Local timezone for schedule times + the token-usage day boundary |
| `CIO_ROLL_TURNS` / `_TOKENS` | `40` / `16000` | Rolling-session checkpoint thresholds |
| `CIO_NUDGE_TURNS` | `8` | Persist-reminder cadence |
| `CIO_MAX_NOTES` | `500` | Per-scope note cap (eviction) |
| `CIO_PROMOTE_HITS` | `3` | Hit count that promotes a warm note to hot |
| `CIO_STOCK_CACHE_DIR` | `data/stock_cache` | Stock OHLCV cache dir |
| `CIO_FIRECRAWL_URL` | `http://localhost:3002` | Firecrawl base URL for the web tools (falls back to `FIRECRAWL_API_URL`) |
| `FIRECRAWL_API_KEY` | unset | Firecrawl bearer token (cloud / authed instances; self-hosted needs none) |
| `CIO_WEB_MAX_CHARS` | `6000` | Per-result web-markdown cap (prompt-budget guard) |
| `CIO_WEB_TIMEOUT` | `45` | Web request timeout (seconds) |
| `CIO_CAPTURE_LEVEL` | `1` | Dev-dashboard capture scope 1–3 (§8) |
| `CIO_TRANSCRIPT_KEEP_RUNS` | `200` | Committee runs retained when pruning (level 1/3) |
| `CIO_DASH_HOST` / `CIO_DASH_PORT` | `127.0.0.1` / `8787` | Dashboard bind |
| `CIO_DASH_TOKEN` | unset | Optional dashboard shared-secret gate |

---

## 11. Verification

`pytest` — **791 offline tests** (no network, no LLM): MemCore (schema/`vec0`, figures
firewall, scope isolation, injection budget, hybrid recall, eviction, promotion, rolling
cadence, playbooks, cold-boot); stock subsystem + panel (incl. forward-P/E field & cell);
committee (bundle, 9 roles incl. **geopolitical & macro**, consensus/tally, 14-section
report incl. macro/geopolitical environment + external-risk matrix, confidence band, debate
pair-selection + rounds, **named-chain selection at each budget state for every role**,
parallel-vs-sequential peak, missing-key degrade); per-agent memory (isolation, firewall,
injection, promotion); **PDF/translation** (real WeasyPrint renders incl. a 繁體中文 doc,
lang parsing, translate no-op/fallback); **`/stop` + per-chat single-flight**
(`tests/test_bot_stop.py`: stopped turn not logged / no answer leaked / agent reset /
registries don't leak; normal turn unaffected; error + genuine-cancel cleanup; per-chat
isolation; 2nd-message reject while busy; accept after completion/stop); **WMA**
(`tests/test_watchlist_monitor.py`: yaml parse/normalize incl. external-risk
sensitivities, invalid-value fallbacks, no-data skip without a model call, escalation flag,
order-preserving fan-out, briefing sections + summary, **global macro snapshot**
parse/offline-safe + macro-aware briefing sections, `wma` chain resolution);
**named fallback-chain mechanism** (`tests/test_fallback_chain.py`: named lookup,
unknown-chain fallback to defaults, legacy inline chain, legacy `{service,model}` agent,
empty config, link normalization, budget walk, specialist degradation, partial-POST safety);
**dashboard Configure** chain editor round-trip, add/delete settings, agent reassignment,
legacy conversion, partial-POST regression; **trading-day calendar** (`tests/test_timeutil.py`:
NYSE-calendar membership, holiday exclusion, weekday fallback, type coercion); **dashboard**
(routes incl. `/subscribers`, escaping, 404, token gate); **security** (symbol sanitization,
cache-path containment, report-filename sanitizer, access gate).
Dependency scan via `pip-audit` (no known vulnerabilities). Committee live-verified
end-to-end against AAPL/NVDA (NIM specialists + Opus CIO) and rendered to a real
CJK-embedded PDF; web tools live-verified against a self-hosted Firecrawl.

Run: `.venv/bin/python -m pytest -q`

---

## 12. Harness layer (`cio/harness/`)

A deterministic, zero-LLM-cost, fail-closed check layer (same discipline as TIRF)
that converts user-found agent defects into durable automated checks instead of
one-off patches. Pure Python — no model call in any hot path; HTTP and DB are
injected so the core is offline-testable.

**Checks (each an agent tool via `tools.py` → `dispatch`, reusing `TOOL_SPECS`):**

- **V1 `consistency.py` → `harness_check_trade_plan`.** Cross-checks an emitted
  trade plan against the agent's own rule set before it leaves the agent. Rules:
  `R1_REL_WEAKNESS` (a pullback/limit entry that fills while the market is up/flat
  implies relative underperformance — the "Rule 2c" / Rule-6 self-contradiction;
  `detail.catalyst_check_required` is set at **any** severity, so a sub-threshold
  WARN still means "catalyst check required, not a valid naked entry"), `R2`
  coherence, `R3` R:R floor, `R4` earnings window, `R5` chase. `CheckResult.blocked`
  iff any BLOCK finding.
- **V2 `citation.py` → `harness_verify_citations`.** Fetch-before-cite: resolves
  every cited URL (injected resolver; stdlib `http_resolver` default) and fails
  closed on a dead link; only **live** sources count toward material-fact
  corroboration via `cio/data/source_policy.is_verified`. Extends the Evidence
  Integrity policy (see EVIDENCE-INTEGRITY.md) with the liveness check it lacked.
- **V3 `event_study.py` → `harness_event_study`.** Post-catalyst forward-return
  **distribution** (mean/median/quartiles/hit-rate) by event type — empirical when
  ≥8 historical analogs exist (`prices_provider_samples` off the `prices` table),
  else a labelled reference prior. Never a point forecast.

**Self-authoring loop (the Meta capability).** `registry.py` governs an in-process
admission gate `PROPOSED → VERIFIED → APPROVED → ACTIVE`; `store.py` persists
governance records to `data/harness_skills.json` (cfo.db is never migrated). The
agent's only write is `harness_propose_skill` (files a PROPOSED record — it cannot
verify/approve/activate). The owner drives the gate via `python -m cio.harness.admin`
or the dashboard **Skills** tab (§8). Invariants: `verify` runs an owner-committed
`(check, cases)` from `candidates.py` (or an explicit `--manual` attestation);
`approve` is refused unless VERIFIED; `activate` unless APPROVED; every transition
is audited. No model-authored code ever executes — the registry governs lifecycle,
it does not run unreviewed code.

**Why it sits beside the prompt rules.** The same defects are also covered by
stored behavioral memory (`mem_notes` `swing_entry_threesome_rule`,
`swing_screen_catalyst_rule`, `evidence_citation_rule`) and playbook
`swing_watchlist_reevaluation`. The harness is the deterministic enforcement of
those binary rules; V1's severity only escalates WARN→BLOCK and never downgrades a
relative-weakness finding to "ignore", keeping all three in agreement. Migrating
the prose rules to point at the tools is deferred until the harness is proven
(`HARNESS-RULE-POINTERS-DRAFT.md`).

**Tests:** `tests/test_harness.py` (rules, gate, MCHP/INTC defect replays,
store/admin gate, agent wiring) + dashboard skill-UI tests in `tests/test_dashboard.py`.
Design notes: `HARNESS-ENGINEERING-EVALUATION.md` (why), `HARNESS-ENGINEERING-SPEC.md`
(what), `HARNESS-TESTING-PLAN.md` (tests).

---

## Appendix — embedding & search internals

- **Model**: `BAAI/bge-base-en-v1.5`, 768-dim, ONNX via fastembed, cached under
  `data/models/` (gitignored; `recall.warmup()` fetches once → offline-stable).
- **Storage**: `sqlite-vec` `vec0` virtual tables, `float[768]`; vectors serialized with
  `sqlite_vec.serialize_float32`. The extension loads on every `connect`.
- **Dim migration**: `db._drop_stale_vec` detects an `embed_dim` change, drops the `vec0`
  tables (recreated by schema), flags `vec_reindex_needed`; `recall.reindex_all` re-embeds.
- **Fusion**: Reciprocal Rank Fusion, `K = 60`, over FTS and vector rank lists, merged and
  truncated to `k`.
- **Committee db**: `journal_mode=WAL` (set once per process), so several agents can write
  their notes concurrently without the default rollback-journal write-lock contention.
