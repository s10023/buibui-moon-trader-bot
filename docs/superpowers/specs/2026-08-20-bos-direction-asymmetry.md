# `bos` LONG/SHORT direction asymmetry — pre-registration

**Date:** 2026-08-20 · **Status:** pre-registered, **PRICED UNREACHABLE — DO NOT RUN**
· **Source:** the direction split exposed by the `bos` causality fix (`d5fba6c`, #652,
2026-08-18) · **Scope:** `backtest_trades` / `backtest_runs`, read-only

Fixed **before** any effect was estimated. The cells in §2 come from the causality-fix
narrative and were named in the task brief, not chosen from a table. Working numbers and the
full `distil_power` transcript live in the gitignored scratch note of the same date.

**The verdict is the deliverable.** "Unreachable, do not build" is a successful output, and
this file exists so the question is not re-opened at the same cost a fourth time.

## 1. Question

The causality fix moved the two direction arms of `bos` in opposite directions. Does `bos`
carry a real, gate-clearing LONG/SHORT asymmetry — i.e. is a short-only `bos` book an edge?

Direction is the one conditioning axis this repo has repeatedly found real, which is why the
question is worth pricing rather than dismissing. It is nonetheless a **new construction** and
inherits the full three-leg gate.

## 2. Cells, fixed now

**Primary:** `bos` / `1h` / `short`, pooled across the three `day_filter` configs
(`mon_fri`, `tue_thu`, `weekend`) and the three symbols in the signal-watch universe
(BTCUSDT, ETHUSDT, SOLUSDT).

**Declared control:** `bos` / `1h` / `long`, same pooling. The asymmetry claim requires the
short arm to clear and the long arm not to; a construction where both clear is a `bos`-level
result, not a direction result, and fails this pre-registration.

**Population:** `backtest_trades` rows from the single post-fix batch (27 runs stamped
2026-08-18 21:42–21:44, one run per `day_filter × timeframe × symbol` cell), **deduped at READ
time** on `(symbol, timeframe, strategy, direction, entry_time)`. The table is **not** trimmed —
it is an append-only run log, and trimming breaks `select_rated_run_ids`' ranking.

Everything stamped before that batch pre-dates the causality fix and is excluded as unverified.
No config declares `bos` on `1d`, so `1d` is outside the family entirely.

## 3. The gate

Verdict is **`analytics.research_guards.passes_gate(dsr, pbo, boot_lo)`**, called — not
restated. All four coded sleeve verdicts delegate to that one function and this would be the
fifth. The thresholds are `GATE_DSR` and `GATE_PBO` in that module; **do not copy their values
into this document or into a driver.** `min_track_record_length` may be printed as a stamp and
gates nothing.

A null result is licensable only via `analytics.audit_guard.powered_null`. §5 shows it is not
licensable here, so **this study cannot return "no asymmetry" either** — only INSUFFICIENT.

## 4. Trial count — 18

The family is `3 timeframes {15m,1h,4h} × 3 configs × 2 directions = 18`.

It is **18 and not 2** because the 1h cells were selected by looking across all of them: the
asymmetry was noticed where it was largest. Declaring the family as the two arms actually
tested would price the search out of existence, which is the precise failure this repo has
already met six times. The honest family is what was searched.

`sr_variance = 0.365897`, the variance of per-trade Sharpe across those 18 cells using
live-parity-gated means. (Ungated means give 0.030413; both are reported in §5.)

## 5. Power — priced, not estimated

Run with `tools/distil_power.py` (`PYTHONPATH=.`; it does not bootstrap `sys.path`).
Deflator `--n-series 3 --n-eff 1.42` — three perps carry 1.42 effective independent series,
a **3.331×** deflator. The 25-symbol 2.92× does **not** transfer and must not be reused here.
`sd = 1.4162` R/trade, bar ±0.10R, corpus best +1.196R.

| run | n_obs | eff n | trials | required effect | verdict |
| --- | ---: | ---: | ---: | ---: | --- |
| **A — primary**, live-parity gated | 89 | 42 | 18 | **+2.1176** | REACHABLE, **bar exceeds the corpus best** |
| B — ungated trade rows | 531 | 251 | 18 | +0.6119 | REACHABLE |
| C — ungated, family shrunk to 2 | 531 | 251 | 2 | +0.2771 | REACHABLE |
| D — most generous: 1 trial, no deflator | 531 | 531 | 1 | +0.1013 | REACHABLE |

**The powered null is NOT LICENSABLE in every run, D included.** Best-case CI half-widths are
0.4283 / 0.1752 / 0.1752 / 0.1205 against a ±0.10R bar.

### Why more data cannot rescue run A

The required effect at 18 trials converges to **+1.5895 R/trade** as n → ∞. That asymptote sits
above the largest asymmetry ever observed in these cells (+1.1929R, on n=28 vs n=31) and above
the corpus best (+1.1960R). A bisection for the n that brings the bar under +0.30R ran to
2×10⁸ effective observations without converging, because no such n exists.

**This is a trial-family problem, not a sample-size problem** — the standing verdict that trial
count dominates n, reproduced exactly.

Shrinking the family is the only lever and it is bounded too. At 2 trials the floor is
+0.4463R and `eff n = 251` needs 531 declared trades. Gated `bos/1h` fires at **95.4 shorts and
85.7 longs per year** pooled across all three symbols, so a forward-only two-arm test returns
its first answer in **5.6 years (short) / 6.2 years (long)**.

## 6. Stop width is held FIXED across the arms

`sl_pct = 0.02` in every run of the batch, and `count(distinct sl_pct) = 1` within each of the
18 cells. Both direction arms therefore carry an **identical stop distance**, so adverse-first
intrabar tie resolution biases them by the same construction and the *difference* is unbiased
to first order.

**This construction does not inherit the ST56 indeterminacy** (the 2026-08-20 wick-fill
stop-geometry result). That indeterminacy binds where one arm is systematically tighter; here
neither is.

It is a property of the flat-2% stop rather than a design achievement — and it is also why the
comparison is unattractive on its own terms: **both arms sit inside the known flat-2% defect**,
so whatever it measured would be a property of that stop, not of direction.

## 7. Verdict

**UNREACHABLE. Do not build, do not run.**

Not underpowered — unreachable. The honest 18-trial family sets a bar no effect this corpus has
ever produced could clear, at any sample size; the only family small enough to be reachable
needs six years of forward data; and no configuration reached can license a null. The study can
return neither a confirmation nor a refutation.

The motivating observation is also weaker than it looks. `signal_watch bos/1h/short` ★3 +0.4215
rests on **31 trades** (`10 + 9 + 12` across the three symbols); across the nine `config × tf`
gated cells the sign of `short − long` is **5 positive, 4 negative**, with the two largest
positives resting on `n_long = 5` and `n = 1`; and every ungated per-trade cell mean is
negative, the best being +0.0253 on n=238. The +0.42 appears only after gating cuts 238 → 31.

## 8. Decision Log

| # | Decision | Observable that REVERSES it |
| --- | --- | --- |
| 1 | Trial family is 18, not 2 | A `bos` direction split declared **before** any cell is inspected, tested only on trades accruing after that declaration. That family is legitimately 2 — and still needs ~5.6 years to fill. |
| 2 | Primary population is the live-parity-gated one (n=89), not the ungated rows (n=531) | The claim being priced is a *tradeable* short-only book. If the proposal changes to an ungated research signal, run B becomes primary and the bar drops to +0.6119. |
| 3 | Verdict is UNREACHABLE rather than INSUFFICIENT | A trial family credibly reduced to ≤3 **and** an effect above the corresponding floor (+0.7317R at 3 trials). Both are needed; either alone leaves this standing. |
| 4 | Correlation deflator is 3.331× | A change to the rated symbol set. `n_eff` is a property of the panel: 1.42 for three perps, 1.97 for fourteen, 2.92 for twenty-five. Re-derive with `analytics.forecast.effective_independent_series`; never carry this one over. |
| 5 | Does not inherit the ST56 tie-break indeterminacy | Any variant that widens or tightens one arm relative to the other — ATR-scaled stops, a per-direction `min_sl_pct`, a structural target. That variant inherits it in full and is unprovable from OHLC alone. |
| 6 | `bos` direction is closed as a *sized* question | It is not closed as a *descriptive* one. Reporting the split costs no trial count and carries no gate, the same way a give-back or expiry statistic does. |
