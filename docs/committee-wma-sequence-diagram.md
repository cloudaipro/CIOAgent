# Committee & WMA — Sequence Diagrams

Sequence diagrams show the chronological message exchange between components.

## How to Read the Parallelism

Mermaid `par` blocks below show calls that run **concurrently**, but real concurrency
is bounded by semaphores, not unlimited:

- WMA symbol scan: `CIO_WMA_CONCURRENCY` (default **4**) concurrent securities.
- Committee Round 1, debate pairs, Round 3 revisions: `CIO_MAX_CONCURRENCY` (default
  **8**) concurrent LLM calls, via `_gather_bounded` which preserves result order.
- Moderator and CIO are **always serial** — each depends on the previous step's output.

When `CIO_PARALLEL=off`, every `par` block degrades to sequential `await`s with
identical results, just slower. The diagrams show the default (parallel) timeline.

A note on the `ContextVar` set at the start of `run_committee`: `run_id`, `run_symbol`,
and `run_source` propagate automatically into the spawned parallel tasks, so every
captured transcript line is correctly grouped even though the calls interleave.

---

## WMA Full Run Sequence

```mermaid
sequenceDiagram
    autonumber
    actor Trigger as Trigger<br/>(CLI / Scheduler / Bot)
    participant WMA as WMA Agent<br/>(agent.py)
    participant WL as Watchlist<br/>(cio.watchlist)
    participant WEB as Web / Firecrawl<br/>(cio.web)
    participant BUNDLE as Bundle<br/>(committee.bundle)
    participant ENGINE as Engine<br/>(committee.engine)
    participant LLM as LLM Backend<br/>(NIM / Claude / OpenAI)
    participant REPORT as Report<br/>(watchlist_monitor.report)
    participant BOT as Bot / Deliver

    Trigger->>WMA: monitor_watchlist(symbols=None)
    WMA->>WL: watchlist.active()
    WL-->>WMA: {symbols: [...]}

    WMA->>WEB: search("global macro geopolitical...", limit=6)
    WEB-->>WMA: macro headlines []
    WMA->>ENGINE: ask_role(MACRO_SNAPSHOT_SYSTEM, prompt, role_key="macro")
    ENGINE->>LLM: POST /chat/completions
    LLM-->>ENGINE: yaml response
    ENGINE-->>WMA: macro snapshot dict

    loop For each symbol (max_conc=4 semaphore)
        WMA->>BUNDLE: gather_bundle(symbol)
        BUNDLE->>BUNDLE: normalize_symbol / get_quote / fundamentals / TA / EDGAR / Finnhub
        BUNDLE-->>WMA: bundle dict

        alt bundle.resolved is None
            WMA-->>WMA: return _skipped stub
        else data available
            WMA->>WEB: search("{company} stock news...", limit=5)
            WEB-->>WMA: headlines []
            WMA->>ENGINE: ask_role(WMA_SYSTEM, user_prompt, role_key="wma")
            ENGINE->>LLM: POST /chat/completions
            LLM-->>ENGINE: fenced yaml block
            ENGINE-->>WMA: raw text
            WMA->>WMA: parse_yaml_block + normalise fields
            WMA->>WMA: _recent_8k(filings) → escalate flag
        end
    end

    WMA-->>REPORT: assessments []
    REPORT->>REPORT: build_briefing(assessments, macro=snapshot)
    REPORT-->>BOT: markdown string
    BOT->>BOT: render PDF (or .md fallback)
    BOT->>BOT: briefing_summary (Telegram text)
    BOT-->>Trigger: PDF + summary delivered
```

**WMA timeline notes.** The macro snapshot (`global_macro_snapshot`) is taken once,
up front, and reused for the whole briefing — it is *not* per security, which keeps the
layer cheap. Each `monitor_symbol` spends at most one `wma`-chain LLM call; a symbol
with no price data short-circuits to a `_skipped` stub and spends nothing. The escalate
flag computed per security is surfaced in `briefing_summary` as a "⚠️ Consider
/committee: …" line, the hand-off point from Layer 1 to Layer 2.

---

## Committee Full Run Sequence (with Debate)

```mermaid
sequenceDiagram
    autonumber
    actor Caller as Caller<br/>(Bot / CLI / WMA escalation)
    participant DELIVERY as Delivery<br/>(committee.delivery)
    participant ENGINE as Engine<br/>(committee.engine)
    participant BUNDLE as Bundle<br/>(committee.bundle)
    participant DEBATE as Debate<br/>(committee.debate)
    participant TIRF as TIRF<br/>(committee.tirf)
    participant MEM as AgentMemory<br/>(committee.agent_memory)
    participant LLM as LLM Backend<br/>(NIM / Claude / OpenAI)
    participant DB as SQLite<br/>(committee.db)
    participant PDF as PDF Renderer

    Caller->>DELIVERY: produce_report(symbol, lang)
    DELIVERY->>ENGINE: set_run_source("command")
    DELIVERY->>ENGINE: run_committee(symbol)

    ENGINE->>ENGINE: set run_id + run_symbol (ContextVar)
    ENGINE->>BUNDLE: gather_bundle(symbol)
    BUNDLE-->>ENGINE: bundle dict

    alt resolved is None
        ENGINE-->>DELIVERY: CommitteeResult(error="no data")
        DELIVERY-->>Caller: CommitteeArtifact(error=...)
    end

    note over ENGINE: Round 1 — Specialists (parallel, semaphore MAX_CONC=8)

    par Parallel specialist calls
        ENGINE->>MEM: recall_block("market", symbol)
        MEM-->>ENGINE: memory block text
        ENGINE->>LLM: ask_role market specialist
        LLM-->>ENGINE: yaml opinion

        ENGINE->>MEM: recall_block("macro", symbol)
        ENGINE->>LLM: ask_role macro specialist
        LLM-->>ENGINE: yaml opinion

        ENGINE->>LLM: ask_role equity specialist
        LLM-->>ENGINE: yaml opinion

        ENGINE->>LLM: ask_role industry specialist
        LLM-->>ENGINE: yaml opinion

        ENGINE->>LLM: ask_role valuation specialist
        LLM-->>ENGINE: yaml opinion

        ENGINE->>LLM: ask_role quant specialist
        LLM-->>ENGINE: yaml opinion

        ENGINE->>LLM: ask_role risk specialist
        LLM-->>ENGINE: yaml opinion

        ENGINE->>LLM: ask_role catalyst specialist
        LLM-->>ENGINE: yaml opinion
    end

    note over ENGINE: Round 2 — Debate (if CIO_DEBATE=on and votes disagree)

    ENGINE->>DEBATE: run_debate(round1_opinions, ...)
    DEBATE->>DEBATE: select_debate_pairs (max 2 pairs)

    par Cross-exam pairs (parallel)
        DEBATE->>LLM: challenger rebuttal (≤120w)
        LLM-->>DEBATE: challenge text
        DEBATE->>LLM: target response (≤120w)
        LLM-->>DEBATE: response text
    end

    DEBATE->>DEBATE: build debate_text transcript

    note over ENGINE,DEBATE: Round 3 — Revisions (parallel)

    par Revision calls (parallel)
        DEBATE->>LLM: revise_opinion specialist A
        LLM-->>DEBATE: revised yaml opinion
        DEBATE->>LLM: revise_opinion specialist B
        LLM-->>DEBATE: revised yaml opinion
    end

    DEBATE-->>ENGINE: debate_result {pairs, exchanges, round3_opinions}

    note over ENGINE: Step 4 — Moderator (serial)
    ENGINE->>ENGINE: _compute_vote_tally (Python, no LLM)
    ENGINE->>LLM: ask_role MODERATOR_SYSTEM
    LLM-->>ENGINE: consensus yaml

    note over ENGINE: Step 5 — CIO (serial, chain fallback)
    ENGINE->>MEM: recall_block("cio", symbol)
    MEM-->>ENGINE: cio memory block
    ENGINE->>LLM: ask_role CIO_SYSTEM (NIM first)
    alt NIM returns text
        LLM-->>ENGINE: cio yaml
    else NIM empty or over budget
        ENGINE->>LLM: ask_role Claude fallback
        LLM-->>ENGINE: cio yaml
    end

    note over ENGINE: Memory saves (all roles)
    par Save memory notes
        ENGINE->>MEM: sanitize + save_note (market)
        ENGINE->>MEM: sanitize + save_note (cio)
    end
    ENGINE->>MEM: reflect (promote hot notes per role)

    note over ENGINE,TIRF: Step 6 — TIRF (zero LLM cost)
    ENGINE->>TIRF: build_research_report(...)
    TIRF->>TIRF: extract → score → validate → review
    TIRF->>DB: persist (9 tables, 1 transaction)
    DB-->>TIRF: report_id
    TIRF-->>ENGINE: ResearchReport

    ENGINE-->>DELIVERY: CommitteeResult

    DELIVERY->>DELIVERY: build_report markdown
    DELIVERY->>LLM: translate_report (if lang=tc)
    LLM-->>DELIVERY: translated markdown
    DELIVERY->>PDF: markdown_to_pdf
    PDF-->>DELIVERY: PDF file
    DELIVERY-->>Caller: CommitteeArtifact {doc_path, summary}
    Caller->>Caller: send PDF + summary to user
```

**Committee timeline notes.** The debate is conditional: `run_debate` is only reached
when Round-1 votes genuinely disagree, and even then `select_debate_pairs` caps the
cross-exam at `CIO_DEBATE_MAX_PAIRS` (default 2) pairs — typically *most-bearish vs
most-bullish* plus the PRD-mandated *risk vs valuation*. Within a pair the challenge and
response are serial (the response must see the challenge); across pairs they run
concurrently. Round 3 re-polls **every** specialist (not just the debaters) with the
full transcript so each may revise or hold. Memory writes (`sanitize → save_note`) and
`reflect` happen after the CIO call; TIRF runs last and is fully deterministic.

---

## Single ask_role Chain Fallback Sequence

```mermaid
sequenceDiagram
    autonumber
    participant CALLER as Caller
    participant AR as ask_role<br/>(engine.py)
    participant USAGE as usage.py<br/>(token budget)
    participant TRANS as transcript.py<br/>(capture)
    participant NIM as NIM API<br/>(nemotron-550B)
    participant CLAUDE as Claude SDK<br/>(claude-opus-4-8)
    participant OPENAI as OpenAI API<br/>(gpt-5.5)

    CALLER->>AR: ask_role(system, user, role_key="cio")
    AR->>AR: resolve_chain("cio") → 3 links
    AR->>USAGE: over_budget("nim", 200000)?
    USAGE-->>AR: False

    AR->>NIM: POST /chat/completions (timeout 300s)
    alt HTTP 429 or 503
        NIM-->>AR: 429 Too Many Requests
        AR->>AR: sleep(retry-after or backoff)
        AR->>NIM: retry (up to 3x)
    end
    NIM-->>AR: 200 {choices[0].message.content}
    AR->>AR: _is_limit_notice? → False

    AR->>USAGE: record("nim", tokens)
    AR->>TRANS: record(role_key, service, model, prompts, response, tokens)
    AR-->>CALLER: text (non-empty)

    note over AR: If NIM returned empty or timed out:

    AR->>USAGE: over_budget("claude", 200000)?
    USAGE-->>AR: False
    AR->>CLAUDE: ClaudeSDKClient.connect + query
    CLAUDE-->>AR: AssistantMessage stream
    AR->>USAGE: record("claude", tokens)
    AR->>TRANS: record(...)
    AR-->>CALLER: text

    note over AR: If Claude also empty (last resort):

    AR->>OPENAI: POST /chat/completions
    OPENAI-->>AR: 200 response
    AR->>USAGE: record("openai", tokens)
    AR->>TRANS: record(...)
    AR-->>CALLER: text  (or "" if all exhausted)
```
