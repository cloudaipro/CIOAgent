# Dedicated swing-scan job

The Telegram request **「今天有哪些適合進場做波段操作」** is interpreted by
the conversational LLM. When the LLM determines that the user is directly
asking to start the scan, it calls the narrow `request_swing_scan` handoff tool;
the Telegram bot then routes that handoff to the same tracked `/swing` workflow.
There is deliberately no keyword/regex bypass: quoted requests, negations, and
questions about earlier results remain normal conversation turns.

## Telegram

```text
/swing                 # reuse today's completed scan, or start one
/swing refresh         # force a new scan
/swing en              # return the report in English
/swing_status          # inspect the active/last job
/stop                  # cooperatively cancel the active scan
```

The same job can therefore be invoked in exactly two ways:

1. An explicit `/swing` command (or its Telegram button).
2. A normal-language message that the LLM understands as a direct request to
   start or refresh the swing scan.

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
