# A08 re-run: the 10 live `volume_suppress` flips under fixed costs, causal detectors, CI + Holm and a day-cluster key

Wayfinder task [#923](https://github.com/s10023/buibui-moon-trader-bot/issues/923) under map #864. Register row A08 in the tested register (#907); re-run rule from #908.

Follow-on: #970 (backtest reads per-TF flags, done), #971 (two weekend 1h-short cells reverted, #996).

## Verdict

None of the 10 flips holds. Under the faithful re-run every cell reads INSUFFICIENT: no suppressed-slice CI clears +0.05R, and every Holm-adjusted p is 1.000. None reverses inside the original window either, because no CI clears -0.05R. The DISABLE was never established: the tool's current rule (CI + Holm + UTC-day cluster key) already reads all 10 as INSUFFICIENT on the original May trades, before any cost or detector fix is applied. No cell is a powered null. CI half-widths run 0.4 to 1.0R, 8 to 20 times the 0.05R bar, so the setting of these flags is unmeasured, not shown to be irrelevant.

Three facts change how the flips should be read. (1) The filed margins were zero-cost gross, not fee-only as the register says. Both original sweeps stored `fee_pct = 0.0`, and every one of 12,785 closed mon_fri trades sits on an exact half-R multiple. (2) Five of the 10 flips are inert. The live configs' `[strategy_timeframes]` allowlist already excluded those cells when they were flipped, so the live scanner never evaluates them. (3) Out of sample the picture runs the other way on two live weekend cells. Over 2026-05-21 to 2026-10-09, weekend `liquidity_sweep` 1h short reads ENABLE: suppressed slice -0.880R net, CI [-1.27, -0.39], Holm-adj p 0.030. Its low-volume live alerts since the flip average -1.271R (26 alerts on 8 days). Weekend `wick_fill` 1h short's low-volume live alerts average -0.724R (71 alerts on 24 days).

Recommendation: revert the two weekend 1h-short cells (`liquidity_sweep`, `wick_fill`) to ON and leave the other eight as they are. The reasoning is in the Recommendation section. It is a config decision for the operator, and writing the TOML is the deployment.

## Method

A faithful re-run in #908's sense keeps the same cells, tool, rule inputs, configs and window, and fixes only the defects.

- **Configs**: `config/signal_watch_weekdays.toml` (mon_fri) and `config/signal_watch_all.toml` (weekend) at `0d25437^`, the commit before the flips (#398), with the original permissive overlay. The overlay sets `volume_suppress = false` and clears both directional flags on bos, engulfing, orb and liquidity_sweep (mon_fri), plus wick_fill (weekend).
- **Window**: `since 2025-09-12` to each original sweep's own `data_end_ms` (2026-05-21, about 02:26 UTC), read from the stored sweeps `66c320c3` and `31fcf380`.
- **Scope**: 3 symbols × 4 TFs × every strategy in the config. The May engine did not enforce `strategy_timeframes` (both stored sweeps hold all 4 TFs for every strategy), so the allowlist was cleared to measure the same cells. Live-parity on (regime, direction_filter, f8_htf_ema, adr_bias, conflict_resolver, cooldown). The conflict resolver got an empty ratings map, as the original did ("loaded 0 rating(s)").
- **Defects fixed**: C4 costs are read from `config/strategy_params.toml`: `fee_pct` 0.0005 per leg, `[backtest] slippage_bps` 2.0 per leg, `min_sl_pct` 0.005, plus funding, all inside the engine's net `pnl_r`. C5: the current, causal `bos` and `liquidity_sweep` detectors (fixed 2026-08-18). C1 and C8: `tools/gate_audit.py::build_audit_table`, which runs `analytics.audit_guard` with a cluster bootstrap CI and a Holm haircut on `n_eff`, keyed on `utc_day_keys`. C6: a fresh unsaved run with no duplicates (dedup on symbol, tf, strategy, direction, entry_time dropped 0 rows).
- **Rule**: the tool's defaults are grain strategy × tf × direction, `min_n` 30, bar ±0.05R, alpha 0.05, `n_boot` 2000, seed 12345. The Holm family is every eligible cell in the config's table: 39 on mon_fri, 54 on weekend.
- **Reproduction check**: run on the stored May trades (12,929 and 23,839 rows), the driver reproduces every filed `n_kept`, `n_supp` and `supp_avg_r` in the 2026-05-21 audit to three decimals.

Two reads beyond the faithful re-run are reported as supplementary looks, not verdicts. The **holdout** runs the same construction from 2025-09-12 to 2026-10-09 and keeps trades signalled after the original window ends. It is out of sample for the flip decision. The **live ledger** is `signal_alert_outcomes` for the live cells after the flip, with low volume tagged by the engine's own `_is_low_volume` on the signal candle. It is descriptive only: alerts cluster by day and the n is small.

## Results

Units are R per trade. "Filed" is the 2026-05-21 point estimate (gross). "Stored, current tool" is the same May trades under today's CI rule (fixes C1 and C8 only). "Re-run" is the faithful re-run (all fixes, net). The deferred rows are the two cells #398 left ON, shown for context.

| Config | Cell | Scanned live | Filed supp (gross) | Stored, current tool: CI | Re-run: n_supp | Re-run: supp net | Re-run: CI | Re-run: adj p | Re-run verdict | Holdout: n / supp / CI | Holdout verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mon_fri | engulfing 15m short | no | +0.073 | [-0.28, +0.50] | 268 | -0.091 | [-0.48, +0.38] | 1.000 | INSUFF | 117 / +0.148 / [-0.60, +1.11] | INSUFF |
| mon_fri | engulfing 1h long | no | +0.211 | [-0.40, +0.88] | 51 | -0.096 | [-0.85, +0.77] | 1.000 | INSUFF | 29 / -0.057 / n/a | INSUFF |
| mon_fri | engulfing 1h short | no | +0.158 | [-0.33, +0.68] | 55 | +0.123 | [-0.48, +0.77] | 1.000 | INSUFF | 37 / -0.240 / [-1.05, +0.80] | INSUFF |
| mon_fri | orb 1h short | yes | +0.349 | [-0.34, +1.18] | 39 | +0.153 | [-0.58, +0.95] | 1.000 | INSUFF | 23 / -0.882 / n/a | INSUFF |
| mon_fri | liquidity_sweep 1h short | no | +0.500 | [-0.25, +1.33] | 57 | +0.338 | [-0.41, +1.16] | 1.000 | INSUFF | 32 / -0.856 / [-1.25, -0.35] | ENABLE |
| weekend | engulfing 1h long | no | +0.189 | [-0.35, +0.76] | 53 | +0.042 | [-0.69, +0.92] | 1.000 | INSUFF | 46 / -0.225 / [-0.97, +0.66] | INSUFF |
| weekend | orb 4h short | yes | +0.529 | [-0.36, +1.54] | 49 | +0.608 | [-0.32, +1.67] | 1.000 | INSUFF | 18 / +0.545 / n/a | INSUFF |
| weekend | liquidity_sweep 1h short | yes | +0.177 | [-0.56, +1.05] | 64 | +0.438 | [-0.34, +1.32] | 1.000 | INSUFF | 46 / -0.880 / [-1.27, -0.39] | ENABLE |
| weekend | wick_fill 1d long | yes | +0.186 | [-0.34, +0.73] | 20 | +0.045 | n/a | n/a | INSUFF | 12 / -0.152 / n/a | INSUFF |
| weekend | wick_fill 1h short | yes | +0.128 | [-0.14, +0.41] | 330 | +0.062 | [-0.40, +0.55] | 1.000 | INSUFF | 141 / -0.628 / [-1.06, -0.06] | INSUFF |
| weekend | orb 1h short (deferred) | yes | +0.328 | [-0.30, +1.00] | 74 | +0.004 | [-0.62, +0.74] | 1.000 | INSUFF | 32 / -0.686 / [-1.21, +0.22] | INSUFF |
| weekend | wick_fill 4h short (deferred) | yes | +0.070 | [-0.28, +0.50] | 88 | +0.091 | [-0.42, +0.64] | 1.000 | INSUFF | 41 / -0.479 / [-1.08, +0.30] | INSUFF |

"n/a" means n_supp is below the tool's `min_n` of 30, so the cell was never tested. The suppressed slices' mean modelled drag in the re-run runs from 0.06R (engulfing) to 0.25R (liquidity_sweep, wick_fill 1h), which is the range the register predicted (0.07 to 0.28R). On the full sample, 2025-09-12 to 2026-10-09, all 12 cells stay INSUFFICIENT, and 8 of the 10 flips have a negative suppressed-slice point estimate. Only weekend `orb` 4h short (+0.591R, n=67) and mon_fri `engulfing` 15m short (+0.023R, n=389) stay positive.

Live ledger since the flip, low-volume alerts on live-scanned cells (net R, descriptive): weekend `liquidity_sweep` 1h short -1.271R (26 alerts, 8 days; its normal-volume alerts -1.020R, 10 alerts). Weekend `wick_fill` 1h short -0.724R (71 alerts, 24 days; normal-volume +0.292R, 11 alerts). Weekend `orb` 4h short -0.776R (15 alerts, 9 days). Weekend `wick_fill` 1d long +0.278R (11 alerts, 9 days). Mon_fri `orb` 1h short +0.078R (11 alerts).

## Findings

1. **The corrected rule never established a DISABLE.** On the May trades alone, the C1 and C8 fixes move all 10 cells to INSUFFICIENT. The cost and causality fixes then shrink the margins further. Engulfing 15m short and 1h long change sign (to -0.091 and -0.096R), and liquidity_sweep 1h short mon_fri falls from +0.500 to +0.338R. None of these shifts is needed for the verdict. Under the audit's own INSUFFICIENT branch ("defer, keep current"), with ON as the current state on 2026-05-21, none of the 10 flips would have been made.
2. **The filed margins were zero-cost, not fee-only.** Register row A08 and caveat C4b describe the May backtests as fee-only. These two sweeps ran at `fee_pct = 0.0`, which is the zero-cost programmatic-sweep class (C4c). The ±0.05R bar was therefore compared with gross margins, where the honest round-trip drag on these cells is 0.06 to 0.25R.
3. **Five flips are inert.** On mon_fri, `engulfing` is allowlisted to ["4h", "1d"] (since 2026-05-17) and `liquidity_sweep` to ["1d"]. On weekend, `engulfing` is allowlisted to ["15m", "1d"]. Those lists were in the TOMLs before #398 and still are, and `cli/signal.py` passes them to the scanner. So the mon_fri engulfing 15m short, 1h long and 1h short flips, the mon_fri liquidity_sweep 1h short flip and the weekend engulfing 1h long flip change nothing live. Their `volume_suppress_*_per_tf` entries are dead config.
4. **The backtest never sees any of the 10 flips.** `analytics/backtest_config.py` does not parse `volume_suppress_long_per_tf` / `volume_suppress_short_per_tf`. Only the live path resolves them (`analytics/signal_config.py`, `analytics/signal/resolvers.py`). Every backtest of the live configs, and the star ratings and quality gate built on them, therefore measures these cells with the gate ON while live runs them OFF. This is a backtest-live parity gap, filed separately.
5. **Out of sample, the two live weekend 1h-short cells lose on the slice the flip admitted.** Weekend `liquidity_sweep` 1h short is the one cell where a gate_audit verdict reverses: holdout ENABLE at Holm-adj p 0.030 in a 41-cell family, with live alerts agreeing. Its normal-volume side also loses live, so the cell itself is weak, not just its low-volume slice. Weekend `wick_fill` 1h short has a holdout CI that clears -0.05R but fails Holm (adj p 0.714). Its live low-volume alerts, which exist only because of the flip, average -0.724R over 24 days.
6. **The current gate stack admits fewer trades than the May stack did.** On the same configs and window, the re-run yields 65% (mon_fri) and 62% (weekend) of the May trade count. The cut falls mostly on longs (×0.53 and ×0.50 against ×0.77 for shorts) and evenly across strategies, so it is not the `bos`/`liquidity_sweep` causality fix. It is unattributed here. It does not move the verdict, because finding 1 already holds on the May trades.

## Recommendation

**Revert weekend `liquidity_sweep` 1h short and weekend `wick_fill` 1h short to ON** (`volume_suppress_short_per_tf["1h"] = true` under each strategy in `config/signal_watch_all.toml`). Leave the other eight. Make no other TOML change.

- **Why those two**: the corrected rule would have left both ON. The only positive reading is the in-window estimate (+0.438 and +0.062R), and that is the sample the flips were selected on, so it is biased upward. Every read the selection did not touch is negative: the holdout (-0.880 and -0.628R), the live ledger (-1.271 and -0.724R) and the full sample (-0.113 and -0.145R). Reverting removes exposure rather than adding it.
- **Why not the rest**: five are inert. Mon_fri `orb` 1h short, weekend `orb` 4h short and weekend `wick_fill` 1d long have out-of-sample reads that conflict or rest on 11 to 23 trades. Reverting them would act on noise in the opposite direction, which is the error the flips themselves made.
- **What the revert does not fix**: weekend `liquidity_sweep` 1h short also loses on its normal-volume alerts (-1.020R live), so a revert cuts about 70% of its alerts and leaves a losing cell. Whether to keep the cell at all is a separate decision outside this task.
- **Strength**: weak. The holdout and live reads are extra looks at cells already examined, the live n is clustered on 8 and 24 days, and the `liquidity_sweep` live rows start 2026-08-08, ten days before the live detector's causality fix. The case for reverting rests on the corrected rule's default plus a consistent out-of-sample sign, not on a gate pass. The inert entries (finding 3) can be removed whenever the TOMLs are next edited. They are harmless, but they read as live decisions.

## Caveats

- The re-run uses today's engine on the May configs. Engine and gate changes since May (finding 6) alter the population as well as the four named defects. The stored-trades reading isolates C1 and C8 cleanly, and the verdict already holds there.
- `strategy_timeframes` was cleared to match the May scope. That is right for a faithful re-run, but for the five inert cells it measures trades the live system never takes.
- The conflict resolver ran with an empty ratings map, as the original did. It is not a no-op: it still arbitrates same-candle conflicts at a rating of 0.0, so all strategies were run to keep that interaction intact.
- Costs are modelled, not realised, so every net figure is an optimistic bound.
- Holm families are per config table, as the tool computes them. A family pooled across both configs would only raise the adjusted p-values.

## Trial accounting

The in-window re-run is a faithful re-run, so it replaces A08's original trial and costs nothing from the round budget (#908 rule 4). The holdout, full-sample and live-ledger reads are extra looks at the same 12 cells. They inform the operator's config call and spend no round-1 slot. Any re-cut of these cells (another bar, grain, window or volume threshold) is a new trial under its own pre-registration.

## Sources

- `docs/audits/2026-05-21-volume-suppress-mon-fri-weekend.md`: filed table, decision rule, overlay description, window.
- `tools/gate_audit.py`, `analytics/audit_guard.py`, `analytics/research_guards` (`utc_day_keys`): the tool and rule.
- `analytics/backtest_runner.py::_collect_sweep_results`, `analytics/backtest/engine.py` (`Trade.pnl_r`): sweep and net-R definition.
- `analytics/backtest_config.py` (no `*_per_tf` volume parsing) against `analytics/signal_config.py` and `analytics/signal/resolvers.py`, plus `cli/signal.py` (`strategy_timeframes` passed to the scanner).
- `analytics.db` snapshot of 2026-10-09: `backtest_runs` / `backtest_trades` sweeps `66c320c3…` (mon_fri, 180 runs) and `31fcf380…` (weekend, 216 runs), `ohlcv`, `signal_alert_outcomes`.
- Driver and analysis scripts: `docs/plans/scratch/wayfinder-864/round1/a08_*.py` (gitignored, local).
