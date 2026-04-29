# Phase 2 Profile Baseline — `perf-1` (2026-04-27)

First of two perf bookends for Phase 2 (Core Code Architecture). This is the
**before** snapshot; `perf-2` (PR 12) will re-run the same suite against
post-split `main` and flag any wall-clock delta `>10%` per benchmarked path
as a candidate regression to bisect.

## Suite

`scripts/profile_suite.py` runs `cProfile` over four hot paths, 3× each, on
the production analytics DB. Each section reports median wall-clock + IQR
(or range, for `n=3`), then the top 20 functions sorted by cumulative time
from the final run.

| Bench | Function under test | Data |
| --- | --- | --- |
| `backtest BTCUSDT/1h wick_fill` | `analytics.backtest_lib.run_backtest` | 30d slice of BTCUSDT/1h OHLCV |
| `param_sweep wick_fill 1h` | `analytics.param_sweep.run_param_sweep` | 30d, 6-combo grid (`min_wick_body_ratio` × `lookback`) |
| `run_scan_cycle BTCUSDT/15m wick_fill (cloned DB)` | `analytics.signal_lib.run_scan_cycle` | full scan against a `/tmp` clone of the production DB |
| `combo backtest wick_fill+bos BTCUSDT/1h` | `analytics.backtest_lib.run_combo_backtest` | 30d slice, two strategies |

Hardware: developer laptop (`Linux 6.17.10-100.fc41.x86_64`, Python 3.13).
DB: `analytics.db` (~129 MiB), max OHLCV ts 2026-04-26.

## Wall-clock summary

| Bench | Run 1 | Run 2 | Run 3 | Median | IQR / range |
| --- | --- | --- | --- | --- | --- |
| backtest BTCUSDT/1h wick_fill | 0.369 s | 0.352 s | 0.363 s | **0.363 s** | 0.017 s |
| param_sweep wick_fill 1h | 0.453 s | 0.463 s | 0.450 s | **0.453 s** | 0.013 s |
| run_scan_cycle BTCUSDT/15m wick_fill | 0.453 s | 0.276 s | 0.276 s | **0.276 s** | 0.177 s |
| combo backtest wick_fill+bos BTCUSDT/1h | 0.317 s | 0.317 s | 0.311 s | **0.317 s** | 0.006 s |

The `run_scan_cycle` IQR is dominated by the first-run overhead of cloning
the 129 MiB `analytics.db` to `/tmp` (cold OS file cache). Runs 2 and 3 hit
warm cache and converge to ~0.276 s. `perf-2` should compare against the
**median** to mute that one-shot copy cost.

## Top-5 cumulative per bench

### backtest BTCUSDT/1h wick_fill

| ncalls | cumtime | function |
| --- | --- | --- |
| 1 | 0.207 s | `analytics/indicators_lib.py:747(detect_wick_fills)` |
| 2,977 | 0.182 s | `pandas indexing.py:1192(__getitem__)` |
| 2,977 | 0.171 s | `pandas indexing.py:1740(_getitem_axis)` |
| 2,986 | 0.165 s | `pandas frame.py:4292(_ixs)` |
| 1 | 0.135 s | `analytics/backtest_lib.py:392(run_backtest)` |

Detector dominates wall-clock; `run_backtest` itself is ~135 ms.
Pandas `.iloc`-style row access and `fast_xs` show up on the hot path.

### param_sweep wick_fill 1h

| ncalls | cumtime | function |
| --- | --- | --- |
| 1 | 0.216 s | `concurrent.futures.process.py:842(shutdown)` |
| 1 | 0.207 s | `analytics/backtest_runner.py:61(detect_signals_for_strategy)` |
| 1 | 0.207 s | `analytics/indicators_lib.py:747(detect_wick_fills)` |
| 23 | 0.167 s | `selectors.py:385(select)` |
| 23 | 0.167 s | `select.poll` |

ProcessPoolExecutor shutdown time and worker-poll waits dominate.
The 6-combo grid runs at ~0.22 s pure compute (see `[perf]` lines in
the raw output).

### run_scan_cycle BTCUSDT/15m wick_fill (cloned DB)

| ncalls | cumtime | function |
| --- | --- | --- |
| 1 | 0.066 s | `shutil.py:230(copyfile)` (DB clone overhead) |
| 1 | 0.061 s | `analytics/signal_lib.py:1051(_scan_task)` |
| 1 | 0.060 s | `analytics/signal_lib.py:347(scan_symbol)` |
| 1 | 0.059 s | `analytics/indicators_lib.py:747(detect_wick_fills)` |
| 503 | 0.043 s | `pandas indexing.py:1192(__getitem__)` |

The DB clone is bench-rig overhead, not real `run_scan_cycle` cost.
Subtracting that line gives ~165 ms net per cycle for one symbol/tf/strategy.

### combo backtest wick_fill+bos BTCUSDT/1h

| ncalls | cumtime | function |
| --- | --- | --- |
| 1 | 0.208 s | `analytics/indicators_lib.py:747(detect_wick_fills)` |
| 3,717 | 0.190 s | `pandas indexing.py:1192(__getitem__)` |
| 3,717 | 0.179 s | `pandas indexing.py:1740(_getitem_axis)` |
| 2,197 | 0.149 s | `pandas frame.py:4292(_ixs)` |
| 2,006 | 0.120 s | `pandas managers.py:1132(fast_xs)` |

`run_combo_backtest` itself is just 52 ms; the time is dominated by the two
upstream detector runs and the cofire-signal join.

## Notes for `perf-2`

- Benchmark windows are anchored at `MAX(open_time)` for BTCUSDT/1h at run
  time, minus 30 days. A 4-week DB-update lag between `perf-1` and `perf-2`
  shifts the slice by ~30 days but keeps the row count constant (~720 rows
  for 1h, ~2,880 for 15m).
- The `run_scan_cycle` bench includes a one-shot DB clone — count the
  steady-state median (runs 2–3), not run 1.
- `cProfile` itself adds ~5–10 % overhead. If `perf-2` shows a regression in
  the 5–10 % band, re-run a few times to confirm vs. profile noise before
  bisecting.
- Suite is reusable: `poetry run python scripts/profile_suite.py` prints the
  same shape; raw output is what `perf-2` should compare against.

## Deviation from plan

The plan (`docs/superpowers/plans/2026-04-27-phase2-architecture.md` PR 1
Step 1) sketched a `profile_suite.py` skeleton whose `_bench_*` calls used
idealised, not-current signatures (`get_ohlcv(symbol, tf, db_path=...)`,
`run_backtest(df=..., spec=...)`, `run_param_sweep(symbols=[...], ...)`,
`run_scan_cycle(client=MagicMock(), ...)`). The actual signatures require
an open `DuckDBPyConnection`, an explicit `start/end` ms range, a single
strategy/symbol/timeframe per `run_param_sweep`, and a `CooldownStore`
for `run_scan_cycle`. The committed file preserves the plan's intent
(4 benches, 3-run median + IQR, top-20 cumulative) but adapts each
benchmark to current call shapes.

## Raw suite output

```text
=== backtest BTCUSDT/1h wick_fill ===
runs: [0.369, 0.3519, 0.3633]
median: 0.363s   IQR/range: 0.017s
         691029 function calls (682875 primitive calls) in 0.356 seconds

   Ordered by: cumulative time
   List reduced from 486 to 20 due to restriction <20>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1    0.008    0.008    0.207    0.207 /home/kng/repo/buibui-moon-trader-bot/analytics/indicators_lib.py:747(detect_wick_fills)
     2977    0.005    0.000    0.182    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/indexing.py:1192(__getitem__)
     2977    0.006    0.000    0.171    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/indexing.py:1740(_getitem_axis)
     2986    0.004    0.000    0.165    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/frame.py:4292(_ixs)
        1    0.005    0.005    0.135    0.135 /home/kng/repo/buibui-moon-trader-bot/analytics/backtest_lib.py:392(run_backtest)
     1869    0.020    0.000    0.110    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/internals/managers.py:1132(fast_xs)
      277    0.002    0.000    0.065    0.000 /home/kng/repo/buibui-moon-trader-bot/analytics/backtest_lib.py:66(_is_volume_spike)
      277    0.002    0.000    0.063    0.000 /home/kng/repo/buibui-moon-trader-bot/analytics/backtest_lib.py:43(_is_low_volume)
     1119    0.003    0.000    0.043    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/frame.py:4337(__getitem__)
     1869    0.001    0.000    0.043    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/internals/managers.py:111(interleaved_dtype)
     1869    0.010    0.000    0.042    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/dtypes/cast.py:1306(find_common_type)
      554    0.001    0.000    0.035    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/generic.py:6360(astype)
     1117    0.001    0.000    0.035    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/frame.py:4966(_get_item)
     7109    0.007    0.000    0.034    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/series.py:945(__getitem__)
179079/179074    0.023    0.000    0.031    0.000 {built-in method builtins.isinstance}
     4865    0.019    0.000    0.026    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/arrays/arrow/array.py:722(__getitem__)
     3739    0.004    0.000    0.024    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/internals/blocks.py:1932(iget)
     1117    0.002    0.000    0.022    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/frame.py:4954(_box_col_values)
      554    0.000    0.000    0.020    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/util/_decorators.py:328(wrapper)
      554    0.000    0.000    0.020    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/series.py:8063(mean)




  Sweep: wick_fill / BTCUSDT / 1h (sl_pct dropped — strategy uses structural SLs)
  Grid size: 6 combos | IS candles: 503 | OOS candles: 217 | workers: 6
  Running......... done
  [perf] grid (6 combos): 0.22s

  Sweep: wick_fill / BTCUSDT / 1h (sl_pct dropped — strategy uses structural SLs)
  Grid size: 6 combos | IS candles: 503 | OOS candles: 217 | workers: 6
  Running......... done
  [perf] grid (6 combos): 0.23s

  Sweep: wick_fill / BTCUSDT / 1h (sl_pct dropped — strategy uses structural SLs)
  Grid size: 6 combos | IS candles: 503 | OOS candles: 217 | workers: 6
  Running......... done
  [perf] grid (6 combos): 0.22s

=== param_sweep wick_fill 1h ===
runs: [0.4533, 0.4632, 0.4502]
median: 0.453s   IQR/range: 0.013s
         474648 function calls (466919 primitive calls) in 0.445 seconds

   Ordered by: cumulative time
   List reduced from 730 to 20 due to restriction <20>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1    0.000    0.000    0.216    0.216 /usr/lib64/python3.13/concurrent/futures/_base.py:646(__exit__)
        1    0.000    0.000    0.216    0.216 /usr/lib64/python3.13/concurrent/futures/process.py:842(shutdown)
        1    0.000    0.000    0.207    0.207 /home/kng/repo/buibui-moon-trader-bot/analytics/backtest_runner.py:61(detect_signals_for_strategy)
        1    0.008    0.008    0.207    0.207 /home/kng/repo/buibui-moon-trader-bot/analytics/indicators_lib.py:747(detect_wick_fills)
      2/1    0.000    0.000    0.190    0.190 /usr/lib64/python3.13/threading.py:1058(join)
      2/1    0.000    0.000    0.190    0.190 {method 'join' of '_thread._ThreadHandle' objects}
      2/1    0.000    0.000    0.190    0.190 /usr/lib64/python3.13/threading.py:1000(_bootstrap)
      2/1    0.000    0.000    0.190    0.190 /usr/lib64/python3.13/threading.py:1027(_bootstrap_inner)
        1    0.000    0.000    0.190    0.190 /usr/lib64/python3.13/concurrent/futures/process.py:330(run)
        1    0.000    0.000    0.190    0.190 /usr/lib64/python3.13/concurrent/futures/process.py:549(join_executor_internals)
        1    0.000    0.000    0.190    0.190 /usr/lib64/python3.13/concurrent/futures/process.py:553(_join_executor_internals)
        1    0.000    0.000    0.182    0.182 /usr/lib64/python3.13/multiprocessing/queues.py:145(join_thread)
        8    0.000    0.000    0.182    0.023 /usr/lib64/python3.13/multiprocessing/util.py:272(__call__)
        1    0.000    0.000    0.181    0.181 /usr/lib64/python3.13/multiprocessing/queues.py:212(_finalize_join)
        8    0.000    0.000    0.181    0.023 /usr/lib64/python3.13/concurrent/futures/process.py:405(wait_result_broken_or_wakeup)
       23    0.000    0.000    0.172    0.007 /usr/lib64/python3.13/multiprocessing/connection.py:1134(wait)
       23    0.000    0.000    0.167    0.007 /usr/lib64/python3.13/selectors.py:385(select)
       23    0.167    0.007    0.167    0.007 {method 'poll' of 'select.poll' objects}
     1869    0.003    0.000    0.157    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/indexing.py:1192(__getitem__)
     1869    0.004    0.000    0.151    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/indexing.py:1740(_getitem_axis)




=== run_scan_cycle BTCUSDT/15m wick_fill (cloned DB) ===
runs: [0.4532, 0.2759, 0.2757]
median: 0.276s   IQR/range: 0.177s
         130207 function calls (128055 primitive calls) in 0.165 seconds

   Ordered by: cumulative time
   List reduced from 532 to 20 due to restriction <20>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1    0.000    0.000    0.066    0.066 /usr/lib64/python3.13/shutil.py:230(copyfile)
        1    0.000    0.000    0.065    0.065 /usr/lib64/python3.13/shutil.py:112(_fastcopy_sendfile)
        2    0.065    0.033    0.065    0.033 {built-in method posix.sendfile}
        1    0.000    0.000    0.061    0.061 /home/kng/repo/buibui-moon-trader-bot/analytics/signal_lib.py:1051(_scan_task)
        1    0.000    0.000    0.060    0.060 /home/kng/repo/buibui-moon-trader-bot/analytics/signal_lib.py:347(scan_symbol)
        1    0.002    0.002    0.059    0.059 /home/kng/repo/buibui-moon-trader-bot/analytics/indicators_lib.py:747(detect_wick_fills)
      503    0.001    0.000    0.043    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/indexing.py:1192(__getitem__)
      503    0.001    0.000    0.042    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/indexing.py:1740(_getitem_axis)
      505    0.001    0.000    0.037    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/frame.py:4292(_ixs)
      498    0.006    0.000    0.030    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/internals/managers.py:1132(fast_xs)
        1    0.000    0.000    0.015    0.015 /usr/lib64/python3.13/pathlib/_local.py:740(unlink)
        1    0.015    0.015    0.015    0.015 {built-in method posix.unlink}
        1    0.013    0.013    0.013    0.013 {built-in method _duckdb.connect}
      499    0.000    0.000    0.012    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/internals/managers.py:111(interleaved_dtype)
      499    0.003    0.000    0.012    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/dtypes/cast.py:1306(find_common_type)
     1892    0.002    0.000    0.009    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/series.py:945(__getitem__)
        1    0.008    0.008    0.008    0.008 /home/kng/repo/buibui-moon-trader-bot/analytics/stats_lib.py:250(compute_p1p2_daily)
      996    0.001    0.000    0.007    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/internals/blocks.py:1932(iget)
36549/36542    0.005    0.000    0.006    0.000 {built-in method builtins.isinstance}
     1019    0.004    0.000    0.005    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/arrays/arrow/array.py:722(__getitem__)




=== combo backtest wick_fill+bos BTCUSDT/1h ===
runs: [0.3173, 0.3168, 0.3111]
median: 0.317s   IQR/range: 0.006s
         655099 function calls (642904 primitive calls) in 0.303 seconds

   Ordered by: cumulative time
   List reduced from 574 to 20 due to restriction <20>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1    0.008    0.008    0.208    0.208 /home/kng/repo/buibui-moon-trader-bot/analytics/indicators_lib.py:747(detect_wick_fills)
     3717    0.005    0.000    0.190    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/indexing.py:1192(__getitem__)
     3717    0.007    0.000    0.179    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/indexing.py:1740(_getitem_axis)
     2197    0.004    0.000    0.149    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/frame.py:4292(_ixs)
     2006    0.023    0.000    0.120    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/internals/managers.py:1132(fast_xs)
        1    0.000    0.000    0.052    0.052 /home/kng/repo/buibui-moon-trader-bot/analytics/backtest_lib.py:678(run_combo_backtest)
     2007    0.001    0.000    0.046    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/internals/managers.py:111(interleaved_dtype)
     2007    0.011    0.000    0.045    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/dtypes/cast.py:1306(find_common_type)
     8108    0.009    0.000    0.038    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/series.py:945(__getitem__)
177105/177088    0.022    0.000    0.033    0.000 {built-in method builtins.isinstance}
        1    0.001    0.001    0.030    0.030 /home/kng/repo/buibui-moon-trader-bot/analytics/backtest_lib.py:392(run_backtest)
     2007    0.003    0.000    0.019    0.000 {built-in method fromkeys}
     8108    0.006    0.000    0.018    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/series.py:1029(_get_value)
      278    0.001    0.000    0.014    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/frame.py:1538(iterrows)
     4058    0.002    0.000    0.013    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/arrays/arrow/array.py:722(__getitem__)
     4276    0.016    0.000    0.022    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/arrays/string_.py:258(__hash__)
        1    0.002    0.002    0.013    0.013 /home/kng/repo/buibui-moon-trader-bot/analytics/backtest_lib.py:392(run_backtest)
     2007    0.003    0.000    0.019    0.000 {built-in method fromkeys}
     8108    0.006    0.000    0.018    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/series.py:1029(_get_value)
      278    0.001    0.000    0.014    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/frame.py:1538(iterrows)
     4059    0.002    0.000    0.013    0.000 /home/kng/repo/buibui-moon-trader-bot/.venv/lib64/python3.13/site-packages/pandas/core/arrays/string_.py:258(__hash__)
        1    0.002    0.002    0.013    0.013 /home/kng/repo/buibui-moon-trader-bot/scripts/profile_suite.py:92(_bench_window_ms)
```
