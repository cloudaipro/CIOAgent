# Committee & WMA — System Design & Architecture

## Design Philosophy

The system is built for a **solo operator** on a constrained token budget, so its
architecture is shaped by three principles:

1. **Two-layer cost tiering.** A cheap daily *breadth* pass (WMA — 1 call/security)
   filters the watchlist down to the few names worth an expensive *depth* pass
   (Committee — ~20 calls/symbol). The WMA never auto-triggers the committee; it raises
   an `escalate` flag and the operator decides. This caps spend predictably.
2. **Offline-safe by construction.** Every external dependency — market data, EDGAR,
   Finnhub, Firecrawl, all three LLM backends, the PDF renderer, every DB write — is
   wrapped to degrade gracefully. Missing an API key disables a feature; it never
   crashes a run. The system is usable with *zero* optional integrations configured.
3. **Transparency without extra cost.** The TIRF layer turns each run into an auditable
   research report (evidence, assumptions, reasoning, counterarguments, sources,
   reproducibility pins, scorecards) using only the yaml the specialists already
   produced — **zero additional LLM calls**.

The model strategy reflects #1: every committee role is assigned a **named fallback chain**
— an ordered 3-link list of `{service, model, daily_limit?}` defined once in
`config/committee_models.yaml` and referenced by name per agent. Three settings ship:
`standard` (OpenAI head → Claude Opus → NIM, used by all specialists and moderator),
`premium` (Claude Opus head → OpenAI → NIM, used by CIO and WMA), and `translation`
(Claude Sonnet head → OpenAI mini → NIM, used by the translator). `ask_role` walks a
chain's links, skipping any link whose daily token budget is spent and falling through on
error/empty, so a backend outage or budget exhaustion never silences any role.

---

## Two-Layer Investment Intelligence Architecture

```mermaid
flowchart TB
    subgraph Triggers["Entry Points"]
        CLI[CLI\npython -m cio.committee]
        BOT[Telegram Bot\n/committee /watchlist]
        SCHED[Scheduler\ndaily cron]
        CHAT[Conversational Agent\nrun_committee tool]
    end

    subgraph L1["Layer 1 — WMA (Cheap Daily Pass)"]
        WMA_AGENT[WMA Agent\nagent.py\n1 LLM call per security]
        MACRO_SNAP[Global Macro Snapshot\n1 LLM call per run]
        WMA_REPORT[Briefing Builder\nreport.py\nmarkdown / exposure table]
    end

    subgraph L2["Layer 2 — Investment Committee (Deep Analysis)"]
        COMMITTEE[run_committee\nengine.py]

        subgraph R1["Round 1 — Specialists (parallel)"]
            S_MARKET[Market Intelligence]
            S_MACRO[Geopolitical & Macro]
            S_EQUITY[Equity Research]
            S_INDUSTRY[Industry Intelligence]
            S_VALUATION[Valuation]
            S_QUANT[Quantitative]
            S_ETF[ETF Research*]
            S_RISK[Risk Management]
            S_CATALYST[Catalyst]
        end

        subgraph R2R3["Rounds 2-3 — Debate (optional)"]
            DEBATE[Debate Engine\ndebate.py]
            CROSS_EXAM[Cross-Exam\nchallenger → target]
            REVISE[Round 3 Revisions]
        end

        MODERATOR[Moderator\nconsensus synthesis]
        CIO[CIO\nfinal decision]
    end

    subgraph TIRF_LAYER["TIRF — Transparent Research Framework (zero LLM cost)"]
        T_EXTRACT[extract.py\nevidence / assumptions\nreasoning / counterargs / sources]
        T_SCORE[scoring.py\nitem-level evidence scoring]
        T_VALIDATE[validate.py\ncompute_metrics scorecard]
        T_REVIEW[review.py\ncio_review]
        T_STORE[store.py\n9 tables in committee.db]
    end

    subgraph DATA["Data Layer"]
        BUNDLE[Bundle\nbundle.py\ngather + format]
        STOCK[cio.stock\nYahoo Finance facade]
        EDGAR_DATA[cio.data EDGAR\nSEC filings]
        FINNHUB_DATA[cio.data Finnhub\nanalyst / earnings]
        WEB[cio.web\nFirecrawl search]
        MEM_STORE[Agent Memory\nagent_memory.py\nSQLite hot/warm/cold]
    end

    subgraph BACKENDS["LLM Backends"]
        NIM_B[NVIDIA NIM\nkimi-k2.6\nOpenAI-compatible REST]
        CLAUDE_B[Claude Agent SDK\nclaude-opus-4-8]
        OPENAI_B[OpenAI API\ngpt-5.5]
    end

    subgraph PERSISTENCE["Persistence"]
        COMM_DB[(committee.db\ntranscript / usage / TIRF)]
        REPORTS_DIR[reports/\nPDF + Markdown]
    end

    subgraph CONFIG["Configuration"]
        MODELS_YAML[config/committee_models.yaml\nnamed chains / per-agent assignment\ndaily_limit per link]
        ENV[Environment Variables\nCIO_PARALLEL / CIO_DEBATE\nCIO_NIM_MAX_TOKENS etc.]
    end

    Triggers --> L1
    Triggers --> L2

    L1 --> BUNDLE
    L2 --> BUNDLE
    BUNDLE --> STOCK
    BUNDLE --> EDGAR_DATA
    BUNDLE --> FINNHUB_DATA
    WEB --> WMA_AGENT
    WEB --> MACRO_SNAP

    WMA_AGENT --> BACKENDS
    MACRO_SNAP --> BACKENDS
    COMMITTEE --> BACKENDS
    R1 --> BACKENDS
    R2R3 --> BACKENDS
    MODERATOR --> BACKENDS
    CIO --> BACKENDS

    COMMITTEE --> MEM_STORE
    MEM_STORE --> R1

    L2 --> TIRF_LAYER
    T_EXTRACT --> T_SCORE --> T_VALIDATE --> T_REVIEW --> T_STORE
    T_STORE --> COMM_DB

    COMMITTEE --> COMM_DB
    L1 --> REPORTS_DIR
    L2 --> REPORTS_DIR

    CONFIG --> BACKENDS
    CONFIG --> L2

    WMA_AGENT --> WMA_REPORT
    WMA_REPORT --> REPORTS_DIR

    note_escalate[⚠️ WMA escalates to Committee\nif event=high/critical OR\nthesis=negative OR recent 8-K]
    L1 -. escalate .-> L2
```

**The nine specialists** (`roles.py`) each carry a focused system prompt and a set of
required yaml output fields, but all share the same base rules (DATA is authoritative,
no invented figures, emit the TIRF deliverables, write a qualitative `memory_note`):

| Role key | Title | Distinct mandate |
|----------|-------|------------------|
| `market` | Market Intelligence | macro environment, capital flows |
| `macro` | Geopolitical & Macro | rates/inflation, conflicts, sanctions, commodities, FX, supply chain |
| `equity` | Equity Research | financial health, earnings quality, FWD_PE vs trailing |
| `industry` | Industry Intelligence | sector cycle, tailwinds/headwinds |
| `valuation` | Valuation | fair value, up/downside, FWD_PE as primary input |
| `quant` | Quantitative | TA signals, trend, momentum |
| `etf` | ETF Research | overlap, liquidity, tracking — **only runs for ETFs** |
| `risk` | Risk Management | designated opposition; worst-case, independent of consensus |
| `catalyst` | Catalyst | upcoming events, re-rating triggers, timelines |

Above them sit the **Moderator** (synthesises a written consensus + agreement score)
and the **CIO** (final recommendation integrating every input, with explicit
macro/geopolitical risk scores and bull/base/bear scenarios).

---

## Component Responsibilities

```mermaid
classDiagram
    class Engine {
        +run_committee(symbol) CommitteeResult
        +ask_role(system, user, role_key) str
        +parse_yaml_block(text) dict
        -_dispatch(service, system, user, model) tuple
        -_ask_nim() tuple
        -_ask_claude() tuple
        -_ask_openai() tuple
        -_gather_bounded(coros, parallel) list
        -_compute_vote_tally(opinions) dict
        -_capture(service, model, ...) None
        -_RUN_ID ContextVar
        -_RUN_SYMBOL ContextVar
        -_RUN_SOURCE ContextVar
    }

    class Bundle {
        +gather_bundle(symbol) dict
        +format_bundle(bundle) str
        -_external(symbol, is_etf) tuple
        -_latest_signal(df) str
    }

    class Roles {
        +SPECIALISTS list
        +MODERATOR_SYSTEM str
        +CIO_SYSTEM str
    }

    class Debate {
        +run_debate(opinions, bundle_text, symbol, roles) dict
        +run_cross_exam(pair, bundle_text, symbol) dict
        +revise_opinion(role, round1, debate_text, bundle) dict
        +select_debate_pairs(opinions, max_pairs) list
    }

    class WMAAgent {
        +monitor_watchlist(symbols) list
        +monitor_symbol(symbol) dict
        +global_macro_snapshot() dict
        -_fetch_news(symbol, company, news_fn) list
        -_recent_8k(filings) bool
        -_skipped(symbol, reason) dict
    }

    class WMAReport {
        +build_briefing(assessments, as_of, watchlist_name, macro) str
        +briefing_summary(assessments, macro) str
        -_exposure_table(assessments) str
        -_macro_alerts(assessments, macro) list
        -_priority_key(a) tuple
    }

    class TIRFBuilder {
        +build_research_report(ticker, bundle, opinions, cio, debate, source) ResearchReport
    }

    class TIRFStore {
        +persist(report) str
        +get_report(report_id) dict
        +get_latest(ticker) dict
        +get_evidence(report_id) list
        +get_reasoning(report_id) list
        +list_reports(limit) list
    }

    class AgentMemory {
        +recall_block(role_key, symbol) str
        +save_note(role_key, note, symbol) None
        +reflect(role_key) None
    }

    class Models {
        +chains() dict
        +chain_names() list
        +resolve_chain_name(role_key) str
        +resolve_chain(role_key) list
        +resolve(role_key) tuple
        +new_chain_links() list
        +nim_settings() dict
        +claude_settings() dict
        +openai_settings() dict
        +load_config() dict
    }

    class Delivery {
        +produce_report(symbol, lang, reports_dir, source) CommitteeArtifact
    }

    Engine --> Bundle : gather data
    Engine --> Roles : system prompts
    Engine --> Debate : run rounds 2-3
    Engine --> AgentMemory : recall + save notes
    Engine --> Models : resolve backends
    Engine --> TIRFBuilder : build TIRF report
    TIRFBuilder --> TIRFStore : persist
    Delivery --> Engine : run_committee
    WMAAgent --> Bundle : gather_bundle
    WMAAgent --> Engine : ask_role
    WMAReport --> WMAAgent : assessments
```

---

## LLM Backend Model Architecture

```mermaid
flowchart LR
    subgraph Roles["Role → Named Chain Mapping"]
        SPEC_ROLES["Specialists\nmarket / macro / equity\nindustry / valuation / quant\netf / risk / catalyst"] --> STD_CHAIN
        MOD_ROLE["Moderator"] --> STD_CHAIN

        STD_CHAIN["standard chain\n1. OpenAI gpt-5.5 200k\n2. Claude opus-4-8 200k\n3. NIM kimi last resort"]

        WMA_ROLE["WMA / macro\nper-security + snapshot"] --> PREM_CHAIN
        CIO_ROLE["CIO"] --> PREM_CHAIN

        PREM_CHAIN["premium chain\n1. Claude opus-4-8 200k\n2. OpenAI gpt-5.5 200k\n3. NIM kimi last resort"]

        TRANS_ROLE["Translator"] --> TRANS_CHAIN["translation chain\n1. Claude sonnet-4-6\n2. OpenAI gpt-5.4-mini\n3. NIM kimi last resort"]
    end

    subgraph Retry["NIM Retry Logic"]
        NIM_POST["POST /chat/completions"] --> HTTP_CODE{HTTP status}
        HTTP_CODE -- 200 --> SUCCESS["Return text + tokens"]
        HTTP_CODE -- "429/503" --> WAIT["sleep Retry-After\nor exponential backoff"]
        WAIT --> NIM_POST
        HTTP_CODE -- timeout --> FALL["Fall through chain\nno retry on timeout"]
        HTTP_CODE -- "other 4xx/5xx" --> FALL
    end
```

---

## Database Schema Overview

All committee-side state lives in **`committee.db`** (a sibling of the portfolio
`cio.db`), kept separate so the agents' accruing notes and embeddings don't bloat the
main database. It holds three families of tables: token usage, the sent/returned
transcript (dev dashboard), and the nine TIRF tables below. `research_reports` is the
parent; the other eight reference it by `report_id`. Versioning is per-ticker:
`version = MAX(version for ticker) + 1`, assigned inside the persist transaction (safe
because the runtime is single-operator — no write race). The schema is created
idempotently on every connect, and `_SCHEMA` uses `CREATE TABLE IF NOT EXISTS`, so a
fresh checkout self-initialises.

`agent_memory.py` also uses `committee.db` but for a different purpose: per-agent
persistent notes scoped `committee:{role_key}`, with 768-dim embeddings for semantic
dedup (`CIO_DEDUP_MAXDIST`, default 0.45 L2) and a tight injection budget
(`CIO_AGENT_MEM_BUDGET`, default 400 tokens). It runs in WAL mode so the parallel
specialists can write their notes near-simultaneously without blocking.



```mermaid
erDiagram
    research_reports {
        INTEGER id PK
        TEXT report_id UK
        TEXT ticker
        TEXT agent
        INTEGER version
        TEXT as_of
        TEXT source
        TEXT prompt_version
        TEXT agent_version
        TEXT data_hash
        TEXT final_recommendation
        TEXT confidence
        REAL evidence_quality
        INTEGER explainability
        INTEGER traceability
        INTEGER auditability
        INTEGER reproducibility
        INTEGER challenge_coverage
        INTEGER tirf_score
        TEXT review_json
        TEXT created_at
    }

    evidence_items {
        INTEGER id PK
        TEXT report_id FK
        TEXT role_key
        TEXT source
        TEXT source_tier
        TEXT date
        TEXT finding
        TEXT impact
        TEXT relevance
        TEXT confidence
        INTEGER reliability_score
        INTEGER recency_score
        INTEGER relevance_score
        INTEGER item_score
    }

    assumptions {
        INTEGER id PK
        TEXT report_id FK
        TEXT role_key
        TEXT name
        TEXT value
        TEXT confidence
    }

    reasoning_chains {
        INTEGER id PK
        TEXT report_id FK
        TEXT role_key
        INTEGER step_no
        TEXT statement
    }

    counterarguments {
        INTEGER id PK
        TEXT report_id FK
        TEXT role_key
        TEXT argument
    }

    source_references {
        INTEGER id PK
        TEXT report_id FK
        TEXT role_key
        TEXT reference
        TEXT source_tier
        INTEGER reliability_score
    }

    committee_sessions {
        INTEGER id PK
        TEXT report_id FK
        TEXT run_id
        TEXT ticker
        TEXT source
        INTEGER debate_on
        INTEGER n_specialists
        INTEGER n_challenges
    }

    committee_challenges {
        INTEGER id PK
        TEXT report_id FK
        TEXT run_id
        TEXT challenger_key
        TEXT target_key
        TEXT challenge
    }

    committee_responses {
        INTEGER id PK
        TEXT report_id FK
        INTEGER challenge_id FK
        TEXT responder_key
        TEXT response
    }

    research_reports ||--o{ evidence_items : "report_id"
    research_reports ||--o{ assumptions : "report_id"
    research_reports ||--o{ reasoning_chains : "report_id"
    research_reports ||--o{ counterarguments : "report_id"
    research_reports ||--o{ source_references : "report_id"
    research_reports ||--o{ committee_sessions : "report_id"
    research_reports ||--o{ committee_challenges : "report_id"
    committee_challenges ||--o{ committee_responses : "challenge_id"
```

---

## Environment Configuration Map

```mermaid
flowchart LR
    subgraph ENV_VARS["Key Environment Variables"]
        CIO_PARALLEL["CIO_PARALLEL=on/off\nspecialist + debate parallelism"]
        CIO_MAX_CONC["CIO_MAX_CONCURRENCY=8\nmax parallel LLM calls"]
        CIO_DEBATE["CIO_DEBATE=on/off\nRounds 2-3 toggle"]
        CIO_DEBATE_PAIRS["CIO_DEBATE_MAX_PAIRS=2\ncross-exam pair limit"]
        CIO_WMA_CONC["CIO_WMA_CONCURRENCY=4\nmax parallel WMA symbols"]
        NIM_KEY["NVIDIA_API_KEY\nNIM authentication"]
        NIM_TIMEOUT["CIO_NIM_TIMEOUT=300s\nNIM request timeout"]
        NIM_RETRIES["CIO_NIM_MAX_RETRIES=3\nNIM retry count"]
        NIM_TOKENS["CIO_NIM_MAX_TOKENS=2048\nmax output tokens"]
        MODELS_CFG["CIO_MODELS_CONFIG\npath to committee_models.yaml"]
        SEC_UA["CIO_SEC_UA\nEDGAR user-agent (enables EDGAR)"]
        FINNHUB_KEY["FINNHUB_API_KEY\nFinnhub data (enables analyst/earnings)"]
    end

    CIO_PARALLEL --> ENGINE2[engine._gather_bounded]
    CIO_MAX_CONC --> ENGINE2
    CIO_DEBATE --> ENGINE2
    CIO_DEBATE_PAIRS --> DEBATE2[debate.select_debate_pairs]
    CIO_WMA_CONC --> WMA2[watchlist_monitor.agent]
    NIM_KEY --> NIM_B2[_ask_nim]
    NIM_TIMEOUT --> NIM_B2
    NIM_RETRIES --> NIM_B2
    NIM_TOKENS --> NIM_B2
    MODELS_CFG --> MODELS2[committee.models.load_config]
    SEC_UA --> EDGAR_B[cio.data EDGAR]
    FINNHUB_KEY --> FINNHUB_B[cio.data Finnhub]
```
