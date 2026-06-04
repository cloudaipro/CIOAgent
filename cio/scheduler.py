"""Autonomous scheduled work for the 24/7 runtime.

The bot is event-driven (it reacts to messages), but a 24/7 CIO should also act
on its own. APScheduler runs inside the bot's asyncio loop and pushes a daily
portfolio digest to every subscribed chat.

The digest is computed directly from the portfolio layer — deterministic, no
model call, zero tokens. That keeps a once-a-day broadcast cheap and reliable;
the conversational agent is reserved for interactive turns.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import memory, portfolio, timeutil

log = logging.getLogger("cio.scheduler")

_LAST_DIGEST_KEY = "last_digest_date"  # ISO date of the last digest actually sent
_LAST_WMA_KEY = "last_wma_date"        # ISO date of the last watchlist briefing sent

_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _is_briefing_day(days_spec: str, when: date | None = None) -> bool:
    """Whether *when* (default today) matches a cron-style day_of_week spec
    (e.g. 'mon-fri', 'mon,wed,fri', '*'). Used only for the boot catch-up; the
    live job relies on APScheduler's own day_of_week filter."""
    when = when or date.today()
    wd = when.weekday()
    spec = (days_spec or "").strip().lower()
    if not spec or spec == "*":
        return True
    allowed: set[int] = set()
    for part in (p.strip() for p in spec.split(",")):
        if "-" in part:
            a, _, b = part.partition("-")
            if a in _DOW and b in _DOW:
                i, j = _DOW[a], _DOW[b]
                allowed.update(range(i, j + 1) if i <= j
                               else list(range(i, 7)) + list(range(0, j + 1)))
        elif part.isdigit():
            allowed.add(int(part) % 7)
        elif part in _DOW:
            allowed.add(_DOW[part])
    return wd in allowed if allowed else True


def _format_digest() -> str:
    s = portfolio.summary()
    if not s["positions"]:
        return "📊 Daily digest: no open positions yet. Upload a transactions CSV to start."
    pos = portfolio.positions()
    priced = pos.dropna(subset=["unrealized_pl"])
    lines = [
        "📊 *Daily portfolio digest*",
        f"Market value: {s['market_value']:,.2f}",
        f"Cost basis:   {s['cost_basis']:,.2f}",
        f"Unrealized:   {s['unrealized_pl']:+,.2f} ({s['unrealized_pct']:+.2f}%)",
        f"Realized:     {s['realized_pl']:+,.2f}   Dividends: {s['dividends']:,.2f}",
    ]
    if len(priced):
        top = priced.sort_values("unrealized_pl", ascending=False).iloc[0]
        bot = priced.sort_values("unrealized_pl", ascending=True).iloc[0]
        lines.append(f"Top: {top['symbol']} {top['unrealized_pl']:+,.2f} | "
                     f"Worst: {bot['symbol']} {bot['unrealized_pl']:+,.2f}")
    unpriced = pos[pos["last_price"].isna()]["symbol"].tolist()
    if unpriced:
        lines.append(f"⚠️ No price set for: {', '.join(unpriced)} — send e.g. \"set AAPL 230\".")
    return "\n".join(lines)


async def price_refresh() -> None:
    """Fetch live prices for all open positions and write them to the DB.

    Never raises into the scheduler — all errors are caught and logged.
    """
    try:
        result = portfolio.refresh_live_prices()
        updated = len(result.get("updated", []))
        failed = len(result.get("failed", []))
        log.info("price_refresh complete: %d updated, %d failed", updated, failed)
    except Exception:
        log.exception("price_refresh job failed")


async def daily_digest(bot) -> None:
    """Push the digest to all subscribed chats. Never raises into the scheduler.

    Records today's date so a same-day reboot won't re-send, and so the
    boot-time catch-up can tell whether today's digest already went out.
    """
    today = date.today().isoformat()
    if memory.get_meta(_LAST_DIGEST_KEY) == today:
        return  # already sent today (idempotent across restarts)
    chats = memory.subscribed_chats()
    if not chats:
        return
    text = _format_digest()
    sent_any = False
    for chat_id in chats:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            sent_any = True
        except Exception:  # one bad chat must not block the rest
            log.exception("digest send failed for chat %s", chat_id)
    if sent_any:
        memory.set_meta(_LAST_DIGEST_KEY, today)


async def _send_briefing(bot, chat_ids, briefing_md: str, summary_text: str,
                         date_str: str) -> bool:
    """Render the briefing PDF (text fallback) and push it to *chat_ids*.
    Returns True if at least one chat received it. Never raises."""
    from pathlib import Path
    reports_dir = Path(__file__).resolve().parent.parent / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    doc_path = None
    try:
        from .committee.render_pdf import markdown_to_pdf
        pdf_path = reports_dir / f"watchlist_briefing_{date_str}.pdf"
        markdown_to_pdf(briefing_md, pdf_path, title=f"Watchlist Briefing {date_str}")
        doc_path = pdf_path
    except Exception:
        log.exception("WMA briefing PDF render failed; will send the .md instead")
        try:
            md_path = reports_dir / f"watchlist_briefing_{date_str}.md"
            md_path.write_text(briefing_md, encoding="utf-8")
            doc_path = md_path
        except Exception:
            log.exception("WMA briefing .md write failed; sending summary text only")

    sent_any = False
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=summary_text)
            if doc_path is not None:
                with open(doc_path, "rb") as fh:
                    await bot.send_document(chat_id=chat_id, document=fh,
                                            filename=doc_path.name)
            sent_any = True
        except Exception:  # one bad chat must not block the rest
            log.exception("WMA briefing send failed for chat %s", chat_id)
    return sent_any


async def watchlist_briefing(bot) -> None:
    """Run the Watchlist Monitoring Agent over the active watchlist and push the
    pre-market briefing to every subscribed chat. Idempotent per day (a same-day
    reboot won't re-send). Never raises into the scheduler."""
    today = date.today().isoformat()
    if memory.get_meta(_LAST_WMA_KEY) == today:
        return  # already sent today
    if not timeutil.is_trading_day():
        log.info("watchlist_briefing: %s is not a Nasdaq trading day; skipping", today)
        return
    chats = memory.subscribed_chats()
    if not chats:
        return
    try:
        from .watchlist_monitor import (
            monitor_watchlist, global_macro_snapshot, build_briefing,
            briefing_summary, as_of_now,
        )
        assessments = await monitor_watchlist()
    except Exception:
        log.exception("watchlist_briefing: monitoring run failed")
        return
    if not assessments:
        log.info("watchlist_briefing: no active watchlist / nothing to review")
        return
    try:
        macro = await global_macro_snapshot()
    except Exception:
        log.exception("watchlist_briefing: macro snapshot failed; continuing without")
        macro = None
    briefing = build_briefing(assessments, as_of=as_of_now(), macro=macro)
    summary = briefing_summary(assessments, macro)
    if await _send_briefing(bot, chats, briefing, summary, today):
        memory.set_meta(_LAST_WMA_KEY, today)


def start(bot) -> AsyncIOScheduler:
    """Start the scheduler on the running loop. Returns it so the caller can stop it.

    CIO_DIGEST_HOUR / CIO_DIGEST_MINUTE (local TZ, default 08:00) control timing.
    Set CIO_DIGEST_HOUR=off to disable. CFO_DIGEST_HOUR/MINUTE still honored.

    CIO_PRICE_REFRESH_HOUR / CIO_PRICE_REFRESH_MINUTE (local TZ, default 17:00)
    control the daily price refresh. Set CIO_PRICE_REFRESH_HOUR=off to disable.
    CFO_PRICE_REFRESH_HOUR/MINUTE still honored.
    """
    hour = os.getenv("CIO_DIGEST_HOUR", os.getenv("CFO_DIGEST_HOUR", "8"))
    if hour.lower() == "off":
        log.info("daily digest disabled (CIO_DIGEST_HOUR=off)")
        return None
    hour, minute = int(hour), int(os.getenv("CIO_DIGEST_MINUTE", os.getenv("CFO_DIGEST_MINUTE", "0")))
    sched = AsyncIOScheduler()
    # coalesce + grace: if the loop was briefly blocked past fire time, still run
    # once rather than skip. (Catch-up for full downtime is handled below.)
    sched.add_job(daily_digest, "cron", hour=hour, minute=minute, args=[bot],
                  id="daily_digest", replace_existing=True,
                  coalesce=True, misfire_grace_time=3600)
    sched.start()
    log.info("scheduler started: daily digest at %02d:%02d local", hour, minute)

    # Boot-time catch-up: if today's slot already passed (e.g. the machine was
    # rebooting at digest time) and we haven't sent today, fire one shortly.
    now = datetime.now()
    slot_passed = (now.hour, now.minute) >= (hour, minute)
    if slot_passed and memory.get_meta(_LAST_DIGEST_KEY) != date.today().isoformat():
        log.info("missed today's digest during downtime; scheduling catch-up")
        sched.add_job(daily_digest, "date",
                      run_date=now.replace(microsecond=0) + timedelta(seconds=15),
                      args=[bot], id="digest_catchup", replace_existing=True)

    # ----- price refresh job --------------------------------------------------
    pr_hour = os.getenv("CIO_PRICE_REFRESH_HOUR", os.getenv("CFO_PRICE_REFRESH_HOUR", "17"))
    if pr_hour.lower() != "off":
        pr_hour = int(pr_hour)
        pr_minute = int(os.getenv("CIO_PRICE_REFRESH_MINUTE", os.getenv("CFO_PRICE_REFRESH_MINUTE", "0")))
        sched.add_job(price_refresh, "cron", hour=pr_hour, minute=pr_minute,
                      id="price_refresh", replace_existing=True,
                      coalesce=True, misfire_grace_time=3600)
        log.info("price refresh scheduled at %02d:%02d local", pr_hour, pr_minute)
        # Boot-time one-shot: always fire ~15s after start so data is fresh on restart.
        sched.add_job(price_refresh, "date",
                      run_date=now.replace(microsecond=0) + timedelta(seconds=15),
                      id="price_refresh_boot", replace_existing=True)
        log.info("price refresh boot one-shot queued")
    else:
        log.info("price refresh disabled (CIO_PRICE_REFRESH_HOUR=off)")

    # ----- watchlist monitoring briefing (WMA) --------------------------------
    # Default 06:00 local on stock days (Mon-Fri). CIO_WMA_HOUR=off disables.
    # CIO_WMA_MINUTE / CIO_WMA_DAYS (cron day_of_week, e.g. "mon-fri") tune it.
    wma_hour = os.getenv("CIO_WMA_HOUR", "6")
    if wma_hour.lower() != "off":
        wma_hour = int(wma_hour)
        wma_minute = int(os.getenv("CIO_WMA_MINUTE", "0"))
        wma_days = os.getenv("CIO_WMA_DAYS", "mon-fri")
        sched.add_job(watchlist_briefing, "cron", day_of_week=wma_days,
                      hour=wma_hour, minute=wma_minute, args=[bot],
                      id="watchlist_briefing", replace_existing=True,
                      coalesce=True, misfire_grace_time=3600)
        log.info("watchlist briefing scheduled %s at %02d:%02d local",
                 wma_days, wma_hour, wma_minute)
        # Boot-time catch-up: if today is a Nasdaq trading day (and matches the
        # configured days), its slot already passed, and we haven't sent today,
        # fire one shortly after boot.
        if (_is_briefing_day(wma_days)
                and timeutil.is_trading_day()
                and (now.hour, now.minute) >= (wma_hour, wma_minute)
                and memory.get_meta(_LAST_WMA_KEY) != date.today().isoformat()):
            log.info("missed today's watchlist briefing during downtime; scheduling catch-up")
            sched.add_job(watchlist_briefing, "date",
                          run_date=now.replace(microsecond=0) + timedelta(seconds=30),
                          args=[bot], id="wma_catchup", replace_existing=True)
    else:
        log.info("watchlist briefing disabled (CIO_WMA_HOUR=off)")

    return sched
