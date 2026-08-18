# Dedicated swing-scan job

The Telegram request **「今天有哪些適合進場做波段操作」** is a high-value,
network-bound workflow and must not be handled inside a general Codex turn.
The bot recognizes that intent and routes it to the same deterministic Alpha
Hunter funnel as `/alpha`, through `cio/swing.py`.

## Telegram

```text
/swing                 # reuse today's completed scan, or start one
/swing refresh         # force a new scan
/swing en              # return the report in English
/swing_status          # inspect the active/last job
/stop                  # cooperatively cancel the active scan
```

The default report is Traditional Chinese. The scan publishes/refreshes
`Alpha-YYYY-MM-DD` and makes it the active watchlist, matching Alpha Hunter's
existing contract.

## Why it avoids the timeout

`/swing` is a `block=False` Telegram handler and runs `swing.execute()` with
`asyncio.to_thread`. It never calls `BotRuntime.ask()` and therefore never
starts a Codex turn or its 600-second turn deadline. The handler acknowledges
immediately, exposes a durable job id, and sends the deterministic report after
the worker finishes. A same-day completed Alpha snapshot is reused, so repeated
requests are normally fast.

The job lifecycle (`queued`, `running`, `cancel_requested`, `completed`, `failed`,
`cancelled`) is
stored in `swing_jobs`; the actual market result remains in the auditable
`alpha_runs` / `alpha_candidates` snapshot tables. `/swing_status` treats a job
left in `queued`/`running` after a process restart as `interrupted`, rather than
claiming that a worker still exists.

Cancellation is cooperative. `/stop` sets an event and cancels the Telegram
waiter; the Alpha loop checks the event between ticker/network steps and before
committing a fresh result. A library call already in progress cannot be killed
by Python's thread pool, so the worker may finish that one call before stopping.

This is a research screen, not an order instruction. The report explicitly asks
the user to confirm live price, volume, catalysts, and a stop level before acting.
