"""Offline contracts for the swing-focused investment committee."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field


def _run(coro):
    return asyncio.run(coro)


def test_swing_roles_receive_mandate_without_mutating_default_roles():
    from cio.committee.roles import SPECIALISTS, SWING_MANDATE, specialists_for_profile

    default_prompt = SPECIALISTS[0]["system_prompt"]
    swing_roles = specialists_for_profile("swing")

    assert SWING_MANDATE in swing_roles[0]["system_prompt"]
    assert "BUY means enter/add the swing now" in swing_roles[0]["system_prompt"]
    assert SPECIALISTS[0]["system_prompt"] == default_prompt
    assert swing_roles[0] is not SPECIALISTS[0]


def test_swing_engine_routes_profile_mandate_and_cio_contract(monkeypatch):
    from cio.committee import engine, structured
    from cio.committee.roles import SWING_MANDATE

    seen = {"specialist_systems": [], "structured": []}
    bundle = {
        "symbol": "AAPL", "resolved": "AAPL", "as_of": "2026-08-20T16:00:00",
        "quote": {"close": 200.0}, "fundamentals": {}, "is_etf": False,
        "ta_profile": "swing", "ta_composite": "neutral",
        "ta_signals": {"squeeze": "bull", "kdj": "neutral"},
    }

    def fake_bundle(symbol, profile):
        seen["bundle_call"] = (symbol, profile)
        return bundle

    async def fake_specialist(role, bundle_text, symbol):
        seen["specialist_systems"].append(role["system_prompt"])
        return {
            "key": role["key"], "title": role["title"], "vote": "HOLD",
            "confidence": 60, "reason": "Wait for confirmation",
        }

    async def fake_structured(system_prompt, user_prompt, *, role_key,
                              response_schema, schema_name):
        seen["structured"].append({
            "system": system_prompt, "prompt": user_prompt, "role": role_key,
            "schema": response_schema, "name": schema_name,
        })
        if role_key == "moderator":
            return json.dumps({
                "committee_recommendation": "HOLD", "agreement_score": 100,
                "majority_view": "Wait", "minority_view": "None",
                "key_disagreements": "None",
            })
        return json.dumps({
            "final_recommendation": "Hold", "confidence_score": 70,
            "risk_rating": "Moderate", "time_horizon": "days to several weeks",
            "macro_alignment_score": 50, "geopolitical_risk_score": 40,
            "external_risk_adjustment": "No adjustment", "base_case": "Wait",
            "bull_case": "Breakout confirms", "bear_case": "Setup fails",
            "scenarios": [
                {"scenario": "Base", "probability": 50, "price_target": "Not quantifiable", "key_drivers": "Trigger"},
                {"scenario": "Bull", "probability": 30, "price_target": "Not quantifiable", "key_drivers": "Breakout"},
                {"scenario": "Bear", "probability": 20, "price_target": "Not quantifiable", "key_drivers": "Failure"},
            ],
            "memory_note": "Wait for confirmation before entering this setup",
            "swing_action": "WAIT", "setup_status": "Unconfirmed",
            "entry_trigger": "Confirmed breakout", "invalidation": "Breakdown",
            "profit_taking": "Trail strength", "position_sizing": "Risk based",
            "event_risk": "Avoid unpriced event risk",
        })

    monkeypatch.setattr(engine, "gather_bundle", fake_bundle)
    monkeypatch.setattr(engine, "run_specialist", fake_specialist)
    monkeypatch.setattr(engine, "ask_structured_role", fake_structured)
    monkeypatch.setattr(engine.agent_memory, "recall_block", lambda *a: "")
    monkeypatch.setattr(engine.agent_memory, "reflect", lambda *a: None)
    monkeypatch.setattr(engine, "_save_clean_note", lambda *a, **k: asyncio.sleep(0))
    import cio.committee.tirf as tirf
    monkeypatch.setattr(tirf, "build_research_report", lambda **k: None)
    monkeypatch.setattr(tirf, "persist", lambda *a: None)

    result = _run(engine.run_committee("AAPL", profile="swing", debate=False))

    assert result.error is None
    assert seen["bundle_call"] == ("AAPL", "swing")
    assert seen["specialist_systems"] and all(
        SWING_MANDATE in prompt for prompt in seen["specialist_systems"])
    moderator, cio = seen["structured"]
    assert SWING_MANDATE in moderator["system"]
    assert cio["schema"] is structured.SWING_CIO_SCHEMA
    assert cio["name"] == "committee_swing_cio"
    assert "Strategy profile: swing" in cio["prompt"]
    assert result.cio["swing_action"] == "WAIT"


def test_swing_report_has_distinct_title_and_trade_plan():
    from cio.committee.report import build_report

    result = {
        "resolved": "AAPL", "as_of": "2026-08-20", "bundle": {
            "ta_profile": "swing", "quote": {"close": 200}, "fundamentals": {},
        },
        "cio": {
            "final_recommendation": "Hold", "confidence_score": 70,
            "base_case": "Wait", "swing_action": "WAIT",
            "setup_status": "Coiling", "entry_trigger": "Confirmed breakout",
            "invalidation": "Close below support", "profit_taking": "Trail VIDYA",
            "position_sizing": "Size from stop distance", "event_risk": "No hold through earnings",
        },
    }
    report = build_report("AAPL", result)

    assert report.startswith("# Swing Strategy Committee Report: AAPL")
    assert "## Swing Trade Plan" in report
    assert "**Entry Trigger:** Confirmed breakout" in report
    assert "BUY=enter, HOLD=wait, SELL=avoid/exit" in report


def test_swing_bundle_adds_confirmed_execution_levels(monkeypatch):
    import pandas as pd
    from cio.committee import bundle as bundle_mod

    idx = pd.date_range("2026-06-01", periods=60, freq="D")
    close = pd.Series([100.0 + i for i in range(60)], index=idx)
    history = pd.DataFrame({
        "High": close + 2, "Low": close - 2, "Close": close,
        "Volume": [1_000_000] * 59 + [2_000_000],
    }, index=idx)

    class Stock:
        normalize_symbol = staticmethod(lambda x: x)
        get_quote = staticmethod(lambda x: {"close": 159.0})
        fundamentals = staticmethod(lambda x: {})
        get_history = staticmethod(lambda *a: history)
        run_strategy_profile = staticmethod(lambda *a: {
            "signals": {"kdj": "bull"}, "composite": "bull",
            "detail": {"kdj": {"confidence": .7, "state": "bull",
                                  "direction": "rising", "events": []}},
            "composite_score": .7, "stability": "stable",
            "asof": "2026-07-29",  # excludes the final 2026-07-30 bar
        })

    monkeypatch.setattr(bundle_mod, "_stock", Stock)
    monkeypatch.setattr(bundle_mod, "_external", lambda *a: ([], None, None, None))
    monkeypatch.setattr(bundle_mod, "_convergence", lambda *a: None)
    monkeypatch.setattr(bundle_mod, "_swing_regime", lambda *a: {
        "status": "GREEN", "detail": "uptrend", "qqq": 500,
        "ma50": 490, "ma200": 450,
    })

    out = bundle_mod.gather_bundle("AAPL", profile="swing")
    text = bundle_mod.format_bundle(out)

    assert out["swing_context"]["bar_date"] == "2026-07-29"
    assert out["swing_context"]["close"] == 158.0
    assert out["swing_context"]["atr14"] == 4.0
    assert "SWING_CONTEXT:" in text
    assert "MARKET_REGIME: GREEN" in text


def test_swing_delivery_uses_profile_filename_chart_and_summary(tmp_path, monkeypatch):
    from cio.committee import delivery

    class Result:
        error = None
        as_of = "2026-08-20T16:10:00"
        tirf = None
        bundle = {"ta_profile": "swing", "ta_composite": "bull"}
        cio = {"final_recommendation": "Buy", "confidence_score": 72,
               "swing_action": "ENTER"}
        vote_tally = {"buy_count": 5, "hold_count": 2, "sell_count": 1}
        consensus = {"agreement_score": 76}

    seen = {}

    async def fake_run(symbol, profile):
        seen["run"] = (symbol, profile)
        return Result()

    monkeypatch.setattr("cio.committee.run_committee", fake_run)
    monkeypatch.setattr("cio.committee.build_report", lambda *a: "# swing")
    monkeypatch.setattr("cio.committee.translate.translate_report",
                        lambda md, lang: asyncio.sleep(0, result=md))
    monkeypatch.setattr("cio.stock.render_indicators",
                        lambda symbol, profile: seen.setdefault("chart", (symbol, profile)) or "chart.png")

    def fake_pdf(md, out_path, title="", **kwargs):
        seen["title"] = title
        out_path.write_bytes(b"%PDF fake")

    monkeypatch.setattr("cio.committee.render_pdf.markdown_to_pdf", fake_pdf)

    art = _run(delivery.produce_report("aapl", "en", tmp_path, profile="swing"))

    assert seen["run"] == ("AAPL", "swing")
    assert seen["chart"] == ("AAPL", "swing")
    assert "Swing Strategy Committee Report" in seen["title"]
    assert art.profile == "swing"
    assert "committee_swing" in art.doc_path.name
    assert "Swing Committee Summary" in art.summary
    assert "*Action:* ENTER" in art.summary


@dataclass
class _Chat:
    id: int = 7

    async def send_action(self, action):
        pass


@dataclass
class _Message:
    chat: _Chat = field(default_factory=_Chat)
    replies: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)

    async def reply_document(self, fh, filename="", **kwargs):
        self.documents.append(filename)


@dataclass
class _Update:
    message: _Message = field(default_factory=_Message)

    @property
    def effective_chat(self):
        return self.message.chat


@dataclass
class _Ctx:
    args: list[str] = field(default_factory=list)


def test_bot_committee_swing_routes_explicit_profile(tmp_path, monkeypatch):
    from cio import bot
    from cio.committee.delivery import CommitteeArtifact

    report = tmp_path / "AAPL_committee_swing.pdf"
    report.write_bytes(b"%PDF fake")
    seen = {}

    async def fake_produce(symbol, lang, reports_dir, source="command", profile="committee"):
        seen.update(symbol=symbol, lang=lang, profile=profile)
        return CommitteeArtifact(
            symbol=symbol, profile=profile, doc_path=report,
            summary="AAPL Swing Committee Summary",
        )

    monkeypatch.setattr("cio.committee.delivery.produce_report", fake_produce)
    monkeypatch.setattr(bot.richmsg, "reply_rich", lambda *a, **k: asyncio.sleep(0, result=False))
    update = _Update()

    _run(bot.cmd_committee_swing(update, _Ctx(args=["aapl", "zh"])))

    assert seen == {"symbol": "AAPL", "lang": "tc", "profile": "swing"}
    assert update.message.documents == [report.name]
    assert any("fresh swing entry" in reply for reply in update.message.replies)


def test_bot_committee_swing_no_arg_shows_own_usage():
    from cio.bot import cmd_committee_swing

    update = _Update()
    _run(cmd_committee_swing(update, _Ctx()))

    assert len(update.message.replies) == 1
    assert "/committee_swing SYMBOL" in update.message.replies[0]
