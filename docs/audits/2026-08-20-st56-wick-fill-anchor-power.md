# ST56 — `wick_fill` wick-anchor: power pricing and pre-registration (2026-08-20)

**Verdict: BUILD the one pre-registered construction. The power gate CLEARS on the generated
backtest panel — required effect +0.0264R per trade at an effective n of 15,938, against a
corpus best of +1.196R — and, unusually for this repo, a null result there would be
LICENSABLE rather than INSUFFICIENT (CI half-width 0.0318 against a bar of ±0.0359).** ST56's
own pessimism ("n=57 total, so any trigger split lands cells at 15-30 and is unreachable
before it starts") priced the wrong population: it read the live ledger's long side, while the
study must GENERATE its legs. The refusal of the operator's "long and wide exploration of
trigger conditions" half is UPHELD and is now measured on this panel rather than inherited —
1 trial to 320 moves the bar +0.0264R to +0.7053R, a 26.7x rise, against a strategy whose best
existing cell means +0.3427R per trade.

No measurement of the construction has been run. The pre-registration below is fixed by this
commit, before any number describing the wick anchor's performance exists.

## Three corrections to the ST56 premise

Filed claims are hypotheses. All three below are measured against `analytics.db` on
2026-08-20; none reverses ST56's direction, and the third inverts its verdict.

**1. There are TWO discard mechanisms, and ST56 named only one.** The row says the structural
stop "fails the side-of-entry test" so the flat `sl_pct` fallback "fires essentially always".
Measured across all 170 live `wick_fill` rows:

| Stop distance | n | Share | Mechanism |
| --- | --- | --- | --- |
| exactly 2.000% | 95 | 55.9% | flat `sl_pct` fallback — the side-of-entry test failed |
| exactly 0.500% | 56 | 32.9% | **`min_sl_pct` FLOOR — the wick survived the side test and was then clamped away** |
| genuinely structural | 19 | 11.2% | the detector's own level reached the ledger |

The floor is the unnamed half and it is a third of the population. `analytics/signal/scanner.py`
takes `sl_dist = max(entry - structural, entry * min_sl_pct)`, so a wick CLOSER than 0.5% is
discarded just as completely as one on the wrong side — and a wick-fill entry is by
construction near its own wick. ST56's specification question therefore has two answers to
give, not one.

**2. The cited line number is stale and the "all 33 shorts" claim is one cell.** ST56 cites
`scanner.py:101`; that is now the live EV gate. The real site is the SL/TP resolver at
`analytics/signal/scanner.py:136-163`. And "all 33 `wick_fill` shorts carry `sl_pct` of exactly
2.000%" describes the 15m short cell (33 rows) — there are 79 shorts in total, and the 1h short
cell (n=41) sits at a median of 0.500%, i.e. on the floor rather than the fallback.

**3. The study population is 31,038 trades, not 57.** Live `wick_fill` rows number 170 (91
long, 79 short), already above ST56's figure. The construction is a stop-geometry change, so
its legs must be generated: `backtest_trades` deduped on
`(symbol, timeframe, strategy, direction, entry_time)` holds **31,038 distinct wick_fill
trades** (18,821 long / 12,217 short) across BTCUSDT / ETHUSDT / SOLUSDT. This is AGENTS.md's
own multi-regime lesson 1 — a historical study must generate its legs, not query them —
applied to a row that forgot it.

## The power pricing

Panel is 3 symbols, so the correlation deflator applies. It was **computed, not quoted**:
`analytics.forecast.effective_independent_series` over per-symbol daily R gives
**n_eff = 1.5405, t_deflator = 1.3955**, and the spec's own cross-check passes
(`n_eff x deflator^2 = 3.0` recovers `n_series`).

⚠ **AGENTS.md's filed deflator for a three-symbol panel does not reproduce.** It records
"n_eff is 1.97 for 14 perps and 1.42 for three, giving deflators of 1.628x and 3.331x". The
production definition is `t_deflator = sqrt(k / n_eff)`, so n_eff 1.42 on three series gives
1.4537 — never 3.331. The n_eff figures are plausible for their panels; the two deflators are
not recoverable from them in either pairing. Do not quote that clause; run the function.

Measured inputs, all from the deterministic dedup: `sd = 2.0485`, `skew = 1.7686`,
`kurtosis = 4.4724` (non-excess), `sr_variance = 0.013212` across the 24 cells with at least
20 trades.

`poetry run python tools/distil_power.py --units per_trade --n-obs 31038 --n-trials 1
--sr-variance 0.013212 --n-series 3 --n-eff 1.5405 --sd 2.0485 --skew 1.7686 --kurtosis 4.4724
--corpus-best 1.196 --bar 0.0359`

```text
distil_power - G3 power gate
  units             per_trade
  n_obs (declared)  31,038
  effective n       15,938  (deflated by n_eff 1.5405 / 3 series)
  trial family      1 trials, sr_variance 0.013212
  gate target       DSR >= 0.95

  required Sharpe   0.012881
  required effect   +0.0264 per trade  (sd 2.0485)
  corpus best       +1.1960
  VERDICT           REACHABLE

  null bar          +/-0.0359
  CI half-width     0.0318  (best case, point estimate exactly 0)
  powered null      LICENSABLE  (analytics.audit_guard.powered_null)
```

The same call at `--n-obs 170 --sd 1.3955` — the live OOS ledger — returns REACHABLE at
+0.2142R per trade but **NOT LICENSABLE** (CI half-width 0.2932). So the live ledger can
confirm a large effect and can never license a null. **The backtest panel is the primary and
the live ledger is a directional check, not a second opinion.**

## Why the wide scan stays refused, priced on this panel

| Trial family | Required effect per trade |
| --- | --- |
| 1 (pre-registered) | **+0.0264R** |
| 8 | +0.3661R |
| 64 | +0.5781R |
| 320 | +0.7053R |

The best `wick_fill` cell in the corpus is BTCUSDT/1d/short at **+0.3427R per trade** (n=31,
Sharpe 0.2241), and the pooled deduped mean is **−0.1658R**. So even an EIGHT-trial family
already demands more (+0.3661R) than the strategy's best observed cell has ever produced, and a
320-trial family demands 2.1x it. The bar rises faster than any scan can find, which is the
arithmetic reason the operator's framing is refused — not taste, and not a general appeal to
the 6-for-6 conditioning record.

## The pre-registration — ONE construction, fixed before measurement

**Claim.** Anchoring `wick_fill`'s stop on the spike candle's own wick, rather than discarding
it to the flat fallback or the 0.5% floor, produces a higher mean R per trade than the
production geometry on the same fires.

**Construction, fixed:**

1. Population: all `wick_fill` fires on BTCUSDT / ETHUSDT / SOLUSDT across 15m / 1h / 4h / 1d,
   deduped on the five-key. 1d is retained but reported separately — n is 233 and it cannot
   carry its own verdict.
2. Stop: the extreme of the spike candle that formed the wick, on the far side of entry. No
   `min_sl_pct` floor and no flat fallback. A fire whose wick anchor is unavailable or on the
   wrong side of entry is **dropped and counted**, never silently defaulted.
3. **Entry is legal at `i+2` or later.** The wick is defined by candle `i`'s own extreme, which
   is the exact confirmation-causality shape that withdrew the structural-touch BUILD (all six
   cells flipped sign). `tests/test_lookahead.py` is the gate.
4. Target: the existing `tp_r` applied to the new stop distance. No TP search — that would be a
   second axis and a second trial.
5. Comparison: paired against production geometry on the identical fire set, so the difference
   is geometry alone.
6. Verdict gate: the published three legs, `DSR >= 0.95 AND PBO <= 0.5 AND boot_lo > 0`, via
   `analytics.research_guards.passes_gate`. One trial. No per-cell scan.

**Success metric:** mean R per trade on the paired difference, against the +0.0264R bar above.

**Decision Log — what reverses this:** a wick-anchor drop rate above ~40% of fires (the anchor
is then not a property of the strategy but of a subset, and the paired comparison stops being
about geometry); or any i+2 legality failure in `test_lookahead.py`, which voids the result
outright rather than reducing it.

## Two tooling defects found while running this

**`tools/distil_power.py` does not bootstrap `sys.path`.** A bare
`python3 tools/distil_power.py ...` dies with `ModuleNotFoundError: No module named 'analytics'`,
while `sanity_checks.py` and `post_branch_checks.py` both bootstrap and AGENTS.md advertises
that they do. It is the tool a gate is required to run, so it should not need a caller to know
about `PYTHONPATH`.

**DuckDB `DISTINCT ON` without a full `ORDER BY` is non-deterministic, and the repo's dedup
rule does not say so.** AGENTS.md prescribes dedup on the five-key. Run without a tiebreak
column, the same dedup over the same table returned per-trade sd of 1.6609, 1.7588 and 2.0485
across three executions — a 23% spread, on a quantity that divides directly into every required
effect. Every figure in this audit uses
`order by symbol, timeframe, strategy, direction, entry_time, trade_id`.
