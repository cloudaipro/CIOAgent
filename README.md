# CIO Agent — AI Investment Committee

A personal CIO agent for a solo operator. Talk to it on **Telegram**; it answers
questions about your **stock portfolio**, imports CSVs, sends charts, fetches live
quotes / runs 38 technical strategies, **searches the live web** (Firecrawl), and —
on demand — convenes a **multi-agent investment committee** (`/committee SYMBOL`)
that researches a stock and returns an institutional-grade **PDF** report (with an
optional **Traditional Chinese** version). Every trading morning a scheduled
**Watchlist Monitoring Agent** (`/briefing`) delivers a pre-market briefing on your
watchlist.

The conversational agent runs on your **Claude Pro subscription** via `claude-agent-sdk`
— **no ANTHROPIC_API_KEY needed** (the `claude` CLI must be installed and logged in). The
committee's agents are pluggable across three model backends — **Claude**, **NVIDIA NIM**
(`minimax`), and **OpenAI** — and the final CIO decision runs a daily-token-budget
fallback chain across them.

## Architecture

```
Telegram  ──►  cio/bot.py        I/O + access gate; text, photos, CSV, /committee, /briefing, /watchlist
                  │
                  ├─► cio/agent.py      Claude agent (Pro auth) + 23 in-process MCP tools
                  │     cio/portfolio.py  pandas/SQLite: cost basis, P&L, valuation
                  │     cio/watchlist.py  named symbol lists (one active) + CSV import + prices
                  │     cio/charts.py     matplotlib → PNG (incl. /watchlist quote-board)
                  │     cio/memory.py     MemCore store: tiered notes, profile, eviction
                  │     cio/context.py    session-start memory injection (token-budgeted)
                  │     cio/recall.py     hybrid recall: FTS5 + fastembed + sqlite-vec (RRF)
                  │     cio/stock/*       yfinance quotes, cache, 38 TA strategies, panel
                  │     cio/web.py        Firecrawl-backed web search + scrape
                  │     cio/timeutil.py   local TZ + is_trading_day (NYSE calendar)
                  │     cio/scheduler.py  APScheduler: daily digest + EOD price refresh + 06:00 briefing
                  │     cio/db.py         SQLite (transactions = source of truth)
                  │
                  ├─► cio/committee/*   investment committee pipeline:
                  │     bundle → 8 specialists → debate → consensus → CIO → report
                  │     models.py  per-agent model router (claude | NIM | OpenAI) + CIO chain
                  │     agent_memory.py  isolated per-agent memory (committee.db, WAL) + dedup
                  │     note_sanitizer.py  LLM figures-sanitizer (salvage) + sanitizer_log.py audit
                  │     render_pdf.py / translate.py  PDF + 繁體中文
                  │
                  └─► cio/watchlist_monitor/*  pre-market Watchlist Monitoring Agent:
                        per-security assessment (bundle + web news + wma chain) → briefing PDF
```

- **Cost basis**: average-cost method. Positions & P&L are *derived* from the
  `transactions` table, so they're always consistent.
- **Prices**: set manually (`set AAPL 230`) or refreshed live from Yahoo Finance
  (on-demand `refresh_prices` tool + a scheduled EOD job).
- **Images out**: chart tools drop PNGs into an outbox the bot sends as photos.
- **Images in**: send a broker screenshot/receipt; the agent uses the Read tool
  (vision) to extract figures.
- **Web access**: `web_search` / `web_scrape` (via a Firecrawl instance) pull live
  news / analyst pages / filings for qualitative context. Figures still come only
  from the portfolio/stock tools — web text is never treated as authoritative numbers.
- **24/7 state**: durable memory, chat subscriptions, and per-chat SDK
  `session_id` live in SQLite, so a restart loses no data and each chat resumes
  its conversation thread. Financial figures are never stored as "memory" — they
  are always recomputed from `transactions`/`prices`.

## Memory & context (MemCore)

A tiered, auditable memory layer designed to be **≥ Hermes & OpenClaw** (see
[docs/COMPARISON.md](docs/COMPARISON.md)) — all local, **no API key**:

- **Injected at session start**: operator profile + pinned notes + the latest
  session digest are packed into the system prompt within a `tiktoken` budget, so
  the agent *knows* its context before the first message (and re-injects on every
  reconnect/fork).
- **Hybrid recall** (`memory_search`): FTS5 keyword + `fastembed` semantic vectors
  in `sqlite-vec`, fused with Reciprocal Rank Fusion — finds things phrased
  differently from how they were stored, across notes **and** past turns.
- **Bounded for 24/7**: rolling sessions checkpoint a digest then reseed a fresh
  thread (transcript can't grow forever); importance-decay eviction caps stored
  notes (hot/user notes protected).
- **Durability**: a PreCompact hook + periodic nudge + deterministic auto-capture
  ensure notable facts are saved before any lossy boundary.
- **Self-improving**: at each checkpoint the agent reflects — auto-distilling a
  reusable playbook when a repeatable procedure occurred, and promoting
  frequently-used notes into the injected set. Learned artifacts pass the figures
  firewall and are logged (`source=auto`).
- **Figures firewall** (three layers): stale numbers never enter memory — they're
  always recomputed from the ledger. (1) a **prompt rule** keeps figures out of notes
  at the source; (2) an **LLM sanitizer** rewrites a figure-laden note into a clean
  qualitative one, *salvaging the insight* (`"141% ROE proves the moat"` →
  `"exceptional profitability proves the moat"`), with its decisions logged for audit;
  (3) a deterministic **keyword-gated regex** is the offline backstop and the
  acceptance contract the rewrite must pass. Bare plan-style percentages
  (`"trim 50% on breakout"`) are allowed; stale fundamentals (`"27% margins"`) are not.

Full design: [docs/MEMORY-AND-CONTEXT.md](docs/MEMORY-AND-CONTEXT.md).

Tools the agent gets: `remember` / `recall` / `forget`, `memory_search` /
`memory_get`, `save_playbook` / `list_playbooks`.

## Investment committee (`/committee` or ask in chat)

`/committee SYMBOL` runs a simulated buy-side process and returns a 13-section **PDF**
research report. Add `zh` (`/committee AAPL zh`) for a **Traditional Chinese** version.

- **Two ways to convene**: the `/committee SYMBOL` slash command, or just **ask in plain
  language** ("convene the committee on META", "委員會討論 Meta"). Both route through the
  same `cio/committee/delivery.py` pipeline. In chat, the agent's `run_committee` tool
  fires the **real** committee (and sends the PDF) — it is told never to invent or
  simulate a verdict itself. The dashboard tags each run with its trigger (`chat` vs
  `/committee` vs `cli`). It's the one cost-bearing tool (~10-20 model calls); the agent
  confirms the symbol before firing.
- **Pipeline**: gather data (price / fundamentals incl. **forward P/E** / 38 TA signals) →
  **8 specialist agents** (market, equity, industry, valuation, quant, ETF, risk, catalyst)
  vote (valuation & equity weigh forward vs trailing P/E) →
  **debate** (bear-vs-bull + risk-vs-valuation cross-examination, then revised votes) →
  **moderator consensus** + deterministic tally → **CIO** final decision (Strong Buy …
  Strong Sell, with bull/base/bear scenarios). Specialists run in parallel.
- **Per-agent memory**: each of the 9 agents keeps its **own isolated** persistent memory
  (scope `committee:{role}` in `data/committee.db`), so they accrue private lessons
  without sharing context — all behind the same figures firewall. Notes are
  **deduplicated** on write: a deterministic key collapses identical takeaways and a
  semantic check (embedding distance) collapses paraphrases, so an agent re-deriving the
  same lesson reinforces one note instead of spawning twins.
- **Model services** (`config/committee_models.yaml`): each agent maps to `claude`, `nim`,
  or `openai`. The CIO runs a **fallback chain** — OpenAI `gpt-5.5-2026-04-23` (daily
  200k tokens) → Claude Opus (daily 200k) → NVIDIA NIM — switching automatically as each
  day's token budget is spent. Limits and models are editable in the config.
- **Output-token caps** are configurable per backend (env overrides yaml):
  `CIO_OPENAI_MAX_TOKENS` / `nim.max_tokens` `CIO_NIM_MAX_TOKENS` (default 2048), and the
  OpenAI param name `CIO_OPENAI_TOKEN_PARAM` (gpt-5.x = `max_completion_tokens`, older
  models = `max_tokens`). Claude's agentic SDK has no output cap — only
  `CIO_CLAUDE_MAX_THINKING_TOKENS` (thinking budget). If a NIM reasoning model returns
  empty (`finish_reason=length`), raise `CIO_NIM_MAX_TOKENS`.

## Watchlists (`/watchlist`)

Named lists of symbols to track, managed from the dashboard. **Multiple lists, exactly
one active** at a time (system-wide); the active list drives the Telegram price snapshot
and the `watchlist_prices` agent tool.

- **CRUD + search + import** in the dashboard `/watchlist` page: create / activate /
  rename / delete lists, add / remove symbols, **drag rows to reorder**, search by name
  or symbol, and **import a CSV** (paste a row of tickers — same format as
  `resources/portfolio2.csv` — or one symbol per line).
- **NASDAQ index floor**: every list is seeded with the NASDAQ Composite `^IXIC` and the
  index can't be removed, so each list always carries a market benchmark.
- **`/watchlist` in Telegram** renders a broker-style **quote-board image** —
  Instrument / Last / Change / Change % / Volume, green/red, with the index row (`COMP`)
  pinned on top. Deterministic (no model tokens); falls back to a text table if rendering
  fails. The order you set by dragging is the order shown here.
- Distinct from the **Watchlist Monitoring Agent** below: that's the scheduled analyst
  briefing; this is plain list management + live prices.

## Watchlist Monitoring Agent (`/briefing`)

A pre-market analyst that scans your watchlist and delivers a consolidated **morning
briefing** — cheaper than the committee (one model call per security), so you can triage
what deserves a deeper look.

- **What it does**: for each security it gathers price / fundamentals / 38 TA signals plus
  overnight web headlines (Firecrawl), then produces a normalized assessment — overall
  status, conviction (0–100), recommendation (Buy/Add/Hold/Monitor/Reduce/Sell), new risks,
  upcoming catalysts, and whether the thesis changed. The briefing aggregates these into an
  executive summary, high/critical **alerts**, risks, catalysts, and a per-security review.
- **Escalation**: names with a high/critical event or a negative thesis change are **flagged**
  for a full `/committee SYMBOL` run (it doesn't auto-run the committee — keeps the briefing cheap).
- **Schedule**: runs automatically at **06:00 local on trading days**. Holidays *and* weekends
  are skipped via the **NYSE calendar** (`pandas_market_calendars`). The briefing PDF + a short
  summary are pushed to every **subscribed** chat (same opt-in as the daily digest).
- **On demand**: `/briefing` (active watchlist) or `/briefing NVDA MU` (specific symbols);
  add `zh` for a **Traditional Chinese** briefing. CLI: `python -m cio.watchlist_monitor [SYMBOL…] [zh]`.
- **Model chain** (`config/committee_models.yaml`, role `wma`): OpenAI `gpt-5.5-2026-04-23`
  → Claude Opus → NVIDIA NIM `kimi-k2.6`, same daily-budget fallback as the CIO.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# one-time: download + cache the local embedding model (bge-base, ~210MB) so recall is offline-stable
.venv/bin/python -c "from cio import recall; print('embed dim', recall.warmup())"
cp .env.example .env          # paste your @BotFather token into TELEGRAM_BOT_TOKEN
.venv/bin/python -m cio.bot   # starts polling
```

In `.env`:
- `TELEGRAM_BOT_TOKEN` (required).
- **`CIO_ALLOWED_CHATS`** — comma-separated Telegram chat id(s) allowed to use the bot.
  **Set this**: unset means the bot answers anyone who finds it. Send `/start` to read
  your chat id (the bot echoes it), then add it here.
- `NVIDIA_API_KEY` — required for committee agents on NIM (`build.nvidia.com`).
- `OPENAI_API_KEY` — the CIO chain's first link; if absent the CIO falls back to Opus.
- `CIO_FIRECRAWL_URL` — web search/scrape endpoint (defaults to a self-hosted
  `http://localhost:3002`, no key); set `FIRECRAWL_API_KEY` for Firecrawl cloud.
  Tune with `CIO_WEB_MAX_CHARS` (per-result cap, 6000) and `CIO_WEB_TIMEOUT` (45s).

The committee report PDF needs WeasyPrint's system libraries (pango/cairo/gdk-pixbuf/
harfbuzz) — already present on most Linux desktops.

## Use (in Telegram)

- `how's my portfolio?` · `what's my top gainer?` · `show my allocation`
- `set AAPL 230` — manual price
- Upload a transactions CSV: `txn_date,symbol,action,quantity,price[,fees,currency,notes]`
  (`action` ∈ BUY/SELL/DIV)
- Send a photo of a broker screen/receipt to have it read
- `/watchlist` — broker-style quote-board image for your active watchlist (manage lists in the dashboard)
- `/committee SYMBOL` — full AI investment-committee PDF report (`/committee AAPL zh` for 繁體中文)
- `/briefing [SYMBOL…]` — pre-market watchlist briefing PDF (add `zh` for 繁體中文; auto-runs 06:00 on trading days)
- `/stop` — cancel whatever's currently running for you (a turn or a committee/briefing run)
- `/subscribe` — opt in to the daily portfolio digest **and** the 06:00 watchlist briefing · `/unsubscribe` — stop both

One request runs at a time per chat: a second message sent while the agent is still
working is refused with a notice (send `/stop` to cancel the first). Already-committed
work before a `/stop` — DB writes, spent model credits — cannot be rolled back.

## Run 24/7 (systemd)

For an always-on agent that auto-starts on boot and restarts on crash:

```bash
sudo cp deploy/cio-agent.service /etc/systemd/system/
# edit User=, WorkingDirectory=, HOME=, and ExecStart= paths if not skchen
sudo systemctl daemon-reload
sudo systemctl enable --now cio-agent     # start now + on every boot
journalctl -u cio-agent -f                # follow logs
```

After a reboot the service comes back automatically. Durable state (portfolio,
memory, subscriptions, per-chat session ids) lives in `data/cio.db` (falls back
to `data/cfo.db` if `cio.db` is absent — no data loss during migration), so
nothing is lost; each chat resumes its thread on its next message.

**Daily digest timing** — set in `.env` (local timezone):

```
CIO_DIGEST_HOUR=8        # hour 0–23, or "off" to disable
CIO_DIGEST_MINUTE=0
# CFO_DIGEST_HOUR / CFO_DIGEST_MINUTE still honored (back-compat)
```

If the machine is rebooting at digest time, a catch-up runs shortly after boot
(once per day, never double-sent).

**Watchlist briefing timing** — runs at 06:00 local on trading days (Nasdaq holidays
and weekends are skipped automatically):

```
CIO_WMA_HOUR=6           # hour 0–23, or "off" to disable
CIO_WMA_MINUTE=0
CIO_WMA_DAYS=mon-fri     # cron day_of_week pre-filter; holidays still skipped via NYSE calendar
CIO_WMA_CONCURRENCY=4    # securities assessed in parallel
CIO_TZ=America/Vancouver # local timezone for all schedule times
```

## Test without Telegram

```bash
.venv/bin/python -c "
import asyncio; from cio.agent import CIOAgent
async def m():
    a=CIOAgent()
    print((await a.ask('summarize my portfolio'))[0]); await a.close()
asyncio.run(m())"
```

Sample data: `data/sample_transactions.csv`.

## Developer dashboard

A localhost-only web view to verify the agent is behaving correctly. Mostly read-only;
write surfaces are the **Watchlist** / **Portfolio** pages (manage lists, set prices,
import CSVs) and **delete** controls on the Telegram / Memory / Committee pages. Every
mutation POSTs then redirects (PRG), behind the same auth gate, and destructive deletes
ask for confirmation first.

```bash
.venv/bin/python -m cio.dashboard          # http://127.0.0.1:8787
```

Pages:

- **Token usage** — OpenAI / Claude / NIM tokens per service per UTC day (from `committee.db`).
- **Telegram** — conversation history **grouped by local day**, with a day selector at the
  top (click a day to view only it) and a per-day **delete** button.
- **Subscribers** — chats opted in to the daily digest + 06:00 watchlist briefing
  (chat id + subscribed-since), so you can see exactly who receives the scheduled pushes.
- **Watchlist** — manage symbol lists (create/activate/rename/delete/search, add/remove
  symbols, drag-to-reorder, CSV import). One scoped JS for drag; no-JS safe.
- **Committee** — every run, each with its **Trigger** (`chat` ask vs `/committee` slash
  vs `cli`), drills into every LLM call: the exact content **sent** (system + user prompt)
  and the content **returned**, per role, in order. Includes a **delete-all-runs** button.
- **Memory** — per-agent / per-chat memory contents for debugging: every scope across
  both stores (`chat:*` / `global` in `cio.db`, `committee:<role>` in `committee.db`)
  with each note's tier, key, value, hits, importance, source, and update time. Each store
  and each scope has a **delete** button.
- **Sanitizer** — audit trail of the figures-sanitizer: every note it rewrote (figures
  stripped) or rejected, with the agent, symbol, what was removed, and the stored result.

Capture is on by default. One knob, `CIO_CAPTURE_LEVEL` (default `1`), tunes scope:

| Level | Committee transcript | Telegram history |
|------:|----------------------|------------------|
| 1 | full, pruned to last `CIO_TRANSCRIPT_KEEP_RUNS` (200) | on |
| 2 | full, kept forever | on |
| 3 | full, pruned | off (committee only) |

Bind/auth: `CIO_DASH_HOST` / `CIO_DASH_PORT`, and an optional `CIO_DASH_TOKEN`
shared secret (append `?token=…` once; a session cookie carries it after). Keep
the bind on `127.0.0.1` — it serves your own financial data.

## Roadmap

- Accounting domain (ledgers, COGS, P&L) and inventory stock — share `db.py`.
- Richer committee data (macro/SEC/news feeds; currently yfinance + LLM reasoning).
- FIFO cost-basis option.
