# CIO Agent — AI Investment Committee

A personal CIO agent for a solo operator. Talk to it on **Telegram**; it answers
questions about your **stock portfolio**, imports CSVs, sends charts, fetches live
quotes / runs 38 technical strategies, **searches the live web** (Firecrawl), and —
on demand — convenes a **multi-agent investment committee** (`/committee SYMBOL`)
that researches a stock and returns an institutional-grade **PDF** report (with an
optional **Traditional Chinese** version).

The conversational agent runs on your **Claude Pro subscription** via `claude-agent-sdk`
— **no ANTHROPIC_API_KEY needed** (the `claude` CLI must be installed and logged in). The
committee's agents are pluggable across three model backends — **Claude**, **NVIDIA NIM**
(`minimax`), and **OpenAI** — and the final CIO decision runs a daily-token-budget
fallback chain across them.

## Architecture

```
Telegram  ──►  cio/bot.py        I/O + access gate; text, photos, CSV, /committee
                  │
                  ├─► cio/agent.py      Claude agent (Pro auth) + 22 in-process MCP tools
                  │     cio/portfolio.py  pandas/SQLite: cost basis, P&L, valuation
                  │     cio/charts.py     matplotlib → PNG
                  │     cio/memory.py     MemCore store: tiered notes, profile, eviction
                  │     cio/context.py    session-start memory injection (token-budgeted)
                  │     cio/recall.py     hybrid recall: FTS5 + fastembed + sqlite-vec (RRF)
                  │     cio/stock/*       yfinance quotes, cache, 38 TA strategies, panel
                  │     cio/web.py        Firecrawl-backed web search + scrape
                  │     cio/scheduler.py  APScheduler: daily digest + EOD price refresh
                  │     cio/db.py         SQLite (transactions = source of truth)
                  │
                  └─► cio/committee/*   investment committee pipeline:
                        bundle → 8 specialists → debate → consensus → CIO → report
                        models.py  per-agent model router (claude | NIM | OpenAI) + CIO chain
                        agent_memory.py  isolated per-agent memory (committee.db, WAL)
                        render_pdf.py / translate.py  PDF + 繁體中文
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
- **Figures firewall**: the store refuses to memorize numbers/prices — those are
  always recomputed from the ledger, so memory can never go stale on a figure.

Tools the agent gets: `remember` / `recall` / `forget`, `memory_search` /
`memory_get`, `save_playbook` / `list_playbooks`.

## Investment committee (`/committee`)

`/committee SYMBOL` runs a simulated buy-side process and returns a 13-section **PDF**
research report. Add `zh` (`/committee AAPL zh`) for a **Traditional Chinese** version.

- **Pipeline**: gather data (price / fundamentals incl. **forward P/E** / 38 TA signals) →
  **8 specialist agents** (market, equity, industry, valuation, quant, ETF, risk, catalyst)
  vote (valuation & equity weigh forward vs trailing P/E) →
  **debate** (bear-vs-bull + risk-vs-valuation cross-examination, then revised votes) →
  **moderator consensus** + deterministic tally → **CIO** final decision (Strong Buy …
  Strong Sell, with bull/base/bear scenarios). Specialists run in parallel.
- **Per-agent memory**: each of the 9 agents keeps its **own isolated** persistent memory
  (scope `committee:{role}` in `data/committee.db`), so they accrue private lessons
  without sharing context — all behind the same figures firewall.
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
- `/committee SYMBOL` — full AI investment-committee PDF report (`/committee AAPL zh` for 繁體中文)
- `/stop` — cancel whatever's currently running for you (a turn or a committee run)
- `/subscribe` — get a daily portfolio digest · `/unsubscribe` — stop it

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

A localhost-only, read-only web view to verify the agent is behaving correctly.

```bash
.venv/bin/python -m cio.dashboard          # http://127.0.0.1:8787
```

Pages:

- **Token usage** — OpenAI / Claude / NIM tokens per service per UTC day (from `committee.db`).
- **Telegram** — full conversation history (every user/assistant turn).
- **Committee** — each run drills into every LLM call: the exact content **sent**
  (system + user prompt) and the content **returned**, per role, in order.
- **Memory** — per-agent / per-chat memory contents for debugging: every scope across
  both stores (`chat:*` / `global` in `cio.db`, `committee:<role>` in `committee.db`)
  with each note's tier, key, value, hits, importance, source, and update time.

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
