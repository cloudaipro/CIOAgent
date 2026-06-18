# Rule → Harness pointer edits (DRAFT — do not apply yet)

> Ready-to-drop-in text that points the stored behavioral rules at the now-wired
> harness tools (V1 `harness_check_trade_plan`, V2 `harness_verify_citations`,
> V3 `harness_event_study`).
>
> **DO NOT APPLY until the harness has a few clean production runs.** Reasons in
> `HARNESS-ENGINEERING-EVALUATION.md` / the chat: the tools are unproven in prod
> (V2's live resolver hasn't run against the network; the bot hasn't exercised the
> tools), and coupling the prompt-level guards to an untested dependency before
> then weakens guards that currently work on their own. Run belt-and-suspenders
> first; apply these once the tools have demonstrably agreed with the rules on real
> turns.
>
> All edits are **additive** (append to the existing value — keep the current rule
> text verbatim). mem-note text is Traditional Chinese to satisfy
> `language_rule_traditional_chinese` (1613); playbook text is English to match.

---

## mem_notes (table `mem_notes`, scope `chat:8535885767`)

Apply later by re-saving the note under the **same key** with `existing_value +
the append below` (the `remember` tool / dashboard Memory tab upserts by key — copy
the current value first so nothing is lost).

### 1616 · key `swing_entry_threesome_rule` → V1 `harness_check_trade_plan`

APPEND:

```
工具強制（harness 上線並驗證後啟用）：輸出任何進場計畫前先呼叫 harness_check_trade_plan。
R1_REL_WEAKNESS 任一嚴重度（WARN 或 BLOCK）= 此進場非有效裸進場，必須先做 catalyst check，
catalyst 清空才可提出；WARN 不等於可直接輸出（detail.catalyst_check_required 恆為 true）。
此工具為本規則的決定性實作，與本規則同義；本規則為人類可讀來源（threshold 漂移時以工具為準）。
```

### 1614 · key `swing_screen_catalyst_rule` → V1 (R1/R5) + V3

APPEND:

```
工具對應（harness 驗證後啟用）：規則 2c（個股 vs 指數相對偏離）已由 harness_check_trade_plan
的 R1_REL_WEAKNESS 決定性實作——任一嚴重度即代表「相對弱、需 catalyst check、非有效裸進場」。
規則 3（大暴漲後 bull composite = 消耗性訊號）之「漲幅可以多大」改用 harness_event_study 取
分布（mean/median/quartiles，非點估），勿自行捏造量級數字。進場評估前呼叫工具。
```

### 1615 · key `evidence_citation_rule` → V2 `harness_verify_citations`

APPEND:

```
工具對應（harness 驗證後啟用）：CHECK 3（Tier-Class／材料事實佐證需 ≥1 Tier-1 或 ≥2 獨立
Tier-2）已由 harness_verify_citations 決定性實作，並新增 URL 存活檢查（fetch-before-cite，
失效／404 連結 fail-closed）——這是 1615 原本未涵蓋的部分。發送任何含查證的回覆前呼叫
harness_verify_citations；死連結不可引用、不可計入佐證；material_verified=false 即不可裸述材料事實。
```

---

## playbook (table `playbooks`, id 7, name `swing_watchlist_reevaluation`)

Apply later by re-saving the playbook (overwrite by name) with the two steps
amended. Append the lines below to the END of each step's text; leave everything
else verbatim.

### Step four (Catalyst-First binding) — APPEND

```
Tooling (enable after the harness is proven): for each anomaly candidate call
harness_check_trade_plan (R1_REL_WEAKNESS at ANY severity ⇒ catalyst check
mandatory before a valid entry), and for "how big can the move be" use
harness_event_study (forward-return distribution, never a point estimate; do not
invent magnitude numbers). The tools are the deterministic enforcement of this
step; this text remains the human-readable source.
```

### Step seven (three-element entry output) — APPEND

```
Tooling (enable after the harness is proven): before emitting each entry zone, call
harness_check_trade_plan with the zone's entry/stop/target + current price + market
bias. A R1_REL_WEAKNESS finding at ANY severity (WARN or BLOCK) means element two
fails — relative weakness, catalyst check required, NOT a valid entry until the
catalyst clears. A WARN is never "safe to present"; it is "catalyst check required".
Naked price without this check = INVALID OUTPUT (unchanged).
```

---

## Later: optional slim-down (separate, even more conservative)

Once the tools are fully trusted, the long self-check prose in 1614/1615/1616 can be
**replaced** (not just appended) by a short "call the tool; treat any R1 finding as
catalyst-check-required; treat material_verified=false as not-assertable" — a real
token win (these notes load every relevant session). Do this only after the append
stage above has run cleanly for a while; it removes the independent prompt-level
guard, so it is the last step, not the first.
