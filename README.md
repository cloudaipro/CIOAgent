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
committee's agents are pluggable across three model backends — **Claude**, **NVIDIA NIM**,
and **OpenAI** — and every agent runs a **named daily-token-budget fallback chain** so any
backend outage or budget exhaustion degrades gracefully instead of silencing a role.

## Architecture

```
Telegram  ──►  cio/bot.py        I/O + access gate; text, photos, CSV, /committee, /briefing, /watchlist
                  │
                  ├─► cio/agent.py      Claude agent (Pro auth) + 41 in-process MCP tools
                  │     cio/portfolio.py  pandas/SQLite: cost basis, P&L, valuation
                  │     cio/watchlist.py  named symbol lists (one active) + CSV import + prices
                  │     cio/charts.py     matplotlib → PNG (incl. /watchlist quote-board)
                  │     cio/memory.py     MemCore store: tiered notes, profile, eviction
                  │     cio/context.py    session-start memory injection (token-budgeted)
                  │     cio/recall.py     hybrid recall: FTS5 + fastembed + sqlite-vec (RRF)
                  │     cio/stock/*       yfinance quotes, cache, 38 TA strategies, panel
                  │     cio/web.py        Firecrawl-backed web search + scrape
                  │     cio/timeutil.py   local TZ + is_trading_day (NYSE calendar)
                  │     cio/econ_calendar.py  high-impact econ-event store + deterministic NFP seeding
                  │     cio/scheduler.py  APScheduler: daily digest + EOD price refresh + 06:00 briefing + econ-event alert
                  │     cio/db.py         SQLite (transactions = source of truth)
                  │
                  ├─► cio/committee/*   investment committee pipeline:
                  │     bundle → 9 specialists → debate → consensus → CIO → report
                  │     models.py  named-chain router: every agent references a reusable fallback chain
                  │     agent_memory.py  isolated per-agent memory (committee.db, WAL) + dedup
                  │     note_sanitizer.py  LLM figures-sanitizer (salvage) + sanitizer_log.py audit
                  │     render_pdf.py / translate.py  PDF + 繁體中文
                  │
                  ├─► cio/watchlist_monitor/*  pre-market Watchlist Monitoring Agent:
                  │     per-security assessment (bundle + web news + wma chain)
                  │     + one shared global macro/geopolitical snapshot → briefing PDF
                  │
                  └─► cio/alpha/*       Alpha Hunter: deterministic 5-layer NASDAQ swing funnel
                        (regime → sector → quality → earnings → momentum → Top-20)
                        zero-LLM; publishes the Alpha-yyyy-mm-dd watchlist (active)
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

`/committee SYMBOL` runs a simulated buy-side process and returns a 14-section **PDF**
research report. Add `zh` (`/committee AAPL zh`) for a **Traditional Chinese** version.

- **Two ways to convene**: the `/committee SYMBOL` slash command, or just **ask in plain
  language** ("convene the committee on META", "委員會討論 Meta"). Both route through the
  same `cio/committee/delivery.py` pipeline. In chat, the agent's `run_committee` tool
  fires the **real** committee (and sends the PDF) — it is told never to invent or
  simulate a verdict itself. The dashboard tags each run with its trigger (`chat` vs
  `/committee` vs `cli`). It's the one cost-bearing tool (~10-20 model calls); the agent
  confirms the symbol before firing.
- **Pipeline**: gather data (price / fundamentals incl. **forward P/E** / 38 TA signals) →
  **9 specialist agents** (market, **geopolitical & macro**, equity, industry, valuation,
  quant, ETF, risk, catalyst) vote (valuation & equity weigh forward vs trailing P/E) →
  **debate** (bear-vs-bull + risk-vs-valuation cross-examination, then revised votes) →
  **moderator consensus** + deterministic tally → **CIO** final decision (Strong Buy …
  Strong Sell, with bull/base/bear scenarios + macro/geopolitical risk scores). Specialists
  run in parallel.
- **Geopolitical & Macro Intelligence**: a dedicated specialist reads the macro backdrop
  (rates, inflation, growth, liquidity), geopolitics (conflicts, sanctions, export controls,
  trade), commodities, and FX, then judges how they cut for the name's sector. Its output
  drives a **Global Macro & Geopolitical Environment** report section + an **External Risk
  Matrix** (geopolitical / commodity / currency / regulatory).
- **Per-agent memory**: each of the 10 agents (9 specialists + CIO) keeps its **own isolated** persistent memory
  (scope `committee:{role}` in `data/committee.db`), so they accrue private lessons
  without sharing context — all behind the same figures firewall. Notes are
  **deduplicated** on write: a deterministic key collapses identical takeaways and a
  semantic check (embedding distance) collapses paraphrases, so an agent re-deriving the
  same lesson reinforces one note instead of spawning twins.
- **Model services** (`config/committee_models.yaml`): every agent references a
  **named fallback chain** — an ordered 3-link list of `{service, model, daily_limit?}`.
  Three built-in settings ship: `premium` (Claude Opus head → OpenAI → NIM, used by CIO
  and WMA), `standard` (OpenAI head → Claude Opus → NIM, used by all 9 specialists and
  moderator), and `translation` (Claude Sonnet head → OpenAI mini → NIM, used by the
  translator). `ask_role` walks the chain, skips any link whose daily token budget is
  spent, and falls through on error/empty. New settings can be added from the dashboard
  **Configure** page; per-agent assignment is a dropdown — no text editor needed.
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
  upcoming catalysts, whether the thesis changed, plus **external-risk exposure**
  (`external_risk_score` + macro / geopolitical / commodity / currency sensitivity). The
  briefing aggregates these into an executive summary, high/critical **alerts**, risks,
  catalysts, and a per-security review.
- **Global Macro & Geopolitical**: one **shared** macro/geopolitical headline read per run
  (not per security — keeps the first layer cheap) opens the briefing as **Global Market
  Intelligence** (market sentiment, geopolitical & commodity risk, key events), feeds a
  **Macro & Geopolitical Alerts** block, and a **Watchlist Exposure Analysis** table ranks
  names by their external-risk sensitivity.
- **Escalation**: names with a high/critical event or a negative thesis change are **flagged**
  for a full `/committee SYMBOL` run (it doesn't auto-run the committee — keeps the briefing cheap).
- **Schedule**: runs automatically at **06:00 local on trading days**. Holidays *and* weekends
  are skipped via the **NYSE calendar** (`pandas_market_calendars`). The briefing PDF + a short
  summary are pushed to every **subscribed** chat (same opt-in as the daily digest).
- **On demand**: `/briefing` (active watchlist) or `/briefing NVDA MU` (specific symbols);
  add `zh` for a **Traditional Chinese** briefing. CLI: `python -m cio.watchlist_monitor [SYMBOL…] [zh]`.
- **Model chain** (`config/committee_models.yaml`, role `wma`): uses the `premium` named
  chain — Claude Opus `claude-opus-4-8` (daily 200k) → OpenAI `gpt-5.5-2026-04-23`
  (daily 200k) → NVIDIA NIM `kimi-k2.6` (last resort) — the same fallback machinery as
  the CIO.

## Alpha Hunter (`/alpha`)

A **deterministic NASDAQ swing-selection engine**. It runs a fixed five-layer funnel —
**Market → Sector → Quality → Earnings → Momentum → Ranking** — over a configurable
ticker universe and publishes a **Top-20 watchlist** of names the market is re-pricing
higher. **Zero model tokens** (pure compute over yfinance + finnhub), deterministic,
and offline-safe — same cost discipline as TIRF.

- **The funnel** (`cio/alpha/`):
  - **L0 Market Regime** — QQQ vs 50/200-day MAs + slope → 🟢 GREEN / 🟡 YELLOW / 🔴 RED.
  - **L1 Sector Ranking** — RS = 0.5·3M + 0.5·6M over QQQ/SMH/IGV/HACK/BOTZ.
  - **L2 Quality Filter** (fail-closed) — cap > $2B, 20-day avg $-vol > $50M, revenue
    growth > 15%, forward-EPS growth > 15%, free cash flow > 0. Missing data → FAIL.
  - **L2.5 Earnings Engine** — 0.40·forward-EPS-growth + 0.40·EPS-revision (Lite: a >5%
    earnings gap-up unfilled for 10 days) + 0.20·surprise (last-4-quarter beat ratio).
  - **L3 Momentum** — relative strength vs QQQ (3M & 6M) + trend template
    (price > 50MA > 150MA > 200MA).
  - **L4 Ranking** — `Final = 0.30·Momentum + 0.20·Trend + 0.30·Earnings +
    0.10·Revenue + 0.10·VolumeExpansion` (0–100). Every candidate scoring **Final ≥
    threshold** (default **80**, configurable in the dashboard) is selected.
- **Auto-named watchlist**: each run publishes/refreshes a list named
  **`Alpha-yyyy-mm-dd`** and **sets it active**, so Telegram `/watchlist` shows it
  immediately. Same-day re-runs refresh the one dated list in place (no duplicates);
  the `^IXIC` benchmark floor is kept.
- **Operate from Telegram**: `/alpha` runs the funnel; ask **"what's the market
  regime"** for the GREEN/YELLOW/RED light (`market_regime` tool); then the agent tools
  `list_watchlists` / `watchlist_add` / `watchlist_remove` / `watchlist_activate` let
  you manage the published list conversationally ("add TSLA to the alpha list",
  "switch to Alpha-2026-06-12").
- **Dashboard**: the **Alpha Hunter** tab (`/alpha`) has a **Run** button, a
  **selection-threshold** control (default 80), and shows the regime light, sector
  ranking, selected candidates, run history, and a link to the published list.
- **Universe**: the candidate pool is `config/alpha_universe.txt` (one ticker per line,
  `#` comments; ~40 liquid NASDAQ names by default). Override with
  `CIO_ALPHA_UNIVERSE=/path/to/file` or the CLI `--universe FILE`. Run time scales with
  universe size (cached after the first run).
- **CLI**: `python -m cio.alpha [--universe FILE] [--no-publish] [--json]`.
- **Docs**: `docs/ALPHA-HUNTER-TECHNICAL-REPORT.md` (full reference),
  `docs/ALPHA-HUNTER-PRD.md`, `docs/ALPHA-HUNTER-TEST-PLAN.md`, and the
  `docs/alpha-hunter/` diagram set (architecture / activity / control-flow /
  data-flow / sequence).

## Economic-event alerts

A 24/7 CIO should warn you *before* a market-moving release, not explain the drop
after. The bot keeps a small calendar of high-impact economic events and pushes a
deterministic, **zero-token** heads-up to subscribed chats ahead of each one.

- **NFP is computed** — the monthly jobs report falls on the first Friday (08:30 ET),
  so it is seeded by rule and can never drift.
- **Everything else is fetched** — CPI/PPI/PCE/FOMC/GDP/Retail have no clean monthly
  rule, so the agent looks up and **verifies** real dates rather than guessing. A wrong
  date is worse than no date.
- **Auto-alert** — `cio/scheduler.py` runs daily (default 07:00 local), warns once per
  event within the lead window (`CIO_ECON_ALERT_LEAD_DAYS`, default 1), and marks it
  sent so it never repeats — even across restarts.

Populate a month via the **`monthly_red_events` playbook**: ask the bot "run
monthly_red_events" and it web-searches the month's releases, verifies them against
BLS/BEA/the Fed or a reputable calendar, and records each with `add_econ_event`. NFP
auto-fills regardless. Inspect/manage everything on the dashboard's **Econ events** and
**Playbooks** tabs.

Agent tools: `add_econ_event`, `list_econ_events`. Timing knobs under
[Run 24/7](#run-247-systemd). Full write-up: **[docs/ECON-EVENT-ALERTS.md](docs/ECON-EVENT-ALERTS.md)**.

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
- `OPENAI_API_KEY` — used by `standard` chain (specialists) as head link and by `premium`
  chain (CIO/WMA) as the second link; absent → those links are skipped, next link used.
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

**Economic-event alert timing** — a daily heads-up before high-impact economic
releases (NFP/CPI/FOMC/…) is pushed to subscribed chats:

```
CIO_ECON_ALERT_HOUR=7        # hour 0–23, or "off" to disable
CIO_ECON_ALERT_MINUTE=0
CIO_ECON_ALERT_LEAD_DAYS=1   # warn this many days ahead of each event
```

See **[Economic-event alerts](#economic-event-alerts)** below. A boot one-shot
re-checks the window (and seeds NFP) shortly after start.

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
write surfaces are the **Watchlist** / **Portfolio** / **Configure** / **Alpha Hunter**
pages (manage lists, set prices, import CSVs, edit model routing, run the swing funnel)
and **delete** controls on the Telegram /
Memory / Committee / Playbooks / Econ-events pages. Every mutation POSTs then redirects
(PRG), behind the same auth gate, and destructive deletes ask for confirmation first.

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
- **Alpha Hunter** — the deterministic NASDAQ swing funnel. A **Run** button scans the
  universe and publishes the `Alpha-yyyy-mm-dd` watchlist (active); a **selection
  threshold** field (default 80) controls which candidates make the list; the page shows
  the market-regime light, sector ranking, selected candidates, and run history.
- **Committee** — every run, each with its **Trigger** (`chat` ask vs `/committee` slash
  vs `cli`), drills into every LLM call: the exact content **sent** (system + user prompt)
  and the content **returned**, per role, in order. Includes a **delete-all-runs** button.
- **Memory** — per-agent / per-chat memory contents for debugging: every scope across
  both stores (`chat:*` / `global` in `cio.db`, `committee:<role>` in `committee.db`)
  with each note's tier, key, value, hits, importance, source, and update time. Each store
  and each scope has a **delete** button.
- **Playbooks** — the agent's saved reusable procedures (name, scope, hit count, steps,
  created) with a per-row **delete**. Steps reference *tools*, not cached numbers, so a
  playbook never goes stale.
- **Econ events** — high-impact economic events the bot alerts on (date, event, impact,
  time, source, whether a heads-up was sent) with a per-row **delete**. NFP auto-seeds;
  the rest are populated by the agent from verified sources.
- **Sanitizer** — audit trail of the figures-sanitizer: every note it rewrote (figures
  stripped) or rejected, with the agent, symbol, what was removed, and the stored result.
- **Configure** — edit `config/committee_models.yaml` from the UI instead of a text editor.
  **Fallback chain settings** section: one editable table per named setting (per link: service
  dropdown, model dropdown, daily token limit); add new settings by name; delete settings
  (refused while any agent still references them). **Agents** section: every agent (defaults,
  all specialists, moderator, cio, wma, translator) has a **chain dropdown** to reassign it
  to any named setting; legacy inline agents show a placeholder that converts them on pick.
  Collapsible sections expose provider connection knobs (base_url / api_key_env / token caps)
  and a **model-catalog** editor (add/remove the model names that populate the dropdowns).
  Saves round-trip the YAML (comments preserved via `ruamel.yaml`) and clears the config
  cache so edits apply to the next run.

Capture is on by default. One knob, `CIO_CAPTURE_LEVEL` (default `1`), tunes scope:

| Level | Committee transcript | Telegram history |
|------:|----------------------|------------------|
| 1 | full, pruned to last `CIO_TRANSCRIPT_KEEP_RUNS` (200) | on |
| 2 | full, kept forever | on |
| 3 | full, pruned | off (committee only) |

Bind/auth: `CIO_DASH_HOST` / `CIO_DASH_PORT`, and an optional `CIO_DASH_TOKEN`
shared secret (append `?token=…` once; a session cookie carries it after). Keep
the bind on `127.0.0.1` — it serves your own financial data.

**Detailed conversation history** (opt-in, off by default). Logs every LLM call —
main-agent turns and each committee agent call — verbatim to day-based text files:
`logs/<yyyy>/<mm>/<yyyy-mm-dd>.txt` (base dir `CIO_LOG_DIR`, default `logs/`). Each
entry records the system prompt, user prompt, response, the LLM service provider +
model, and token usage. Toggle it from the **Configure** tab (persisted in
`dashboard_settings.json`, shared across the bot + dashboard processes) or force it
with the `CIO_DETAILED_LOG` env var (when set, it wins and locks the dashboard toggle).
View on the dashboard **Detailed history** tab (`/detailed`): lists logged days, shows
a selected day's full log, and deletes a day — mirroring the Telegram tab. Files are
git-ignored; logging never breaks a turn.

## Roadmap

- Accounting domain (ledgers, COGS, P&L) and inventory stock — share `db.py`.
- Richer committee data (macro/SEC/news feeds; currently yfinance + LLM reasoning).
- FIFO cost-basis option.
