"""Dedicated, deterministic swing-scan workflow for Telegram.

This module is intentionally separate from the conversational Codex turn.  A
full Alpha Hunter scan may spend minutes in network-bound market-data calls, so
the Telegram command submits it as a tracked worker job and sends the result
when it is done.  There are no model calls in this path.

The financial snapshot remains owned by ``cio.alpha``.  The small job ledger
below only records lifecycle state, which lets ``/swing_status`` remain useful
while a scan is running and after a bot restart.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import alpha, db
from .alpha.engine import ScanCancelled

log = logging.getLogger("cio.swing")

_ACTIVE = {"queued", "running", "cancel_requested"}
_TERMINAL = {"completed", "failed", "cancelled"}
_LOCK = threading.RLock()
_JOBS: dict[str, "SwingJob"] = {}
_ACTIVE_BY_CHAT: dict[int, str] = {}
_MAX_MEMORY_JOBS = 100


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _db_path(path=None):
    return path or db.DB_PATH


@dataclass
class SwingJob:
    job_id: str
    chat_id: int
    language: str = "tc"
    refresh: bool = False
    status: str = "queued"
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    run_id: int | None = None
    error: str | None = None
    result: Any = field(default=None, repr=False, compare=False)
    meta: dict | None = field(default=None, repr=False, compare=False)
    cancel_event: threading.Event = field(default_factory=threading.Event,
                                           repr=False, compare=False)


def _persist(job: SwingJob, *, db_path=None) -> None:
    """Best-effort lifecycle write; job persistence must not break a scan."""
    try:
        conn = db.connect(_db_path(db_path))
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO swing_jobs "
                "(job_id, chat_id, language, refresh, status, run_id, error, "
                "created_at, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job.job_id, job.chat_id, job.language, int(job.refresh), job.status,
                 job.run_id, job.error, job.created_at, job.started_at,
                 job.finished_at),
            )
        conn.close()
    except Exception:
        log.warning("could not persist swing job %s", job.job_id, exc_info=True)


def _public(job: SwingJob) -> dict:
    return {
        "job_id": job.job_id,
        "chat_id": job.chat_id,
        "language": job.language,
        "refresh": job.refresh,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "run_id": job.run_id,
        "error": job.error,
    }


def create_job(chat_id: int, *, language: str = "tc", refresh: bool = False,
               db_path=None) -> SwingJob:
    """Create one durable job for *chat_id*.

    The bot's per-chat single-flight guard is the primary duplicate defense;
    this second guard also protects callers that use the workflow directly.
    """
    language = "en" if str(language).lower() == "en" else "tc"
    with _LOCK:
        active_id = _ACTIVE_BY_CHAT.get(int(chat_id))
        active = _JOBS.get(active_id) if active_id else None
        if active is not None and active.status in _ACTIVE:
            raise RuntimeError(f"swing job {active.job_id} is already running")
        job = SwingJob(
            job_id=f"swg-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}",
            chat_id=int(chat_id), language=language, refresh=bool(refresh),
        )
        _JOBS[job.job_id] = job
        _ACTIVE_BY_CHAT[job.chat_id] = job.job_id
        terminal = sorted(
            (j for j in _JOBS.values()
             if j.status in _TERMINAL and j.job_id != job.job_id),
            key=lambda j: j.created_at,
        )
        for stale in terminal[:-_MAX_MEMORY_JOBS]:
            _JOBS.pop(stale.job_id, None)
    _persist(job, db_path=db_path)
    return job


def get_job(job_id: str) -> SwingJob | None:
    with _LOCK:
        return _JOBS.get(str(job_id))


def _set_status(job: SwingJob, status: str, *, db_path=None, **fields) -> SwingJob:
    with _LOCK:
        job.status = status
        for key, value in fields.items():
            setattr(job, key, value)
        if status in _TERMINAL:
            job.finished_at = job.finished_at or _now()
            if _ACTIVE_BY_CHAT.get(job.chat_id) == job.job_id:
                _ACTIVE_BY_CHAT.pop(job.chat_id, None)
    _persist(job, db_path=db_path)
    return job


def cancel_job(job_id: str, *, db_path=None) -> SwingJob | None:
    """Signal cooperative cancellation and mark a queued/running job stopped."""
    job = get_job(job_id)
    if job is None:
        return None
    job.cancel_event.set()
    with _LOCK:
        active = job.status in _ACTIVE
    if active:
        # A to_thread worker cannot be killed by cancelling its asyncio waiter.
        # Keep the chat occupied until execute() observes the event and reaches
        # its terminal state, so a new scan cannot race the old one.
        with _LOCK:
            if job.status == "queued":
                terminal_status = "cancelled"
            else:
                terminal_status = "cancel_requested"
        _set_status(job, terminal_status, db_path=db_path,
                    error=("cancellation requested" if terminal_status == "cancel_requested"
                           else "cancelled by user"))
    return job


def execute(job_id: str, *, db_path=None) -> SwingJob | None:
    """Run one job synchronously, intended for ``asyncio.to_thread``.

    ``alpha`` checks the event between network-bound ticker steps and before
    committing the result.  A cancellation cannot interrupt a library call that
    is already inside yfinance, but it will stop before the next ticker and no
    report is sent for the cancelled job.
    """
    job = get_job(job_id)
    if job is None:
        return None
    with _LOCK:
        if job.status == "cancelled":
            return job
        if job.status not in _ACTIVE:
            return job
        if job.status == "cancel_requested" or job.cancel_event.is_set():
            return _set_status(job, "cancelled", db_path=db_path,
                               error="cancelled by user")
    _set_status(job, "running", db_path=db_path, started_at=_now())
    try:
        check = job.cancel_event.is_set
        run_kwargs = {"cancel_check": check, "publish": True}
        if db_path is not None:
            run_kwargs["db_path"] = db_path
        if job.refresh:
            result, meta = alpha.run_and_save(**run_kwargs)
        else:
            result, meta = alpha.reuse_today_or_run(**run_kwargs)
        if check():
            raise ScanCancelled("Swing scan cancelled")
        run_id = (meta or {}).get("run_id") if isinstance(meta, dict) else None
        with _LOCK:
            job.result = result
            job.meta = meta
        return _set_status(job, "completed", db_path=db_path, run_id=run_id)
    except (ScanCancelled, KeyboardInterrupt):
        return _set_status(job, "cancelled", db_path=db_path,
                           error="cancelled by user")
    except Exception as exc:  # one job must never take down the Telegram bot
        log.exception("swing job %s failed", job.job_id)
        return _set_status(job, "failed", db_path=db_path, error=str(exc))


def _latest_persisted(chat_id: int, *, db_path=None) -> dict | None:
    try:
        conn = db.connect(_db_path(db_path))
        row = conn.execute(
            "SELECT job_id, chat_id, language, refresh, status, run_id, error, "
            "created_at, started_at, finished_at FROM swing_jobs "
            "WHERE chat_id=? ORDER BY created_at DESC LIMIT 1",
            (int(chat_id),),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        out = dict(row)
        # A process restart cannot leave a live worker behind.  Make that
        # explicit instead of reporting a permanently running job.
        if out.get("status") in _ACTIVE:
            out["status"] = "interrupted"
        return out
    except Exception:
        log.debug("could not read swing job status for chat %s", chat_id,
                  exc_info=True)
        return None


def status_for_chat(chat_id: int, *, db_path=None) -> dict | None:
    """Return the active in-memory job, otherwise the latest durable job."""
    with _LOCK:
        active_id = _ACTIVE_BY_CHAT.get(int(chat_id))
        active = _JOBS.get(active_id) if active_id else None
        if active is not None and active.status in _ACTIVE:
            return _public(active)
        terminal = [j for j in _JOBS.values() if j.chat_id == int(chat_id)]
        if terminal:
            return _public(max(terminal, key=lambda j: j.created_at))
    return _latest_persisted(chat_id, db_path=db_path)


def status_text(chat_id: int, *, language: str = "tc", db_path=None) -> str:
    info = status_for_chat(chat_id, db_path=db_path)
    en = str(language).lower() == "en"
    if info is None:
        return "No swing scan is running. Use /swing to start one." if en else \
            "目前沒有波段掃描工作。請使用 /swing 開始。"
    status = info.get("status")
    job_id = info.get("job_id", "-")
    if status in _ACTIVE:
        if status == "cancel_requested":
            return (f"⏳ Swing scan {job_id} is stopping. Check again shortly." if en else
                    f"⏳ 波段掃描 {job_id} 正在停止，請稍後再查詢。")
        return (f"⏳ Swing scan is {status}. Job: {job_id}.\n"
                "Use /swing_status to check again or /stop to cancel." if en else
                f"⏳ 波段掃描目前{('排隊中' if status == 'queued' else '執行中')}。"
                f"工作編號：{job_id}\n可用 /swing_status 查詢，或用 /stop 取消。")
    if status == "completed":
        return (f"✅ Swing scan {job_id} completed. Use /swing to view today's "
                "cached result again." if en else
                f"✅ 波段掃描 {job_id} 已完成。再次使用 /swing 可直接查看今天的快取結果。")
    if status == "cancelled":
        return (f"🛑 Swing scan {job_id} was cancelled." if en else
                f"🛑 波段掃描 {job_id} 已取消。")
    if status == "interrupted":
        return (f"⚠️ Swing scan {job_id} was interrupted by a restart. Use /swing "
                "to resume from today's completed snapshot or start again." if en else
                f"⚠️ 波段掃描 {job_id} 因程序重啟而中斷。請使用 /swing 重新執行；"
                "若今天已有完成快照，系統會直接重用。")
    detail = info.get("error") or "unknown error"
    return (f"⚠️ Swing scan {job_id} failed: {detail}" if en else
            f"⚠️ 波段掃描 {job_id} 失敗：{detail}")


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _selected(result, meta: dict | None) -> list[dict]:
    candidates = list(getattr(result, "candidates", []) or [])
    threshold = (meta or {}).get("threshold")
    if threshold is None:
        return candidates  # cached rows are already threshold-selected
    try:
        return list(result.select(float(threshold)))
    except Exception:
        return candidates


def format_report(result, meta: dict | None = None, *, language: str = "tc",
                  max_show: int = 12) -> str:
    """Render only values from the deterministic Alpha snapshot."""
    reg = getattr(result, "regime", {}) or {}
    status = str(reg.get("status", "UNKNOWN")).upper()
    run_date = getattr(result, "run_date", "unknown")
    selected = _selected(result, meta)
    threshold = (meta or {}).get("threshold")
    is_en = str(language).lower() == "en"
    threshold_text = (f"Final ≥ {_fmt(threshold)}" if threshold is not None
                      else ("already threshold-selected" if is_en else "已通過篩選"))

    if is_en:
        lines = [
            f"📈 Swing candidates for today — {run_date}",
            f"Market regime: {status} ({reg.get('detail', '')})",
            f"Universe scanned: {getattr(result, 'universe_size', 0)} | {threshold_text}",
        ]
        action = {
            "GREEN": "🟢 Selective new swing entries are allowed by the market filter.",
            "YELLOW": "🟡 Mixed market: treat these as watch candidates and reduce size.",
            "RED": "🔴 Defensive market: no new swing entry is recommended by this scan.",
        }.get(status, "⚪ Market state is unknown: do not treat the list as an entry signal.")
        lines += [action, ""]
        if not selected:
            lines.append("No candidate cleared the configured selection threshold today.")
        else:
            lines.append(f"{len(selected)} candidate(s):")
            for c in selected[:max_show]:
                lines.append(
                    f"  {c.get('rank', 0):>2}. {c.get('ticker', '?'):<7} "
                    f"score {_fmt(c.get('final'))} | momentum {_fmt(c.get('momentum'))} "
                    f"| trend {_fmt(c.get('trend'))} | earnings {_fmt(c.get('earnings'))}"
                )
            if len(selected) > max_show:
                lines.append(f"  …and {len(selected) - max_show} more.")
        if meta and meta.get("watchlist_name"):
            lines += ["", f"📋 Published watchlist: {meta['watchlist_name']} (active)."]
        if meta and meta.get("reused"):
            lines += ["♻️ Reused today's completed scan."]
        lines += ["", "Research only — confirm live price, volume, catalysts, and stop level before acting."]
        return "\n".join(lines)

    lines = [
        f"📈 今日波段候選 — {run_date}",
        f"市場環境：{status}（{reg.get('detail', '')}）",
        f"掃描範圍：{getattr(result, 'universe_size', 0)} 檔｜{threshold_text}",
    ]
    action = {
        "GREEN": "🟢 市場趨勢允許「選擇性」新進場。",
        "YELLOW": "🟡 市場環境混合：以觀察為主、降低部位，避免追價。",
        "RED": "🔴 市場偏弱：本掃描不建議今天新開波段部位。",
    }.get(status, "⚪ 市場狀態未知：不要把候選名單視為進場訊號。")
    lines += [action, ""]
    if not selected:
        lines.append("今天沒有股票通過設定的選股門檻。")
    else:
        lines.append(f"符合條件的候選（{len(selected)} 檔）：")
        for c in selected[:max_show]:
            lines.append(
                f"  {c.get('rank', 0):>2}. {c.get('ticker', '?'):<7} "
                f"分數 {_fmt(c.get('final'))}｜動能 {_fmt(c.get('momentum'))} "
                f"｜趨勢 {_fmt(c.get('trend'))}｜獲利 {_fmt(c.get('earnings'))}"
            )
        if len(selected) > max_show:
            lines.append(f"  …另有 {len(selected) - max_show} 檔。")
    if meta and meta.get("watchlist_name"):
        lines += ["", f"📋 已發布並啟用觀察名單：{meta['watchlist_name']}。"]
    if meta and meta.get("reused"):
        lines += ["♻️ 已重用今天完成的掃描結果。"]
    lines += ["", "這是研究篩選，不是自動下單指令；進場前請確認即時價格、成交量、催化劑與停損位置。"]
    return "\n".join(lines)


__all__ = [
    "SwingJob", "ScanCancelled", "create_job", "get_job", "cancel_job",
    "execute", "status_for_chat", "status_text", "format_report",
]
