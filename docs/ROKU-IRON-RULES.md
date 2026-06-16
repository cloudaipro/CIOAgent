# ROKU Iron Rules — swing-decision canon

Named after the ROKU miss: a full-bull technical composite (5/5 oscillators green)
fired right after an exhausting spike, while the catalyst was already spent. The
blended score hid it; these rules exist so it can't happen again.

> Status: rules 1–5 are transcribed from the committee discussion (conv_turns
> 280–289) where they were first stated in prose; they had no codified home before
> this file. Owner to reconcile exact wording. **Rule 6 is new** (2026-06) and is
> the corrected version of the originally-proposed "win-rate ≥ 60% ⇒ overfit".

## Rule 1 — Catalyst first
A trade needs a *reason price should move* (earnings/guidance/filing/regulatory/
product), established **before** any chart read. No catalyst → no trade, however
pretty the setup. Enforced by the `catalyst` layer being mandatory in
`cio.committee.tirf.gate`.

## Rule 2 — Indicators are lagging confirmation, not forward signal
TA (RSI/MACD/KDJ/Squeeze/Fisher) describes the math left *after* price moved. It
times a thesis; it never originates one. These live in the **execution layer**
(`cio.stock.profiles`), explicitly walled off from signal generation.

## Rule 3 — A full-bull composite after a large run is an exhaustion tell, not a buy
When every oscillator is green *because* the move already happened, that is
consumption, not confirmation. Treat a maxed composite arriving late in an extended
move as a reason for caution. (The literal ROKU lesson.)

## Rule 4 — Layers score independently; a green layer never rescues a red one
Catalyst / behavior / momentum / execution are scored separately and AND-gated. A
strong execution read cannot lift a weak catalyst into a trade. No cross-layer
averaging that lets timing hide a missing "why".

## Rule 5 — Manage risk on R, not on hope
Position size and stop are set from risk (R = entry − stop) before entry. Exits are
mechanical (trailing stop while the thesis holds; hard exit when the catalyst layer
flips). See `cio.alpha.hold`.

## Rule 6 — Out-of-sample validation is mandatory; overfit is flagged by expectancy decay, not win-rate
**Original proposal (rejected):** "any backtest with win-rate ≥ 60% is presumed
overfit." Rejected because it (a) contradicts the expectancy-over-win-rate principle
(win-rate level says nothing about edge — mean-reversion books legitimately win
60–70% with small wins), and (b) catches the wrong cases both ways.

**Adopted form:**
1. Out-of-sample / walk-forward validation is required for **every** strategy,
   regardless of win-rate. OOS hygiene is universal, not a high-win-rate tripwire.
2. Overfit is flagged when **out-of-sample expectancy < 50% of in-sample
   expectancy** (or OOS expectancy ≤ 0 while in-sample is positive). This is the
   real degradation signal. Enforced by `cio.alpha.expectancy.oos_check`.
3. A minimum sample (`MIN_SAMPLE = 20` closed trades) applies before any expectancy
   figure is trusted; thin samples are reported low-confidence, not acted on.
4. Track the number of filter/parameter combinations tried; the more trials behind a
   "good" backtest, the larger the deflation it deserves (data-snooping bias,
   Sullivan-Timmermann-White 1999).

**KPI consequence:** win-rate is demoted to a sub-stat. The headline is
`expectancy = win% · avg_win − loss% · avg_loss`, annualized by turnover
(`cio.alpha.expectancy.summary`).
