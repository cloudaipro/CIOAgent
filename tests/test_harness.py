"""Comprehensive tests for cio/harness/ (V1 consistency, V2 citation,
V3 event_study, and the self-authoring registry gate).

Deterministic: no network, no live cfo.db. HTTP and DB are injected.
See docs/HARNESS-TESTING-PLAN.md.
"""
import sqlite3

import pytest

from cio.harness import (
    TradePlan, check_trade_plan,
    Citation, verify_citations,
    EventType, study, wave2_estimate,
    SkillRegistry, VerifyCase, GateError, HarnessSkill, SkillStatus,
    Severity,
)
from cio.harness import event_study, tools
from cio.harness import store as hstore, admin as hadmin
from cio.data.source_policy import Tier


# fake resolver: a dict url->status, None when absent (offline, deterministic)
def make_resolver(table):
    return lambda u: table.get(u)


# ===================================================================== V1 ===
class TestV1Consistency:
    def test_mchp_replay_blocks(self):
        """conv_turns 326-329: limit $97.50 while $99 intraday, market up."""
        plan = TradePlan(symbol="MCHP", entry_kind="limit", entry_price=97.5,
                         current_price=99.0, market_bias="up")
        res = check_trade_plan(plan)
        assert res.blocked is True
        assert "R1_REL_WEAKNESS" in res.codes()
        f = next(f for f in res.findings if f.code == "R1_REL_WEAKNESS")
        assert f.severity == Severity.BLOCK

    def test_pullback_in_down_market_no_false_positive(self):
        """A pullback that fills while the market is also down keeps relative
        strength — R1 must NOT fire (over-blocking is a failure too)."""
        plan = TradePlan(entry_kind="pullback", entry_price=95, current_price=99,
                         market_bias="down")
        res = check_trade_plan(plan)
        assert "R1_REL_WEAKNESS" not in res.codes()

    def test_small_dip_flat_market_is_warn_not_block(self):
        plan = TradePlan(entry_kind="limit", entry_price=98.5, current_price=99.0,
                         market_bias="flat")  # 0.5% drop, flat market < 2% threshold
        res = check_trade_plan(plan)
        codes = res.codes()
        assert "R1_REL_WEAKNESS" in codes
        f = next(f for f in res.findings if f.code == "R1_REL_WEAKNESS")
        assert f.severity == Severity.WARN
        assert res.blocked is False

    def test_r1_requires_catalyst_check_at_any_severity(self):
        """Aligns V1 with the binary stored rules (Rule 6 / Rule 2c / playbook step 7):
        a WARN is 'catalyst check required', not 'safe to enter'."""
        blk = check_trade_plan(TradePlan(entry_kind="limit", entry_price=97.5,
                                         current_price=99.0, market_bias="up"))
        fb = next(f for f in blk.findings if f.code == "R1_REL_WEAKNESS")
        assert fb.severity == Severity.BLOCK
        assert fb.detail["catalyst_check_required"] is True
        wrn = check_trade_plan(TradePlan(entry_kind="limit", entry_price=98.5,
                                         current_price=99.0, market_bias="flat"))
        fw = next(f for f in wrn.findings if f.code == "R1_REL_WEAKNESS")
        assert fw.severity == Severity.WARN
        assert fw.detail["catalyst_check_required"] is True

    def test_explicit_market_move_overrides_default(self):
        plan = TradePlan(entry_kind="limit", entry_price=98.5, current_price=99.0,
                         market_bias="up", market_move_pct=3.0)  # 0.5 + 3.0 = 3.5 >= 2
        res = check_trade_plan(plan)
        f = next(f for f in res.findings if f.code == "R1_REL_WEAKNESS")
        assert f.severity == Severity.BLOCK

    def test_incoherent_plan_blocks(self):
        plan = TradePlan(entry_kind="breakout", entry_price=100, stop_price=105,
                         target_price=90)
        res = check_trade_plan(plan)
        assert res.blocked is True
        assert "R2_PLAN_COHERENCE" in res.codes()

    def test_rr_floor_warns(self):
        plan = TradePlan(entry_kind="breakout", entry_price=97.5, stop_price=87.5,
                         target_price=105)  # rr = 7.5/10 = 0.75
        res = check_trade_plan(plan)
        assert "R3_RR_FLOOR" in res.codes()
        f = next(f for f in res.findings if f.code == "R3_RR_FLOOR")
        assert f.detail["rr"] == 0.75
        assert f.severity == Severity.WARN

    def test_earnings_window_squeeze_warns(self):
        plan = TradePlan(entry_kind="breakout", entry_date="2026-06-18",
                         earnings_date="2026-08-05", min_hold_days=60)
        res = check_trade_plan(plan)
        assert "R4_EARNINGS_WINDOW" in res.codes()

    def test_earnings_window_ok_when_room(self):
        plan = TradePlan(entry_kind="breakout", entry_date="2026-06-18",
                         earnings_date="2026-08-05", min_hold_days=10)
        res = check_trade_plan(plan)
        assert "R4_EARNINGS_WINDOW" not in res.codes()

    def test_chase_warns(self):
        plan = TradePlan(entry_kind="market", pct_today=4.7, at_upper_band=True)
        res = check_trade_plan(plan)
        assert "R5_CHASE" in res.codes()

    def test_clean_plan_ok(self):
        plan = TradePlan(entry_kind="breakout", entry_price=100, current_price=99,
                         stop_price=95, target_price=110, market_bias="up")
        res = check_trade_plan(plan)
        assert res.ok is True
        assert res.findings == []

    def test_empty_plan_never_raises(self):
        assert check_trade_plan(TradePlan()).ok is True


# ===================================================================== V2 ===
class TestV2Citation:
    def test_dead_url_blocks(self):
        r = verify_citations(
            [Citation(url="https://www.cnbc.com/2026/06/16/intel-bad.html",
                      backs_material=True)],
            resolver=make_resolver({}))  # absent -> None -> dead
        assert r.blocked is True
        assert "C_DEAD_URL" in [f.code for f in r.findings]

    def test_intc_caught(self):
        """347 state: dead CNBC + one live Tier-2 -> blocked + material unverified."""
        table = {"https://www.reuters.com/intel-apple": 200}
        cits = [
            Citation(url="https://www.cnbc.com/intel-18a.html", backs_material=True),
            Citation(url="https://www.reuters.com/intel-apple", backs_material=True),
        ]
        r = verify_citations(cits, resolver=make_resolver(table))
        assert r.blocked is True
        assert r.material_verified is False
        assert "https://www.cnbc.com/intel-18a.html" in r.dead_urls

    def test_intc_fixed(self):
        """349 state: two live independent Tier-2 -> ok + material verified."""
        table = {"https://www.reuters.com/intel-apple": 200,
                 "https://finance.yahoo.com/intel": 200}
        cits = [
            Citation(url="https://www.reuters.com/intel-apple", backs_material=True),
            Citation(url="https://finance.yahoo.com/intel", backs_material=True),
        ]
        r = verify_citations(cits, resolver=make_resolver(table))
        assert r.ok is True
        assert r.material_verified is True

    def test_live_tier3_cannot_back_material(self):
        table = {"https://www.fool.com/x": 200}
        r = verify_citations([Citation(url="https://www.fool.com/x", backs_material=True)],
                             resolver=make_resolver(table))
        assert r.material_verified is False
        v = r.verdicts[0]
        assert v.live is True and v.ok is False and v.tier == int(Tier.LOW_TRUST)

    def test_non_material_dead_still_blocks(self):
        r = verify_citations([Citation(url="https://x.dead/y", backs_material=False)],
                             resolver=make_resolver({}))
        assert r.blocked is True  # a cited URL that doesn't resolve is fabrication

    def test_issuer_domain_promoted(self):
        table = {"https://ir.intel.com/pr": 200}
        r = verify_citations([Citation(url="https://ir.intel.com/pr", backs_material=True)],
                             resolver=make_resolver(table),
                             issuer_domains={"intel.com"})
        assert r.verdicts[0].tier == int(Tier.PRIMARY)
        assert r.material_verified is True

    def test_empty_never_raises(self):
        assert verify_citations([], resolver=make_resolver({})).ok is True


# ===================================================================== V3 ===
class TestV3EventStudy:
    def test_reference_fallback(self):
        r = study(EventType.STRATEGIC_CUSTOMER)
        assert r.sample == "reference"
        assert r.n == 0
        assert r.note  # honesty note present
        assert r.p25 < r.p75  # a distribution, not a point

    def test_historical_fit(self):
        samples = [10, -5, 3, 8, -2, 6, 1, 12, 4, -1]  # 10 analogs
        r = study(EventType.EARNINGS, samples=samples)
        assert r.sample == "historical"
        assert r.n == 10
        assert r.mean == round(sum(samples) / len(samples), 2)
        assert r.hit_rate == round(sum(1 for x in samples if x > 0) / len(samples), 3)

    def test_too_few_samples_falls_back(self):
        r = study(EventType.EARNINGS, samples=[1, 2, 3])  # < MIN_SAMPLES
        assert r.sample == "reference"

    def test_never_a_point_estimate(self):
        for et in EventType:
            r = study(et)
            assert r.p25 < r.p75

    def test_wave2_is_a_band(self):
        w = wave2_estimate(9.83, EventType.STRATEGIC_CUSTOMER)
        assert w["follow_through_low"] < w["follow_through_high"]
        assert w["wave2_pct_low"] < w["wave2_pct_high"]
        assert "caveat" in w and w["caveat"]

    def test_percentile_correct(self):
        # exact: median of 1..9 = 5; p25 = 3; p75 = 7
        samples = list(range(1, 10)) + [0]  # 10 vals, but check the math on 1..9
        r = study(EventType.OTHER, samples=list(range(1, 10)) + [10])
        assert r.median == pytest.approx(5.5, abs=0.6)

    def test_prices_provider_builds_samples(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE prices (symbol TEXT, price_date TEXT, close REAL)")
        # 30 ascending business-ish days for SYM, flat benchmark
        rows = []
        for i in range(30):
            d = f"2026-01-{i+1:02d}"
            rows.append(("SYM", d, 100.0 + i))      # +1/day
            rows.append(("BMK", d, 100.0))          # flat
        conn.executemany("INSERT INTO prices VALUES (?,?,?)", rows)
        conn.commit()
        # events on day 0 and day 5; horizon 20 -> both have forward data
        samples = event_study.prices_provider_samples(
            conn, "SYM", ["2026-01-01", "2026-01-06"], horizon_days=20, benchmark="BMK")
        assert len(samples) == 2
        assert all(s > 0 for s in samples)  # SYM rises, benchmark flat -> positive abnormal


# =============================================================== REGISTRY ===
def _skill(check, sid="s1", origin="self_authored"):
    return HarnessSkill(id=sid, name="t", kind="validator",
                        trigger="x", origin=origin, check=check)


class TestRegistryGate:
    def test_admit_happy_path(self):
        reg = SkillRegistry()
        sk = _skill(lambda x: x > 0)
        cases = [VerifyCase("pos", 5, True), VerifyCase("neg", -1, False)]
        out = reg.admit(sk, cases, approver="owner")
        assert isinstance(out, HarnessSkill)
        assert reg.get("s1").status == SkillStatus.ACTIVE

    def test_approve_before_verify_refused(self):
        reg = SkillRegistry()
        reg.register(_skill(lambda x: True))
        out = reg.approve("s1", "owner")
        assert isinstance(out, GateError)
        assert reg.get("s1").status == SkillStatus.PROPOSED  # unchanged

    def test_failing_case_rejects_and_blocks_approval(self):
        reg = SkillRegistry()
        reg.register(_skill(lambda x: True))  # wrong: always True
        reg.verify("s1", [VerifyCase("should_be_false", 0, False)])
        assert reg.get("s1").status == SkillStatus.REJECTED
        out = reg.approve("s1", "owner")
        assert isinstance(out, GateError)

    def test_activate_before_approve_refused(self):
        reg = SkillRegistry()
        reg.register(_skill(lambda x: x > 0))
        reg.verify("s1", [VerifyCase("pos", 5, True)])
        out = reg.activate("s1")
        assert isinstance(out, GateError)
        assert reg.get("s1").status == SkillStatus.VERIFIED

    def test_run_active_only_when_active(self):
        reg = SkillRegistry()
        reg.register(_skill(lambda x: x * 2))
        assert isinstance(reg.run_active("s1", 3), GateError)  # not active yet
        reg.verify("s1", [VerifyCase("c", 3, 6)])
        reg.approve("s1", "owner")
        reg.activate("s1")
        assert reg.run_active("s1", 3) == 6

    def test_throwing_check_counts_as_failure(self):
        def boom(x):
            raise ValueError("nope")
        reg = SkillRegistry()
        reg.register(_skill(boom))
        reg.verify("s1", [VerifyCase("c", 1, 1)])
        assert reg.get("s1").status == SkillStatus.REJECTED

    def test_empty_cases_refused(self):
        reg = SkillRegistry()
        reg.register(_skill(lambda x: x))
        assert isinstance(reg.verify("s1", []), GateError)

    def test_duplicate_id_raises(self):
        reg = SkillRegistry()
        reg.register(_skill(lambda x: x))
        with pytest.raises(ValueError):
            reg.register(_skill(lambda x: x))

    def test_self_authored_uses_identical_gate(self):
        """origin does not buy a fast path."""
        reg = SkillRegistry()
        sk = _skill(lambda x: True, origin="self_authored")
        reg.register(sk)
        # cannot jump straight to approve
        assert isinstance(reg.approve("s1", "owner"), GateError)

    def test_audit_trail_complete(self):
        reg = SkillRegistry()
        sk = _skill(lambda x: x > 0)
        reg.admit(sk, [VerifyCase("c", 1, True)], approver="owner")
        actions = [a.action for a in reg.get("s1").audit]
        assert "register" in actions
        assert "verify_pass" in actions
        assert "approve" in actions
        assert "activate" in actions

    def test_unknown_skill_refused(self):
        reg = SkillRegistry()
        assert isinstance(reg.verify("nope", [VerifyCase("c", 1, 1)]), GateError)
        assert isinstance(reg.approve("nope", "owner"), GateError)


# ====================================================== DEFAULT REGISTRY ===
class TestDefaultRegistry:
    def test_builtins_all_active(self):
        reg = tools.build_default_registry(approver="owner")
        ids = {s.id for s in reg.active()}
        assert ids == {"v1_consistency", "v2_citation", "v3_event_study"}

    def test_builtins_passed_full_gate(self):
        reg = tools.build_default_registry(approver="owner")
        for sid in ("v1_consistency", "v2_citation", "v3_event_study"):
            actions = [a.action for a in reg.get(sid).audit]
            assert "verify_pass" in actions and "approve" in actions and "activate" in actions

    def test_run_active_v1_catches_mchp(self):
        reg = tools.build_default_registry(approver="owner")
        res = reg.run_active("v1_consistency",
                             {"symbol": "MCHP", "entry_kind": "limit",
                              "entry_price": 97.5, "current_price": 99.0,
                              "market_bias": "up"})
        assert res.blocked is True


# ================================================================== TOOLS ===
class TestTools:
    def test_specs_well_formed(self):
        for spec in tools.TOOL_SPECS:
            assert spec["name"].startswith("harness_")
            assert spec["description"]
            assert spec["input_schema"]["type"] == "object"

    def test_dispatch_trade_plan(self):
        out = tools.dispatch("harness_check_trade_plan",
                             {"symbol": "MCHP", "entry_kind": "limit",
                              "entry_price": 97.5, "current_price": 99.0,
                              "market_bias": "up"})
        assert out["blocked"] is True

    def test_dispatch_event_study(self):
        out = tools.dispatch("harness_event_study", {"event_type": "earnings"})
        assert out["sample"] in ("reference", "historical")
        assert out["p25"] < out["p75"]

    def test_dispatch_unknown(self):
        assert "error" in tools.dispatch("nope", {})


# ============================================================ REPLAYS (acc) =
class TestReplayAcceptance:
    def test_mchp_end_to_end(self):
        reg = tools.build_default_registry(approver="owner")
        res = reg.run_active("v1_consistency",
                             {"entry_kind": "limit", "entry_price": 97.5,
                              "current_price": 99.0, "market_bias": "up"})
        assert "R1_REL_WEAKNESS" in res.codes()

    def test_intc_caught_then_fixed(self):
        caught = tools.verify_citations_skill({
            "citations": [{"url": "https://www.cnbc.com/intel.html", "backs_material": True},
                          {"url": "https://www.reuters.com/intel", "backs_material": True}],
            "resolver": make_resolver({"https://www.reuters.com/intel": 200}),
        })
        assert caught.blocked is True

        fixed = tools.verify_citations_skill({
            "citations": [{"url": "https://www.reuters.com/intel", "backs_material": True},
                          {"url": "https://finance.yahoo.com/intel", "backs_material": True}],
            "resolver": make_resolver({"https://www.reuters.com/intel": 200,
                                       "https://finance.yahoo.com/intel": 200}),
        })
        assert fixed.ok is True and fixed.material_verified is True


# ============================================== META: STORE + ADMIN GATE ===
class TestStoreGate:
    def test_propose_creates_proposed(self, tmp_path):
        p = tmp_path / "s.json"
        rec = hstore.propose("x", "trigger", path=p)
        assert rec["status_label"] == "PROPOSED"
        assert hstore.get(rec["id"], path=p)["status_label"] == "PROPOSED"

    def test_approve_before_verify_refused(self, tmp_path):
        p = tmp_path / "s.json"
        rec = hstore.propose("x", "t", path=p)
        res = hstore.transition(rec["id"], "approve", "alex", path=p)
        assert res["ok"] is False
        assert hstore.get(rec["id"], path=p)["status_label"] == "PROPOSED"  # unchanged

    def test_activate_before_approve_refused(self, tmp_path):
        p = tmp_path / "s.json"
        rec = hstore.propose("x", "t", path=p)
        hstore.transition(rec["id"], "verify", "ci", path=p)
        assert hstore.transition(rec["id"], "activate", "owner", path=p)["ok"] is False

    def test_full_gate(self, tmp_path):
        p = tmp_path / "s.json"
        rec = hstore.propose("x", "t", path=p)
        assert hstore.transition(rec["id"], "verify", "ci", path=p)["ok"]
        assert hstore.transition(rec["id"], "approve", "alex", path=p)["ok"]
        assert hstore.transition(rec["id"], "activate", "owner", path=p)["ok"]
        out = hstore.get(rec["id"], path=p)
        assert out["status_label"] == "ACTIVE"
        assert out["approved_by"] == "alex"
        actions = [a["action"] for a in out["audit"]]
        assert actions == ["propose", "verify", "approve", "activate"]

    def test_unknown_skill_refused(self, tmp_path):
        p = tmp_path / "s.json"
        assert hstore.transition("nope", "verify", "ci", path=p)["ok"] is False


class TestAdminCLI:
    def test_verify_with_candidate_passes(self, tmp_path):
        p = tmp_path / "s.json"
        rec = hstore.propose("pos_check", "t", path=p)
        cmap = {"pos_check": (lambda x: x > 0,
                              [VerifyCase("a", 5, True), VerifyCase("b", -1, False)])}
        res = hadmin._verify(rec["id"], False, "", p, candidates_map=cmap)
        assert res["ok"] is True
        assert hstore.get(rec["id"], path=p)["status_label"] == "VERIFIED"

    def test_verify_failing_candidate_rejects(self, tmp_path):
        p = tmp_path / "s.json"
        rec = hstore.propose("bad", "t", path=p)
        cmap = {"bad": (lambda x: True, [VerifyCase("should_false", 0, False)])}
        res = hadmin._verify(rec["id"], False, "", p, candidates_map=cmap)
        assert res["ok"] is False
        assert hstore.get(rec["id"], path=p)["status_label"] == "REJECTED"

    def test_no_candidate_refused_without_manual(self, tmp_path):
        p = tmp_path / "s.json"
        rec = hstore.propose("nyi", "t", path=p)
        res = hadmin._verify(rec["id"], False, "", p, candidates_map={})
        assert res["ok"] is False
        assert hstore.get(rec["id"], path=p)["status_label"] == "PROPOSED"

    def test_manual_verify_allowed(self, tmp_path):
        p = tmp_path / "s.json"
        rec = hstore.propose("nyi", "t", path=p)
        res = hadmin._verify(rec["id"], True, "ran pytest", p, candidates_map={})
        assert res["ok"] is True
        assert hstore.get(rec["id"], path=p)["status_label"] == "VERIFIED"

    def test_run_full_lifecycle_exit_codes(self, tmp_path):
        p = tmp_path / "s.json"
        rec = hstore.propose("k", "t", path=p)
        assert hadmin.run(["list"], path=p) == 0
        assert hadmin.run(["approve", rec["id"], "--by", "alex"], path=p) == 1  # before verify
        assert hadmin.run(["verify", rec["id"], "--manual", "--note", "x"], path=p) == 0
        assert hadmin.run(["approve", rec["id"], "--by", "alex"], path=p) == 0
        assert hadmin.run(["activate", rec["id"]], path=p) == 0
        assert hstore.get(rec["id"], path=p)["status_label"] == "ACTIVE"


# ================================================= AGENT WIRING (live file) ==
class TestAgentWiring:
    # V1/V2 moved from model-elective MCP tools to after_model run-loop processors
    # (see TestAfterModelHook in test_harness_x.py); only V3 + propose stay tools.
    HARNESS_TOOLS = {"harness_event_study", "harness_propose_skill"}
    REMOVED_TOOLS = {"harness_check_trade_plan", "harness_verify_citations"}

    def test_in_cio_tools(self):
        from cio.agent import CIO_TOOLS
        names = {t.name for t in CIO_TOOLS}
        assert self.HARNESS_TOOLS <= names
        # the two consistency/citation tools are gone from the model surface
        assert self.REMOVED_TOOLS.isdisjoint(names)

    def test_mcp_prefixed_and_allow_listed(self):
        from cio.agent import build_options
        allowed = set(build_options().allowed_tools)
        for t in self.HARNESS_TOOLS:
            assert f"mcp__cio__{t}" in allowed
        for t in self.REMOVED_TOOLS:
            assert f"mcp__cio__{t}" not in allowed

    def test_agent_tool_event_study_runs_end_to_end(self):
        import asyncio
        from cio.agent import t_harness_event_study
        out = asyncio.run(t_harness_event_study.handler({"event_type": "earnings"}))
        assert '"sample"' in out["content"][0]["text"]

    def test_agent_propose_tool_files_proposed(self, monkeypatch):
        import asyncio
        from cio import agent as ag
        seen = {}

        def fake_propose(name, trigger, kind="validator", rule_spec="", **k):
            seen["name"] = name
            return {"id": "sk_test"}

        monkeypatch.setattr(ag.harness.store, "propose", fake_propose)
        out = asyncio.run(ag.t_harness_propose_skill.handler(
            {"name": "n", "trigger": "t", "kind": "validator", "rule_spec": "r"}))
        assert "sk_test" in out["content"][0]["text"]
        assert seen["name"] == "n"
