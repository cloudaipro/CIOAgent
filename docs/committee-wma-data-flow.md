# Committee & WMA — Data Flow

Data flow tracks how information is gathered, transformed, and consumed at each stage.

## The Central Data Object: the Bundle

Almost everything starts from one structure — the **bundle** produced by
`gather_bundle(symbol)`. It is a plain dict with 11 keys:

| Key | Source | Notes |
|-----|--------|-------|
| `symbol` / `resolved` | `cio.stock.normalize_symbol` | `resolved=None` is the "no data" sentinel that aborts a run |
| `quote` | `cio.stock.get_quote` | close, change_pct, volume |
| `fundamentals` | `cio.stock.fundamentals` | PE, FWD_PE, PB, EPS, ROE, margin, market cap, 52W range, quarterly revenue; also carries `quoteType` used to set `is_etf` |
| `ta_signals` | `cio.stock.run_strategy` | latest label for rsi / macd / stoch / trix / kdj |
| `is_etf` | derived | drops the ETF specialist when false |
| `as_of` | `datetime.utcnow()` | snapshot timestamp; feeds TIRF recency scoring |
| `filings` | `cio.data` EDGAR | opt-in (`CIO_SEC_UA`); recent 10-K/10-Q/8-K |
| `analyst` | `cio.data` Finnhub | opt-in (`FINNHUB_API_KEY`); buy/hold/sell counts; skipped for ETFs |
| `earnings` | `cio.data` Finnhub | opt-in; next report date + EPS estimate/actual |

`format_bundle(bundle)` then renders this dict into a **compact ~10-line labelled text
block** that is what actually gets injected into LLM prompts. Missing fields render as
`N/A (no source)` — the prompts forbid the models from inventing numbers, so a missing
field stays missing rather than being hallucinated. The bundle is the *authoritative
source of numbers*; everything else (news, macro, catalysts) is explicitly labelled
qualitative judgement.

The bundle is shared verbatim between the WMA (`monitor_symbol`) and the committee
(`run_specialist`), which is why a single data layer serves both subsystems.

---

## Overall System Data Flow

```mermaid
flowchart TD
    subgraph External["External Data Sources"]
        YF[Yahoo Finance\nprice / fundamentals / TA]
        EDGAR[SEC EDGAR\n10-K / 10-Q / 8-K filings]
        FINNHUB[Finnhub\nanalyst recs / earnings]
        FIRECRAWL[Firecrawl / Web\novernight headlines]
        MACRO_NEWS[Web Search\nmacro/geopolitical headlines]
    end

    subgraph Bundle["Bundle Layer (cio.committee.bundle)"]
        GB[gather_bundle\nresolve + fetch all sources]
        FB[format_bundle\ncompact labeled text]
    end

    subgraph WMALayer["WMA Layer (cio.watchlist_monitor)"]
        MACRO_SNAP[global_macro_snapshot\n1 LLM call / run]
        MON_SYM[monitor_symbol\n1 LLM call / security]
        WMA_ASSESS[assessment dict\n25 fields]
        BUILD_BRIEF[build_briefing\nmarkdown]
    end

    subgraph CommitteeLayer["Committee Layer (cio.committee)"]
        R1[Round 1 opinions\n9 specialist dicts]
        DEBATE_EX[Debate exchanges\nchallenge + response]
        R3[Round 3 opinions\nrevised dicts]
        MOD_CONS[Moderator consensus\nyaml]
        CIO_FINAL[CIO final decision\nyaml]
    end

    subgraph TIRF["TIRF Layer (cio.committee.tirf)"]
        EXTRACT[extract\nevidence/assumptions\nreasoning/counter/sources]
        SCORE[scoring\nitem_score per evidence]
        VALIDATE[validate\ncompute_metrics]
        REVIEW[review\ncio_review scorecard]
        PERSIST[persist\ncommittee.db 9 tables]
    end

    subgraph Memory["Agent Memory (cio.committee.agent_memory)"]
        NOTES[memory_note\nper role per ticker]
        REFLECT[reflect\nhot/warm/cold tiers]
    end

    subgraph Output["Output Layer"]
        PDF[PDF report]
        MARKDOWN[Markdown report]
        TELEGRAM[Telegram message]
        DASHBOARD[Dev Dashboard]
        DB_TRANS[committee_transcript\ntable]
        DB_USAGE[token_usage\ntable]
    end

    YF --> GB
    EDGAR --> GB
    FINNHUB --> GB
    GB --> FB
    FB --> MON_SYM
    FB --> R1

    FIRECRAWL --> MON_SYM
    MACRO_NEWS --> MACRO_SNAP

    MACRO_SNAP --> BUILD_BRIEF
    MON_SYM --> WMA_ASSESS
    WMA_ASSESS --> BUILD_BRIEF
    BUILD_BRIEF --> PDF
    BUILD_BRIEF --> MARKDOWN
    BUILD_BRIEF --> TELEGRAM

    R1 --> DEBATE_EX
    R1 --> R3
    DEBATE_EX --> R3
    R3 --> MOD_CONS
    R3 --> CIO_FINAL
    MOD_CONS --> CIO_FINAL
    CIO_FINAL --> PDF
    CIO_FINAL --> TELEGRAM

    R3 --> EXTRACT
    CIO_FINAL --> EXTRACT
    DEBATE_EX --> EXTRACT
    EXTRACT --> SCORE
    SCORE --> VALIDATE
    VALIDATE --> REVIEW
    REVIEW --> PERSIST

    R1 --> NOTES
    CIO_FINAL --> NOTES
    NOTES --> REFLECT
    REFLECT --> NOTES

    R1 --> DB_TRANS
    MOD_CONS --> DB_TRANS
    CIO_FINAL --> DB_TRANS
    DB_TRANS --> DASHBOARD
    DB_USAGE --> DASHBOARD
```

---

## Bundle Data Flow (gather_bundle)

```mermaid
flowchart LR
    SYM[symbol input] --> NORM[normalize_symbol\nticker → canonical]
    NORM --> QUOTE[get_quote\nclose / change_pct / volume]
    NORM --> FUND[fundamentals\nPE / FWD_PE / PB / EPS\nROE / margin / mktcap\n52W high-low / revenue_q]
    NORM --> TA[run_strategy\nrsi / macd / stoch / trix / kdj\n→ latest signal label]
    NORM --> EXT{CIO_SEC_UA\nor FINNHUB_API_KEY?}
    EXT -- yes --> EDGAR2[recent_filings\nform / filed date]
    EXT -- yes --> ANALYST[analyst_recs\nstrong_buy/buy/hold/sell counts]
    EXT -- yes --> EARNINGS[earnings_calendar\nnext date / eps_est / eps_actual]
    EXT -- no --> EMPTY_EXT["filings=empty, analyst=None,<br/>earnings=None"]

    QUOTE --> BUNDLE_DICT
    FUND --> BUNDLE_DICT
    TA --> BUNDLE_DICT
    EDGAR2 --> BUNDLE_DICT
    ANALYST --> BUNDLE_DICT
    EARNINGS --> BUNDLE_DICT
    EMPTY_EXT --> BUNDLE_DICT

    BUNDLE_DICT[bundle dict\n11 keys] --> FORMAT[format_bundle\ncompact labeled text block\n~10 lines]
    FORMAT --> PROMPT[injected into\nLLM user_prompt]
```

---

## WMA Assessment Data Flow

```mermaid
flowchart TD
    BUNDLE_TEXT[bundle_text\nformatted string] --> USER_PROMPT[user_prompt\nDATA + OVERNIGHT_HEADLINES]
    HEADLINES[web headlines\n≤5 items] --> USER_PROMPT
    WMA_SYS[WMA_SYSTEM\nprompt constant] --> LLM_CALL[ask_role wma chain]
    USER_PROMPT --> LLM_CALL
    LLM_CALL --> RAW_YAML[raw LLM text\nfenced yaml block]
    RAW_YAML --> PARSE[parse_yaml_block]
    PARSE --> VALIDATORS[Field normalizers\none_of / _conviction / _score100 / _as_list]
    VALIDATORS --> ASSESS_DICT[assessment dict\n25 fields]

    ASSESS_DICT --> STATUS[overall_status\nbullish/neutral/bearish]
    ASSESS_DICT --> CONV[conviction_score 0-100]
    ASSESS_DICT --> REC[recommendation\nBuy/Add/Hold/Monitor/Reduce/Sell]
    ASSESS_DICT --> EVT[event_importance\nlow/medium/high/critical]
    ASSESS_DICT --> EXT_RISK[external_risk_score\nmacro/geo/commodity/currency sensitivity]
    ASSESS_DICT --> THESIS[investment_thesis_change\nunchanged/positive/negative]
    ASSESS_DICT --> LISTS[key_positive_events\nkey_negative_events / new_risks\nupcoming_catalysts]
    ASSESS_DICT --> ESCALATE_FIELD[escalate bool\ncommittee trigger]
```

---

## Committee Specialist Data Flow

```mermaid
flowchart TD
    BUNDLE_TEXT2[bundle_text] --> SP_PROMPT[specialist user_prompt\nSYMBOL + required fields + DATA]
    MEM_BLOCK[agent_memory.recall_block\nrole_key + ticker] --> SYS_PROMPT[specialist system_prompt\n+ FIGURE_RULE + memory_block]
    ROLE[role definition\nkey/title/system_prompt/fields] --> SYS_PROMPT
    SYS_PROMPT --> ASK[ask_role\nrole_key → backend]
    SP_PROMPT --> ASK
    ASK --> RAW[raw text\nfenced yaml block]
    RAW --> PARSE2[parse_yaml_block]
    PARSE2 --> OP_DICT[opinion dict]

    OP_DICT --> VOTE[vote: BUY/HOLD/SELL]
    OP_DICT --> CONF[confidence: 0-100]
    OP_DICT --> REASON[reason: string]
    OP_DICT --> ROLE_FIELDS[role-specific fields\ne.g. financial_health / trend_score]
    OP_DICT --> TIRF_FIELDS[TIRF fields\nevidence / assumptions\nreasoning / counterarguments / sources]
    OP_DICT --> MEM_NOTE[memory_note\n→ sanitize → save]

    MEM_NOTE --> SANITIZE[note_sanitizer.sanitize\nLLM call: strip stale figures]
    SANITIZE --> REGEX_FW[save_note\nregex firewall]
    REGEX_FW --> DB_NOTES[agent_memory SQLite]
```

---

## TIRF Data Flow

TIRF (Transparent Investment Research Framework) is the audit layer. Its defining
property: it adds **zero LLM calls**. Every specialist already emits its evidence,
assumptions, reasoning, counterarguments, and sources *in the same yaml block* as its
vote (enforced by `_TIRF_RULE` in the role prompts). `run_specialist` stashes that raw
parse under `_parsed`, and TIRF reads it back out — no second model round-trip.

The pipeline is `extract → score → validate → review → persist`:

- **extract** (`extract.py`) — tolerantly coerces the messy yaml each model produces
  (list-of-dicts, list-of-strings, maps, bare scalars) into typed
  `SpecialistResearch` objects. Missing keys yield empty lists, which `validate` later
  scores as *low completeness* rather than erroring.
- **score** (`scoring.py`) — each evidence item gets a 0–100 `item_score` =
  **0.50·reliability + 0.30·relevance + 0.20·recency**.
  - *Reliability* is classified from the free-text source: SEC filing 100, earnings
    call 90, company guidance 85, industry research 80, news 60, social media 20,
    unknown 50.
  - *Recency* vs the bundle's `as_of`: <7 days 100, <30 days 80, <90 days 60, older or
    undated or future-dated 30.
  - *Relevance*: direct 100, related 70, indirect 40.
- **validate** (`validate.py`) — rolls per-specialist sub-metrics into the **five
  report-level success metrics** (each 0–100): explainability, traceability,
  auditability, reproducibility, challenge_coverage. Their mean is `tirf_score`. The
  completeness gates are 3 evidence items and 3 counterarguments per specialist.
- **review** (`review.py`) — the **CIO review scorecard**: five dimensions (evidence
  quality, assumption quality, counterargument coverage, source reliability, reasoning
  consistency) each checked against a pass threshold; verdict is `pass` only if all
  pass, else `review` with human-readable flags.
- **repro** (`repro.py`) — pins reproducibility: a canonical, key-sorted JSON snapshot
  of the decision-relevant bundle fields plus its `sha256`, and `prompt_version`
  (`tirf-1.0`) / `agent_version` (`committee-1.0`) stamps. Identical inputs ⇒ identical
  hash, so a run can be replayed and audited.
- **persist** (`store.py`) — writes the report and all children into **9 tables in
  `committee.db` in a single transaction**, assigning a per-ticker incrementing
  `version`. Never raises — a persistence failure logs and the run still completes.

```mermaid
flowchart LR
    OPINIONS[Round-3 opinions\neach with _parsed dict] --> EXTRACT2[extract.extract_from_opinion\nper specialist]
    CIO_OUT[CIO yaml dict] --> RPT_META[ResearchReport\nticker/as_of/recommendation/confidence]
    BUNDLE_IN[bundle dict] --> REPRO[repro.manifest\nprompt_version / agent_version\ndata_hash / data_snapshot]
    DEBATE_IN[debate_result\nexchanges] --> CHALLENGES[report.challenges list]

    EXTRACT2 --> SP_REPORT[SpecialistReport\nevidence / assumptions\nreasoning / counterarguments / sources]
    SP_REPORT --> SCORING[scoring.score_specialist\nitem_score per evidence\nsource_tier / reliability / recency / relevance]
    SCORING --> SCORED_SP[scored SpecialistReport]
    SCORED_SP --> RPT_META
    REPRO --> RPT_META
    CHALLENGES --> RPT_META

    RPT_META --> METRICS[validate.compute_metrics\nexplainability / traceability\nauditability / reproducibility\nchallenge_coverage / tirf_score]
    METRICS --> REVIEW2[review.cio_review\nscorecard dict]
    REVIEW2 --> FULL_RPT[ResearchReport\nfully populated]

    FULL_RPT --> DB_WRITE[tirf.store.persist\ncommittee.db transaction]
    DB_WRITE --> research_reports[(research_reports)]
    DB_WRITE --> evidence_items[(evidence_items)]
    DB_WRITE --> assumptions_tbl[(assumptions)]
    DB_WRITE --> reasoning_chains[(reasoning_chains)]
    DB_WRITE --> counterarguments[(counterarguments)]
    DB_WRITE --> source_references[(source_references)]
    DB_WRITE --> committee_sessions[(committee_sessions)]
    DB_WRITE --> committee_challenges[(committee_challenges)]
    DB_WRITE --> committee_responses[(committee_responses)]
```
