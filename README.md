# CIO Agent

A personal CIO chat agent for a solo operator. Talk to it on **Telegram**; it
answers questions about your **stock portfolio**, imports CSVs, and sends charts.

Runs on your **Claude Pro subscription** via `claude-agent-sdk` — **no
ANTHROPIC_API_KEY needed**. The Claude Code CLI must be installed and logged in
(`claude` on your PATH, already authenticated).

## Architecture

```
Telegram  ──►  cio/bot.py     I/O: text, photos, CSV uploads, chart replies
                  │
                  ▼
              cio/agent.py    Claude agent (Pro auth) + in-process MCP tools
                  │
                  ▼
            cio/portfolio.py  pandas/SQLite: cost basis, P&L, valuation
            cio/charts.py     matplotlib → PNG
            cio/memory.py     MemCore store: tiered notes, profile, playbooks, eviction
            cio/context.py    session-start memory injection (token-budgeted)
            cio/recall.py     hybrid recall: FTS5 + fastembed + sqlite-vec (RRF)
            cio/scheduler.py  APScheduler: daily portfolio digest
            cio/db.py         SQLite schema (transactions = source of truth)
```

- **Cost basis**: average-cost method. Positions & P&L are *derived* from the
  `transactions` table, so they're always consistent.
- **Prices**: entered manually (no live feed yet) — `set AAPL 230`.
- **Images out**: chart tools drop PNGs into an outbox the bot sends as photos.
- **Images in**: send a broker screenshot/receipt; the agent uses the Read tool
  (vision) to extract figures.
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

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# one-time: download + cache the local embedding model (bge-base, ~210MB) so recall is offline-stable
.venv/bin/python -c "from cio import recall; print('embed dim', recall.warmup())"
cp .env.example .env          # paste your @BotFather token into TELEGRAM_BOT_TOKEN
.venv/bin/python -m cio.bot   # starts polling
```

## Use (in Telegram)

- `how's my portfolio?` · `what's my top gainer?` · `show my allocation`
- `set AAPL 230` — manual price
- Upload a transactions CSV: `txn_date,symbol,action,quantity,price[,fees,currency,notes]`
  (`action` ∈ BUY/SELL/DIV)
- Send a photo of a broker screen/receipt to have it read
- `/subscribe` — get a daily portfolio digest · `/unsubscribe` — stop it

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

## Roadmap

- Accounting domain (ledgers, COGS, P&L) and inventory stock — share `db.py`.
- Optional live price feed.
- FIFO cost-basis option.
