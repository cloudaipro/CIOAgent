"""Autonomous scheduled work for the 24/7 runtime.

The bot is event-driven (it reacts to messages), but a 24/7 CFO should also act
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

from . import memory, portfolio

log = logging.getLogger("cfo.scheduler")

_LAST_DIGEST_KEY = "last_digest_date"  # ISO date of the last digest actually sent


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


def start(bot) -> AsyncIOScheduler:
    """Start the scheduler on the running loop. Returns it so the caller can stop it.

    CFO_DIGEST_HOUR / CFO_DIGEST_MINUTE (local TZ, default 08:00) control timing.
    Set CFO_DIGEST_HOUR=off to disable.

    CFO_PRICE_REFRESH_HOUR / CFO_PRICE_REFRESH_MINUTE (local TZ, default 17:00)
    control the daily price refresh. Set CFO_PRICE_REFRESH_HOUR=off to disable.
    """
    hour = os.getenv("CFO_DIGEST_HOUR", "8")
    if hour.lower() == "off":
        log.info("daily digest disabled (CFO_DIGEST_HOUR=off)")
        return None
    hour, minute = int(hour), int(os.getenv("CFO_DIGEST_MINUTE", "0"))
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
    pr_hour = os.getenv("CFO_PRICE_REFRESH_HOUR", "17")
    if pr_hour.lower() != "off":
        pr_hour = int(pr_hour)
        pr_minute = int(os.getenv("CFO_PRICE_REFRESH_MINUTE", "0"))
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
        log.info("price refresh disabled (CFO_PRICE_REFRESH_HOUR=off)")

    return sched
