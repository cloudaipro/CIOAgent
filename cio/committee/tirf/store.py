"""
store.py — TIRF persistence + retrieval (PRD §10 / proposal §15).

Nine tables in committee.db (alongside token_usage / committee_transcript). Mirrors
the transcript.py posture exactly: module-level ``DB_PATH`` (monkeypatchable in
tests), idempotent schema, ALTER-migration tolerance, and **never raises** — a
persistence failure logs and returns gracefully so a committee run always completes.

Versioning (PRD §8): version = MAX(version for ticker) + 1, assigned inside the
persist transaction.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path

from cio import db as _db

from .models import ResearchReport

log = logging.getLogger(__name__)

# Committee-side DB, alongside cio.db (mirrors transcript.py / usage.py).
DB_PATH: Path = _db.DB_PATH.parent / "committee.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_reports (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id           TEXT UNIQUE,
    ticker              TEXT,
    agent               TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    as_of               TEXT,
    source              TEXT,
    prompt_version      TEXT,
    agent_version       TEXT,
    data_hash           TEXT,
    data_snapshot       TEXT,
    final_recommendation TEXT,
    confidence          TEXT,
    evidence_quality    REAL,
    explainability      INTEGER,
    traceability        INTEGER,
    auditability        INTEGER,
    reproducibility     INTEGER,
    challenge_coverage  INTEGER,
    tirf_score          INTEGER,
    review_json         TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_research_ticker ON research_reports(ticker, version);

CREATE TABLE IF NOT EXISTS evidence_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id     TEXT,
    role_key      TEXT,
    source        TEXT,
    source_tier   TEXT,
    date          TEXT,
    finding       TEXT,
    impact        TEXT,
    relevance     TEXT,
    confidence    TEXT,
    reliability_score INTEGER,
    recency_score INTEGER,
    relevance_score INTEGER,
    item_score    INTEGER,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_evidence_report ON evidence_items(report_id);

CREATE TABLE IF NOT EXISTS assumptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   TEXT,
    role_key    TEXT,
    name        TEXT,
    value       TEXT,
    confidence  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_assumptions_report ON assumptions(report_id);

CREATE TABLE IF NOT EXISTS reasoning_chains (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   TEXT,
    role_key    TEXT,
    step_no     INTEGER,
    statement   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reasoning_report ON reasoning_chains(report_id);

CREATE TABLE IF NOT EXISTS counterarguments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   TEXT,
    role_key    TEXT,
    argument    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_counter_report ON counterarguments(report_id);

CREATE TABLE IF NOT EXISTS source_references (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       TEXT,
    role_key        TEXT,
    reference       TEXT,
    source_tier     TEXT,
    reliability_score INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_source_report ON source_references(report_id);

CREATE TABLE IF NOT EXISTS committee_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id     TEXT,
    run_id        TEXT,
    ticker        TEXT,
    source        TEXT,
    debate_on     INTEGER,
    n_specialists INTEGER,
    n_challenges  INTEGER,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS committee_challenges (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id        TEXT,
    run_id           TEXT,
    challenger_key   TEXT,
    challenger_title TEXT,
    target_key       TEXT,
    target_title     TEXT,
    challenge        TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_challenge_report ON committee_challenges(report_id);

CREATE TABLE IF NOT EXISTS committee_responses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id     TEXT,
    challenge_id  INTEGER,
    responder_key TEXT,
    response      TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a plain sqlite3 connection and ensure schema. No vec extension needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

def latest_version(ticker: str, db_path: Path | None = None) -> int:
    """Highest stored version for a ticker (0 if none). Never raises."""
    path = db_path if db_path is not None else DB_PATH
    try:
        conn = _connect(path)
        row = conn.execute(
            "SELECT MAX(version) AS v FROM research_reports WHERE ticker=?",
            (str(ticker).upper(),),
        ).fetchone()
        conn.close()
        return int(row["v"]) if row and row["v"] is not None else 0
    except Exception as exc:
        log.warning("latest_version failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Persist
# ---------------------------------------------------------------------------

def persist(report: ResearchReport, db_path: Path | None = None) -> str:
    """Write a ResearchReport + all children in one transaction. Assigns
    report.report_id (if empty) and report.version. Returns the report_id, or ""
    on failure. Never raises.
    """
    path = db_path if db_path is not None else DB_PATH
    try:
        if not report.report_id:
            report.report_id = uuid.uuid4().hex[:12]
        ticker = str(report.ticker).upper()
        conn = _connect(path)
        with conn:
            # Version inside the transaction (single-operator runtime — no race).
            row = conn.execute(
                "SELECT MAX(version) AS v FROM research_reports WHERE ticker=?",
                (ticker,),
            ).fetchone()
            report.version = (int(row["v"]) + 1) if row and row["v"] is not None else 1

            m = report.metrics or {}
            conn.execute(
                "INSERT INTO research_reports "
                "(report_id,ticker,agent,version,as_of,source,prompt_version,agent_version,"
                " data_hash,data_snapshot,final_recommendation,confidence,evidence_quality,"
                " explainability,traceability,auditability,reproducibility,challenge_coverage,"
                " tirf_score,review_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    report.report_id, ticker, report.agent, report.version,
                    report.as_of, report.source, report.prompt_version, report.agent_version,
                    report.data_hash, report.data_snapshot,
                    str(report.final_recommendation or ""), str(report.confidence),
                    report.evidence_quality,
                    m.get("explainability"), m.get("traceability"), m.get("auditability"),
                    m.get("reproducibility"), m.get("challenge_coverage"), m.get("tirf_score"),
                    json.dumps(report.review or {}),
                ),
            )
            rid = report.report_id

            for sp in report.specialists:
                for ev in sp.evidence:
                    conn.execute(
                        "INSERT INTO evidence_items "
                        "(report_id,role_key,source,source_tier,date,finding,impact,relevance,"
                        " confidence,reliability_score,recency_score,relevance_score,item_score) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, sp.role_key, ev.source, ev.source_tier, ev.date, ev.finding,
                         ev.impact, ev.relevance, ev.confidence, ev.reliability_score,
                         ev.recency_score, ev.relevance_score, ev.item_score),
                    )
                for a in sp.assumptions:
                    conn.execute(
                        "INSERT INTO assumptions (report_id,role_key,name,value,confidence) "
                        "VALUES (?,?,?,?,?)",
                        (rid, sp.role_key, a.name, a.value, a.confidence),
                    )
                for st in sp.reasoning:
                    conn.execute(
                        "INSERT INTO reasoning_chains (report_id,role_key,step_no,statement) "
                        "VALUES (?,?,?,?)",
                        (rid, sp.role_key, st.step_no, st.statement),
                    )
                for c in sp.counterarguments:
                    conn.execute(
                        "INSERT INTO counterarguments (report_id,role_key,argument) "
                        "VALUES (?,?,?)",
                        (rid, sp.role_key, c.argument),
                    )
                for sr in sp.sources:
                    conn.execute(
                        "INSERT INTO source_references "
                        "(report_id,role_key,reference,source_tier,reliability_score) "
                        "VALUES (?,?,?,?,?)",
                        (rid, sp.role_key, sr.reference, sr.source_tier, sr.reliability_score),
                    )

            # Committee session + challenge protocol
            challenges = report.challenges or []
            conn.execute(
                "INSERT INTO committee_sessions "
                "(report_id,run_id,ticker,source,debate_on,n_specialists,n_challenges) "
                "VALUES (?,?,?,?,?,?,?)",
                (rid, getattr(report, "run_id", None), ticker, report.source,
                 1 if challenges else 0, len(report.specialists), len(challenges)),
            )
            for ch in challenges:
                cur = conn.execute(
                    "INSERT INTO committee_challenges "
                    "(report_id,run_id,challenger_key,challenger_title,target_key,target_title,challenge) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (rid, getattr(report, "run_id", None),
                     ch.get("challenger_key"), ch.get("challenger_title"),
                     ch.get("target_key"), ch.get("target_title"), ch.get("challenge")),
                )
                if ch.get("response"):
                    conn.execute(
                        "INSERT INTO committee_responses "
                        "(report_id,challenge_id,responder_key,response) VALUES (?,?,?,?)",
                        (rid, cur.lastrowid, ch.get("target_key"), ch.get("response")),
                    )
        conn.close()
        return rid
    except Exception as exc:
        log.warning("tirf.store.persist failed: %s", exc, exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Retrieval (the proposal §16 GET endpoints, as Python API)
# ---------------------------------------------------------------------------

def _rows(path: Path, sql: str, args: tuple) -> list[dict]:
    try:
        conn = _connect(path)
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("tirf.store query failed: %s", exc)
        return []


def get_report(report_id: str, db_path: Path | None = None) -> dict | None:
    """Fetch one research_reports row as a dict (or None). Never raises."""
    path = db_path if db_path is not None else DB_PATH
    rows = _rows(path, "SELECT * FROM research_reports WHERE report_id=?", (report_id,))
    return rows[0] if rows else None


def get_latest(ticker: str, db_path: Path | None = None) -> dict | None:
    """Fetch the newest report row for a ticker. Never raises."""
    path = db_path if db_path is not None else DB_PATH
    rows = _rows(
        path,
        "SELECT * FROM research_reports WHERE ticker=? ORDER BY version DESC LIMIT 1",
        (str(ticker).upper(),),
    )
    return rows[0] if rows else None


def get_evidence(report_id: str, db_path: Path | None = None) -> list[dict]:
    path = db_path if db_path is not None else DB_PATH
    return _rows(path, "SELECT * FROM evidence_items WHERE report_id=? ORDER BY id", (report_id,))


def get_assumptions(report_id: str, db_path: Path | None = None) -> list[dict]:
    path = db_path if db_path is not None else DB_PATH
    return _rows(path, "SELECT * FROM assumptions WHERE report_id=? ORDER BY id", (report_id,))


def get_reasoning(report_id: str, db_path: Path | None = None) -> list[dict]:
    path = db_path if db_path is not None else DB_PATH
    return _rows(path, "SELECT * FROM reasoning_chains WHERE report_id=? ORDER BY role_key,step_no",
                 (report_id,))


def get_counterarguments(report_id: str, db_path: Path | None = None) -> list[dict]:
    path = db_path if db_path is not None else DB_PATH
    return _rows(path, "SELECT * FROM counterarguments WHERE report_id=? ORDER BY id", (report_id,))


def get_sources(report_id: str, db_path: Path | None = None) -> list[dict]:
    path = db_path if db_path is not None else DB_PATH
    return _rows(path, "SELECT * FROM source_references WHERE report_id=? ORDER BY id", (report_id,))


def get_challenges(report_id: str, db_path: Path | None = None) -> list[dict]:
    path = db_path if db_path is not None else DB_PATH
    return _rows(path, "SELECT * FROM committee_challenges WHERE report_id=? ORDER BY id", (report_id,))


def list_reports(limit: int = 100, db_path: Path | None = None) -> list[dict]:
    """Newest reports first (for the dashboard / dev CLI). Never raises."""
    path = db_path if db_path is not None else DB_PATH
    return _rows(
        path,
        "SELECT report_id,ticker,version,as_of,source,final_recommendation,confidence,"
        "tirf_score,created_at FROM research_reports ORDER BY id DESC LIMIT ?",
        (limit,),
    )
