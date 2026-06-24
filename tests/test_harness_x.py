"""Tests for the HarnessX layer (items 1-4 + optional advisor):

  1. typed processor / hook run loop  (runloop.py, processors.py)
  4. situation-routed variants         (profiles.harness_for + build_runloop)
  2. change-manifest + Level-2 gate    (models.SkillManifest, registry)
  3. regression seesaw suite           (regression.py)
  opt. propose-only advisor            (advisor.py)

Deterministic: no network (citation resolver injected), no live cfo.db.
See docs/HARNESS-X-DESIGN.md.
"""
import json

import pytest

from cio.harness import (
    Hook, Event, ProcessorResult, RunResult, Runloop,
    Finding, Severity,
    SkillRegistry, VerifyCase, HarnessSkill, SkillStatus, GateError, SkillManifest,
)
from cio.harness import processors, regression, advisor, tools
from cio.harness import store as hstore
from cio.stock import profiles


def _plan(d: dict) -> str:
    return "```plan\n" + json.dumps(d) + "\n```"


def make_resolver(table):
    return lambda u: table.get(u)


_MCHP = {"symbol": "MCHP", "entry_kind": "limit", "entry_price": 97.5,
         "current_price": 99.0, "market_bias": "up"}
_CLEAN = {"symbol": "X", "entry_kind": "breakout", "entry_price": 100,
          "current_price": 99, "stop_price": 95, "target_price": 110,
          "market_bias": "up"}


# ============================================================ RUN LOOP (1) ==
class _Boom:
    name = "boom"
    hook = Hook.AFTER_MODEL
    def process(self, event):
        raise RuntimeError("processor blew up")


class _Noter:
    name = "noter"
    hook = Hook.AFTER_MODEL
    def process(self, event):
        return ProcessorResult(name="noter", note="hello",
                               findings=[Finding(code="X", severity=Severity.BLOCK)])


class _Interceptor:
    name = "icept"
    hook = Hook.AFTER_MODEL
    def process(self, event):
        return ProcessorResult(name="icept", intercept=True, replacement="REDACTED")


class TestRunloop:
    def test_throwing_processor_never_breaks_turn(self):
        rl = Runloop().add(_Boom()).add(_Noter())
        res = rl.fire(Event(text="x"))
        # boom is swallowed; noter still runs
        assert res.note == "hello"
        assert "X" in res.codes()

    def test_aggregates_notes_and_blocked(self):
        rl = Runloop().add(_Noter())
        res = rl.fire(Event())
        assert res.blocked is True
        assert res.to_row()["codes"] == ["X"]

    def test_intercept_first_wins(self):
        rl = Runloop().add(_Interceptor())
        res = rl.fire(Event())
        assert res.intercept is True
        assert res.replacement == "REDACTED"

    def test_only_fires_matching_hook(self):
        rl = Runloop().add(_Noter())  # AFTER_MODEL
        res = rl.fire(Event(hook=Hook.BEFORE_TOOL))
        assert res.results == [] and res.note == ""


# ================================================ CONSISTENCY PROCESSOR (1) ==
class TestConsistencyProcessor:
    def test_mchp_plan_block_flags_r1(self):
        p = processors.ConsistencyProcessor()
        r = p.process(Event(text=f"Setup:\n{_plan(_MCHP)}"))
        assert "R1_REL_WEAKNESS" in r.codes()
        assert r.blocked is True
        assert "R1_REL_WEAKNESS" in r.note

    def test_clean_plan_no_note(self):
        p = processors.ConsistencyProcessor()
        r = p.process(Event(text=f"Idea:\n{_plan(_CLEAN)}"))
        assert r.note == "" and r.blocked is False

    def test_no_plan_block_is_noop(self):
        p = processors.ConsistencyProcessor()
        r = p.process(Event(text="just chatting, no plan here"))
        assert r.findings == [] and r.note == ""

    def test_malformed_json_ignored(self):
        p = processors.ConsistencyProcessor()
        r = p.process(Event(text="```plan\n{not valid json}\n```"))
        assert r.findings == []  # parse failure skipped, no crash

    def test_extra_keys_filtered(self):
        d = dict(_MCHP, bogus_key=123, another="x")
        p = processors.ConsistencyProcessor()
        r = p.process(Event(text=_plan(d)))
        assert "R1_REL_WEAKNESS" in r.codes()  # unknown keys dropped, still runs

    def test_multiple_plan_blocks(self):
        text = f"{_plan(_CLEAN)}\nand also\n{_plan(_MCHP)}"
        p = processors.ConsistencyProcessor()
        r = p.process(Event(text=text))
        assert "R1_REL_WEAKNESS" in r.codes()  # the bad one is caught


# =================================================== CITATION PROCESSOR (1) ==
class TestCitationProcessor:
    def test_dead_url_blocks(self):
        p = processors.CitationProcessor()
        ev = Event(text="Intel won Apple [1].",
                   sources=[{"url": "https://dead.example/x"}],
                   resolver=make_resolver({}), extra={"material": True})
        r = p.process(ev)
        assert "C_DEAD_URL" in r.codes()
        assert r.blocked is True

    def test_live_primary_no_note(self):
        p = processors.CitationProcessor()
        ev = Event(text="fact [1]",
                   sources=[{"url": "https://live.sec.gov/x"}],
                   resolver=make_resolver({"https://live.sec.gov/x": 200}),
                   extra={"material": True})
        r = p.process(ev)
        assert r.note == "" and r.blocked is False

    def test_no_sources_is_noop(self):
        p = processors.CitationProcessor()
        r = p.process(Event(text="no citations here", sources=[]))
        assert r.findings == [] and r.note == ""

    def test_non_material_live_url_ok(self):
        p = processors.CitationProcessor()
        ev = Event(text="color commentary",
                   sources=[{"url": "https://www.fool.com/x"}],
                   resolver=make_resolver({"https://www.fool.com/x": 200}),
                   extra={"material": False})
        r = p.process(ev)
        assert r.blocked is False  # Tier-3 fine when not backing a material fact


# =========================================== ANTI-BOT BROWSER ESCALATION ==
class TestBrowserEscalation:
    """http_resolver escalates anti-bot refusals (403/429/...) to a headless
    browser; true-dead statuses (404) never pay that cost. conv_turns 488/489."""
    from cio.harness import citation as _cit
    URL = "https://www.marketscreener.com/quote/stock/X/consensus/"

    def test_disabled_keeps_stdlib_status(self, monkeypatch):
        monkeypatch.delenv("CIO_CITATION_BROWSER", raising=False)
        monkeypatch.setattr(self._cit, "_stdlib_status", lambda u, t=4.0: 403)
        called = {"n": 0}
        monkeypatch.setattr(self._cit, "browser_resolver",
                            lambda u, t=20.0: called.__setitem__("n", called["n"] + 1) or 200)
        assert self._cit.http_resolver(self.URL) == 403
        assert called["n"] == 0  # browser never invoked when disabled

    def test_anti_bot_escalates_to_browser(self, monkeypatch):
        monkeypatch.setenv("CIO_CITATION_BROWSER", "1")
        monkeypatch.setattr(self._cit, "_stdlib_status", lambda u, t=4.0: 403)
        monkeypatch.setattr(self._cit, "browser_resolver", lambda u, t=20.0: 200)
        assert self._cit.http_resolver(self.URL) == 200  # 403 -> live via browser

    def test_dead_404_never_escalates(self, monkeypatch):
        monkeypatch.setenv("CIO_CITATION_BROWSER", "1")
        monkeypatch.setattr(self._cit, "_stdlib_status", lambda u, t=4.0: 404)
        called = {"n": 0}
        monkeypatch.setattr(self._cit, "browser_resolver",
                            lambda u, t=20.0: called.__setitem__("n", called["n"] + 1) or 200)
        assert self._cit.http_resolver(self.URL) == 404
        assert called["n"] == 0  # 404 is real-dead, no browser cost

    def test_browser_failure_falls_back(self, monkeypatch):
        monkeypatch.setenv("CIO_CITATION_BROWSER", "1")
        monkeypatch.setattr(self._cit, "_stdlib_status", lambda u, t=4.0: 403)
        monkeypatch.setattr(self._cit, "browser_resolver", lambda u, t=20.0: None)
        assert self._cit.http_resolver(self.URL) == 403  # browser dead -> keep 403


# ================================================== VARIANT ROUTING (4) ==
class TestVariantRouting:
    def test_harness_for_each_profile(self):
        assert profiles.harness_for("committee") == ["v1", "v2", "v3"]
        assert profiles.harness_for("monitor") == ["v2"]
        assert profiles.harness_for("swing") == ["v1", "v3"]

    def test_unknown_profile_defaults_committee(self):
        assert profiles.harness_for("nonsense") == ["v1", "v2", "v3"]
        assert profiles.harness_for(None) == ["v1", "v2", "v3"]

    def test_alias_resolves(self):
        # "wave" aliases to swing
        assert profiles.harness_for("wave") == ["v1", "v3"]

    def test_build_runloop_processor_sets(self):
        names = lambda prof: {p.name for p in
                              processors.build_runloop(prof).processors(Hook.AFTER_MODEL)}
        assert names("committee") == {"v1_consistency", "v2_citation"}  # v3 is a tool, skipped
        assert names("monitor") == {"v2_citation"}
        assert names("swing") == {"v1_consistency"}


class TestVariantIsolation:
    """The SAME blocking plan blocks under committee but NOT under monitor (which
    carries no consistency processor) — a guard stays in its situation."""
    def test_mchp_blocks_committee_not_monitor(self):
        text = f"note\n{_plan(_MCHP)}"
        assert processors.after_model_note(text, profile="committee") is not None
        assert processors.after_model_note(text, profile="monitor") is None


# ============================================== after_model_note convenience =
class TestAfterModelNote:
    def test_note_for_bad_plan(self):
        note = processors.after_model_note(f"x\n{_plan(_MCHP)}", profile="committee")
        assert note and "R1_REL_WEAKNESS" in note

    def test_none_for_clean(self):
        assert processors.after_model_note(f"x\n{_plan(_CLEAN)}",
                                           profile="committee") is None

    def test_dead_citation_note_with_injected_resolver(self):
        note = processors.after_model_note(
            "Intel won Apple [1].", profile="committee",
            sources=[{"url": "https://dead/x"}], resolver=make_resolver({}),
            material=True)
        assert note and "C_DEAD_URL" in note

    def test_never_raises_on_garbage(self):
        assert processors.after_model_note(None) is None  # type: ignore[arg-type]


# ================================================ MANIFEST + LEVEL-2 (2) ==
class TestManifest:
    def test_complete_requires_signature_and_prediction(self):
        assert SkillManifest().complete() is False
        assert SkillManifest(attribution_signature="code=X").complete() is False
        assert SkillManifest(attribution_signature="code=X",
                             predicted_unlocks=["t"]).complete() is True

    def test_harness_skill_to_row_includes_manifest(self):
        sk = HarnessSkill(id="s", name="s",
                          manifest=SkillManifest(attribution_signature="z",
                                                 predicted_stabilizes=["a"]))
        row = sk.to_row()
        assert row["manifest"]["attribution_signature"] == "z"
        assert HarnessSkill(id="t", name="t").to_row()["manifest"] is None


class TestLevel2Gate:
    def _proc_skill(self, sid="p"):
        return HarnessSkill(id=sid, name="p", kind="processor",
                            origin="self_authored", check=lambda x: x > 0)

    def test_processor_without_l2_refused(self):
        reg = SkillRegistry()
        reg.register(self._proc_skill())
        out = reg.verify("p", [VerifyCase("unit", 5, True)])
        assert isinstance(out, GateError)
        assert "Level-2" in out.reason

    def test_processor_with_l2_verifies(self):
        reg = SkillRegistry()
        reg.register(self._proc_skill())
        l2 = VerifyCase("roundtrip", 5, True, level=2,
                        runner=lambda check, inp: check(inp))
        out = reg.verify("p", [VerifyCase("unit", 5, True), l2])
        assert reg.get("p").status == SkillStatus.VERIFIED
        assert out["max_level"] == 2

    def test_l2_runner_is_used_not_check(self):
        reg = SkillRegistry()
        reg.register(self._proc_skill())
        # runner returns a constant regardless of check; proves the runner path ran
        seen = {}
        def runner(check, inp):
            seen["ran"] = True
            return True
        l2 = VerifyCase("rt", 999, True, level=2, runner=runner)
        reg.verify("p", [VerifyCase("u", 5, True), l2])
        assert seen.get("ran") is True

    def test_validator_still_l1_only(self):
        reg = SkillRegistry()
        reg.register(HarnessSkill(id="v", name="v", kind="validator",
                                  origin="self_authored", check=lambda x: x > 0))
        reg.verify("v", [VerifyCase("u", 5, True)])
        assert reg.get("v").status == SkillStatus.VERIFIED


class TestManifestGate:
    def test_approve_refused_without_manifest_when_strict(self):
        reg = SkillRegistry(require_manifest=True)
        reg.register(HarnessSkill(id="v", name="v", kind="validator",
                                  origin="self_authored", check=lambda x: x > 0))
        reg.verify("v", [VerifyCase("u", 5, True)])
        out = reg.approve("v", "owner")
        assert isinstance(out, GateError) and "manifest" in out.reason

    def test_approve_ok_with_manifest_when_strict(self):
        reg = SkillRegistry(require_manifest=True)
        sk = HarnessSkill(id="v", name="v", kind="validator", origin="self_authored",
                          check=lambda x: x > 0,
                          manifest=SkillManifest(attribution_signature="code=Y",
                                                 predicted_unlocks=["t"]))
        reg.register(sk)
        reg.verify("v", [VerifyCase("u", 5, True)])
        assert not isinstance(reg.approve("v", "owner"), GateError)

    def test_builtin_exempt_from_manifest(self):
        reg = SkillRegistry(require_manifest=True)
        reg.register(HarnessSkill(id="b", name="b", kind="validator",
                                  origin="builtin", check=lambda x: x > 0))
        reg.verify("b", [VerifyCase("u", 5, True)])
        assert not isinstance(reg.approve("b", "owner"), GateError)


class TestBuiltinManifests:
    def test_builtins_carry_manifests(self):
        reg = tools.build_default_registry(approver="owner")
        for sid in ("v1_consistency", "v2_citation", "v3_event_study"):
            m = reg.get(sid).manifest
            assert m is not None and m.attribution_signature
            assert m.complete()


# ==================================================== REGRESSION SUITE (3) ==
class TestRegressionSuite:
    def test_real_config_has_no_regressions(self):
        assert regression.check_regression() == []

    def test_detector_catches_broken_config(self):
        # A builder that puts NO processors anywhere: the MCHP golden expects a
        # block, so it must now FAIL — proving the seesaw detects a regression.
        broken = lambda profile: Runloop()
        failures = regression.check_regression(build=broken)
        assert failures  # non-empty
        names = {f["golden"] for f in failures}
        assert "mchp_committee_blocks" in names

    def test_detector_catches_widened_guard(self):
        # A builder that wrongly runs the consistency processor on EVERY profile —
        # the monitor isolation golden (plan must NOT block under monitor) flips.
        def widened(profile):
            return Runloop().add(processors.ConsistencyProcessor())
        failures = regression.check_regression(build=widened)
        names = {f["golden"] for f in failures}
        assert "monitor_skips_consistency" in names


# ======================================================= ADVISOR (optional) =
class TestAdvisor:
    def test_digest_threshold(self):
        traces = [{"codes": ["R3_RR_FLOOR"], "ref": "t1"},
                  {"codes": ["R3_RR_FLOOR"], "ref": "t2"},
                  {"codes": ["R3_RR_FLOOR", "C_DEAD_URL"], "ref": "t3"},
                  {"codes": ["C_DEAD_URL"], "ref": "t4"}]
        pats = advisor.digest(traces, min_count=3)
        codes = {p.code for p in pats}
        assert "R3_RR_FLOOR" in codes        # 3 occurrences
        assert "C_DEAD_URL" not in codes     # only 2, below threshold

    def test_digest_sorted_by_count(self):
        traces = [{"codes": ["A"]}] * 5 + [{"codes": ["B"]}] * 3
        pats = advisor.digest(traces, min_count=3)
        assert [p.code for p in pats] == ["A", "B"]

    def test_plan_maps_known_codes_only(self):
        from cio.harness.advisor import DefectPattern
        drafts = advisor.plan([DefectPattern("R3_RR_FLOOR", 4),
                               DefectPattern("UNKNOWN_CODE", 9)])
        names = {d.name for d in drafts}
        assert "rr_floor_escalation" in names
        assert all("UNKNOWN" not in d.name for d in drafts)  # unknown skipped

    def test_run_advisor_files_proposed_only(self, tmp_path):
        p = tmp_path / "s.json"
        traces = [{"codes": ["C_DEAD_URL"], "ref": f"t{i}"} for i in range(4)]
        filed = advisor.run_advisor(traces, path=p, min_count=3)
        assert filed and all(r["status_label"] == "PROPOSED" for r in filed)
        # the boundary: every stored record is at PROPOSED, never further
        for rec in hstore.all_records(path=p):
            assert rec["status_label"] == "PROPOSED"
            assert rec["origin"] == "advisor"

    def test_run_advisor_empty_when_nothing_recurs(self, tmp_path):
        p = tmp_path / "s.json"
        filed = advisor.run_advisor([{"codes": ["R3_RR_FLOOR"]}], path=p, min_count=3)
        assert filed == []
