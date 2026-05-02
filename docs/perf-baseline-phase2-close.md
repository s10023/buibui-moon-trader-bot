# Phase 2 Profile Close — `perf-2` (2026-05-02)

Second of two perf bookends for Phase 2 (Core Code Architecture). This is the
**after** snapshot; it re-runs `scripts/profile_suite.py` against post-split
`main` and compares against the `perf-1` baseline ([PR #328], 2026-04-27).
Threshold for a candidate regression is **>10% wall-clock delta** per
benchmarked path, locked in `perf-1`.

[PR #328]: https://github.com/s10023/buibui-moon-trader-bot/pull/328

## Verdict

**Phase 2 closes clean.** All four hot paths land within ±10 % of baseline,
all in the green band. The 3–6 % faster medians are within day-to-day noise
on the same laptop (data drift in the 30-day window + warm OS file cache)
and are not load-bearing improvements — the meaningful claim is **no
measurable regression**, not *speedup*. The 12-PR split (cli-1, stats-1,
backtest-1, store-1, store-2, signal-1, signal-2, signal-3, strat-1,
strat-2, strat-3) introduced no detectable wall-clock cost on any hot
path. Top-5 cumulative frames now resolve to their post-split module
paths, confirming the splits are real and observable in the profiler.

## Wall-clock diff vs `perf-1`

| Bench | perf-1 median | perf-2 median | Δ abs | Δ % | Verdict |
| --- | --- | --- | --- | --- | --- |
| backtest BTCUSDT/1h wick_fill | 0.363 s | **0.343 s** | -0.020 s | **-5.5 %** | green |
| param_sweep wick_fill 1h | 0.453 s | **0.439 s** | -0.014 s | **-3.1 %** | green |
| run_scan_cycle BTCUSDT/15m wick_fill | 0.276 s | **0.261 s** | -0.015 s | **-5.4 %** | green |
| combo backtest wick_fill+bos BTCUSDT/1h | 0.317 s | **0.307 s** | -0.010 s | **-3.2 %** | green |

All deltas are within day-to-day noise on the same dev laptop and are not
load-bearing improvements — the meaningful claim is *no regression*, not
*speedup*. `run_scan_cycle` keeps the same first-run-cold profile as
`perf-1` (run 1 = 0.314 s for the `/tmp` DB clone, runs 2–3 ≈ 0.258 s steady
state); the median is the right comparison either way.

## Hot-path frames moved (where they should)

`perf-1` saw these top-5 frames in monolith files. `perf-2` resolves the
same logical frames inside their post-split modules:

| Logical frame | perf-1 location | perf-2 location |
| --- | --- | --- |
| `detect_wick_fills` | `analytics/indicators_lib.py:747` | `analytics/strategies/wick_fills.py:11` |
| `run_backtest` | `analytics/backtest_lib.py:392` | `analytics/backtest/engine.py:348` |
| `run_combo_backtest` | `analytics/backtest_lib.py:*` | `analytics/backtest/combo.py:101` |
| `scan_symbol` / `_scan_task` | `analytics/signal_lib.py:*` | `analytics/signal/scanner.py:75`, `:351` |
| `_is_low_volume` / `_is_volume_spike` | `analytics/backtest_lib.py:*` | `analytics/backtest/gates.py:6`, `:29` |
| `detect_market_structure` (combo) | `analytics/indicators_lib.py:*` | `analytics/strategies/market_structure.py:11` |
| `compute_p1p2_daily` (scan) | `analytics/stats_lib.py:*` | `analytics/stats/p1p2.py:22` |

Every legacy `analytics/{indicators,backtest,signal,stats}_lib.py` reference
in the `perf-1` top-20 is now resolved to its post-split package location.
The remaining lib shims (e.g. `analytics/signal_lib.py` 4-line shim,
`analytics/backtest_lib.py` re-export shim) are no longer on the hot path.

## Top-5 cumulative per bench (post-split)

### backtest BTCUSDT/1h wick_fill

| ncalls | cumtime | function |
| --- | --- | --- |
| 1 | 0.202 s | `analytics/strategies/wick_fills.py:11(detect_wick_fills)` |
| 3,009 | 0.176 s | `pandas indexing.py:1192(__getitem__)` |
| 3,009 | 0.166 s | `pandas indexing.py:1740(_getitem_axis)` |
| 3,018 | 0.160 s | `pandas frame.py:4292(_ixs)` |
| 1 | 0.120 s | `analytics/backtest/engine.py:348(run_backtest)` |

Detector still dominates wall-clock (60 %); `run_backtest` core 35 %; pandas
`.iloc`-style row access on the hot path. Per-frame cumtimes within ±5 ms
of `perf-1`. New volume-gate frames (`gates.py:6` / `:29`, ~57 ms) appear in
the top-20 because they're now standalone functions in their own module
rather than inlined branches in `backtest_lib.py` — the work was already
being done; the profiler just attributes it differently.

### param_sweep wick_fill 1h

| ncalls | cumtime | function |
| --- | --- | --- |
| 8 | 0.337 s | `concurrent.futures.process.py:405(wait_result_broken_or_wakeup)` |
| 1 | 0.205 s | `analytics/backtest_runner.py:61(detect_signals_for_strategy)` |
| 1 | 0.205 s | `analytics/strategies/wick_fills.py:11(detect_wick_fills)` |
| 1 | 0.201 s | `concurrent.futures._base.py:646(__exit__)` |
| 1 | 0.201 s | `concurrent.futures.process.py:842(shutdown)` |

WFO sweep is process-pool bound — 6 worker shutdowns dominate cumtime.
Detector cumtime byte-equivalent to single-bench backtest, confirming
parallel workers cost nothing extra per detector call.

### run_scan_cycle BTCUSDT/15m wick_fill (cloned DB)

| ncalls | cumtime | function |
| --- | --- | --- |
| 1 | 0.067 s | `shutil.py:230(copyfile)` *(one-shot DB clone)* |
| 1 | 0.054 s | `analytics/signal/scanner.py:351(_scan_task)` |
| 1 | 0.053 s | `analytics/signal/scanner.py:75(scan_symbol)` |
| 1 | 0.052 s | `analytics/strategies/wick_fills.py:11(detect_wick_fills)` |
| 452 | 0.037 s | `pandas indexing.py:1192(__getitem__)` |

Three top frames now resolve to `analytics/signal/scanner.py` — the
signal-3 split is observable. `signal_lib.py` (the 4-line shim) does not
appear in the top-20 at all, as expected.

### combo backtest wick_fill+bos BTCUSDT/1h

| ncalls | cumtime | function |
| --- | --- | --- |
| 1 | 0.208 s | `analytics/strategies/wick_fills.py:11(detect_wick_fills)` |
| 3,749 | 0.190 s | `pandas indexing.py:1192(__getitem__)` |
| 3,749 | 0.179 s | `pandas indexing.py:1740(_getitem_axis)` |
| 2,232 | 0.149 s | `pandas frame.py:4292(_ixs)` |
| 2,049 | 0.120 s | `pandas internals/managers.py:1132(fast_xs)` |
| 1 | 0.048 s | `analytics/backtest/combo.py:101(run_combo_backtest)` |
| 1 | 0.030 s | `analytics/strategies/market_structure.py:11(detect_market_structure)` |

`combo` orchestrator now resolves to `analytics/backtest/combo.py:101`
(was `analytics/backtest_lib.py` pre-split); the secondary BOS detector
resolves to `analytics/strategies/market_structure.py:11`. Same arithmetic
shape as `perf-1`: two detectors + a co-fire pass over the merged signal
frame.

## Hardware / data

Same dev laptop as `perf-1` (`Linux 6.17.10-100.fc41.x86_64`, Python 3.13).
`analytics.db` max OHLCV ts: 2026-05-02 (vs 2026-04-26 in `perf-1`). The
30-day benched window slides forward by 6 days; bar count and signal counts
drift slightly, which is the expected source of the 3–6 % per-bench delta.

## Phase 2 perf gate: PASS

12-PR split, ≤ 6 % wall-clock movement on every hot path, all on the green
side. No bisect needed. Phase 2 closes.
