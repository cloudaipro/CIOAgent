# TIRF — Test Plan

**Scope:** validates every acceptance criterion in `docs/TIRF-PRD.md` §16.
**Runner:** `pytest tests/test_tirf.py` (+ full-suite regression `pytest -q`).
**Principles:** fully offline — `ask_role` is monkeypatched, `store.DB_PATH` is routed
to a `tmp_path` DB, no network, no real LLM. Every TIRF function is asserted
never-raises on malformed input.

---

## 1. Test environment & isolation

| Concern | Approach |
|---|---|
| No LLM / network | Monkeypatch `cio.committee.engine.ask_role` to an async stub returning canned TIRF-rich yaml. |
| No real DB writes | `monkeypatch.setattr("cio.committee.tirf.store.DB_PATH", tmp_path/"committee.db")`. Per-agent memory already routed to a temp db by the existing autouse fixture in `test_committee.py`; the TIRF suite adds its own. |
| Determinism | All scoring/validation/versioning is pure Python — assert exact numbers. |
| Offline embedding | `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` (already set by the committee suite). |

A shared `make_specialist_yaml(...)` helper builds well-formed specialist yaml with
N evidence / N counterarguments so boundary tests can dial completeness up and down.

---

## 2. Test matrix (maps 1:1 to PRD §16 acceptance criteria)

### A. Zero-cost invariant
| Test | Asserts | AC |
|---|---|---|
| `test_tirf_adds_zero_llm_calls` | Run `run_committee` (debate off) with a call-counting `ask_role`; count = 9 specialists + 1 moderator + 1 CIO = 11. TIRF adds none. | A1 |

### B. Data contract & extraction
| Test | Asserts | AC |
|---|---|---|
| `test_extract_full_yaml` | A full yaml → all five collections populated with correct field values. | B1 |
| `test_extract_bare_yaml` | vote/confidence/reason only → empty collections, valid object, no raise. | B2 |
| `test_extract_malformed_shapes` | evidence as list-of-strings, assumptions as list, reasoning as dict, `{"_raw":...}` → no raise, best-effort. | B3 |

### C. Evidence scoring
| Test | Asserts | AC |
|---|---|---|
| `test_classify_source_tiers` | SEC→100, earnings call→90, guidance→85, industry→80, news→60, social→20, unknown→50. | C1 |
| `test_recency_buckets` | dates at 3/20/60/200 days vs as_of → 100/80/60/30; undated→30; future→30. | C2 |
| `test_relevance_and_composite` | direct/related/indirect→100/70/40; composite = round(.5·rel+.3·rlv+.2·rec) on a worked example. | C3 |

### D. Validation & metrics
| Test | Asserts | AC |
|---|---|---|
| `test_evidence_gate_boundary` | 2 items → gate False; 3 → True. | D1 |
| `test_counter_gate_boundary` | 2 → False; 3 → True. | D1 |
| `test_full_report_scores_high` | fully-populated report → tirf_score ≥ 80. | D2 |
| `test_empty_report_scores_low` | bare report → tirf_score < 40, no raise. | D2 |
| `test_all_five_metrics_in_range` | all five metrics present and in [0,100]. | D3 |

### E. Versioning & reproducibility
| Test | Asserts | AC |
|---|---|---|
| `test_version_autoincrement` | two persists same ticker → versions 1 then 2. | E1 |
| `test_data_hash_stable_and_sensitive` | identical bundle → same hash; changed price → different hash. | E2 |
| `test_manifest_pins_and_verify` | manifest has 4 pins; `verify` True on same bundle, False on changed. | E3 |
| `test_reproducibility_score_100` | complete manifest → reproducibility metric = 100. | E3 |

### F. Persistence & retrieval
| Test | Asserts | AC |
|---|---|---|
| `test_persist_roundtrip` | persist → `get_report` returns row with matching ticker/version/scores. | F1 |
| `test_persist_children` | `get_evidence`/`get_assumptions`/`get_sources`/`get_counterarguments` return persisted children. | F2 |
| `test_store_never_raises_bad_db` | persist to an impossible path → "" , no raise. | F3 |

### G. Challenge protocol
| Test | Asserts | AC |
|---|---|---|
| `test_challenges_persisted` | a debate result with 2 exchanges → 2 `committee_challenges` rows + linked responses; session row counts match. | G1 |

### H. CIO review
| Test | Asserts | AC |
|---|---|---|
| `test_cio_review_scorecard` | five sub-scores in [0,100]; verdict ∈ {pass, review}; a weak report flags. | H1 |

### I. Dossier
| Test | Asserts | AC |
|---|---|---|
| `test_dossier_has_11_sections` | all 11 required `## ` headers present. | I1 |
| `test_dossier_empty_safe` | empty report → renders, contains `_Insufficient data._`, no raise. | I2 |

### J. Integration & non-regression
| Test | Asserts | AC |
|---|---|---|
| `test_run_committee_attaches_tirf` | end-to-end (stubbed `ask_role`) → `result.tirf` populated, persisted, tirf_score>0. | J1 |
| `test_report_contains_tirf_appendix` | `build_report` output contains "TIRF Transparency Appendix" + "Evidence Ledger". | J2 |
| existing `tests/test_committee.py` et al. | unchanged and green. | J3 |

### K. Docs
| Check | Asserts | AC |
|---|---|---|
| presence | `docs/TIRF-PRD.md` and `docs/TIRF-TEST-PLAN.md` exist. | K1 |

---

## 3. Exit criteria

1. `pytest tests/test_tirf.py` — **all green**.
2. `pytest -q` (full suite) — **no regressions**.
3. Every PRD §16 criterion (A1…K1) has at least one green test above.
4. A manual `python -m cio.committee.tirf generate <SYMBOL>` smoke (optional, needs a
   backend) renders a dossier and persists a versioned report.

---

## 4. Out of scope (v1)

* Live-LLM behavioural quality of TIRF deliverables (the framework *measures* quality;
  it does not assert a model will always meet the gates).
* PDF binary rendering (covered by the existing `test_pdf_report.py`; the dossier reuses
  the same `markdown_to_pdf`).
* Dashboard HTML views (optional surface; store API is the tested contract).
