# Memory / Context: CFOAgent (MemCore) vs Hermes vs OpenClaw

CFOAgent's MemCore is designed to be **≥ Hermes and OpenClaw** on every memory/
context axis, while adding finance-grade guarantees neither rival has.

## Feature matrix

| Capability | Hermes | OpenClaw | **CFOAgent (MemCore)** |
|---|---|---|---|
| Injected at session start | ✅ `MEMORY.md`+`USER.md` (fixed ~1.3k tok) | ✅ `MEMORY.md` hot auto-load | ✅ profile + pinned notes + digest, **adaptive importance×recency within a tiktoken budget** |
| Per-user profile | ✅ `USER.md` | ⚠️ via files | ✅ `user_profile` (per scope) |
| Keyword search | ✅ FTS5 | ✅ BM25 | ✅ FTS5 (external-content) |
| Semantic search | ✅ fastembed (133 MB) | ✅ sqlite-vec, **default needs `OPENAI_API_KEY`** | ✅ fastembed (67 MB quantized) + sqlite-vec, **no API key** |
| Hybrid fusion | ✅ RRF (Milvus) | ✅ hybrid | ✅ Reciprocal Rank Fusion |
| Works offline / no key | ⚠️ key for some backends | ⚠️ key for default embeddings | ✅ **fully offline, no key** (model cached in `data/models/`) |
| Save enforcement | ✅ `nudge_interval` | ⚠️ reactive only | ✅ periodic nudge **+ PreCompact hook flush + deterministic auto-capture** |
| Pre-compaction flush | ⚠️ summarization | ✅ flush before compaction | ✅ **PreCompact hook** (real SDK signal) → checkpoint |
| Bounded long-run growth | ⚠️ bounded files | ⚠️ files grow | ✅ **rolling sessions** (digest+reseed) **+ importance-decay eviction** |
| Conversation history search | ✅ FTS5 | ✅ vector | ✅ `conv_turns` hybrid (notes **and** turns) |
| Learning loop / skills | ✅ skill docs | ⚠️ limited | ✅ `playbooks` (steps reference tools) |
| Storage | SQLite + vec DB | SQLite + sqlite-vec | single auditable SQLite (`cfo.db`) |

## Where CFOAgent is strictly better

1. **Figures firewall (correctness).** Numbers are *never* memorized — the store
   rejects financial figures and the agent recomputes them from `transactions`/
   `prices`. Markdown-memory systems (Hermes/OpenClaw) will happily store
   "AAPL is worth $230" and let it go stale. CFOAgent structurally cannot.
2. **No API key, fully offline.** OpenClaw's default semantic search needs
   `OPENAI_API_KEY`; CFOAgent matches its hybrid search with a local quantized
   model and `sqlite-vec`, runnable with zero external services.
3. **Adaptive injection budget.** Injected context is ranked by importance×recency
   and packed to a measured token budget, rather than fixed-size files.
4. **Auditability.** Every note carries `source` (user/agent/auto/legacy),
   timestamps, and `importance`/`hits`; eviction is principled and reversible —
   suited to a finance tool.
5. **Defense at every lossy boundary.** Facts are flushed before *both*
   compaction (PreCompact hook) and rolling-session forks, and the figures that
   matter live outside memory entirely.

## Sources
- Hermes memory: https://hermes-agent.nousresearch.com/docs/user-guide/features/memory ·
  fastembed embeddings: https://www.bluehost.com/blog/hermes-agent-memory/
- OpenClaw builtin memory + sqlite-vec + embedding providers:
  https://docs.openclaw.ai/concepts/memory-builtin ·
  https://docs.openclaw.ai/reference/memory-config
- Hybrid (RRF) reference: https://vectorize.io/articles/openclaw-vs-hermes-agent-memory
