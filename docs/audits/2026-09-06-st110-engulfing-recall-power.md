# ST110 — `detect_engulfing` recall relaxation: power pricing

Prices the SoT ST110 candidate against the three-leg gate **before** it is designed, per the
standing rule that a hypothesis is priced rather than estimated. Read-only: no detector, config,
golden or rating was touched.

## Verdict

REACHABLE, but only as a single pre-registered pooled test — a per-cell scan is not. At one
trial the gate demands **+0.0354R per trade** and the existing engulfing book already pools at
**+0.1136R**, so the relaxation has roughly 3.2x headroom and does not need the recovered
patterns to outperform the retained ones. Widen the trial family to the eight (timeframe x
direction) cells and the bar rises to **+0.4190R**, which exceeds every engulfing cell in the
corpus except a 135-trade `1d short` — so the same hypothesis is comfortably reachable and
plainly unreachable depending only on how many ways it is asked. Trial count, not sample size,
is the whole answer here. ⚠ The headroom rests on a backtest book that the live ledger
contradicts, so treat +0.1136R as an optimistic bound and not as evidence the variant wins.

## What ST110 claims

`analytics/strategies/engulfing.py:53-54` (bullish) and `:76-77` (bearish) require the current
open to strictly clear the previous body:

```python
and curr_open < prev_body_bot     # bullish
and curr_open > prev_body_top     # bearish
```

Both inequalities were re-read on 2026-09-06 and both are still strict. On a continuous 24/7
perp tape 43.8% of bar transitions have `open == prev_close` exactly (399,227 of 912,239 over
BTC/ETH/SOL x 15m/1h/4h/1d), and on those the strict test can never fire. Relaxing to `>=` /
`<=` recovers 100,361 patterns, 62,213 -> 162,574, a **2.613x** multiplier. The loss is
direction-neutral (31,099 long vs 31,114 short), so this is recall, not bias.

## Inputs, and where each came from

Every figure below was measured on a snapshot of `analytics.db` taken 2026-09-06 (the live DB
was held by the 15-minute signal-watch write lock, so the read was taken against a copy — the
same move `/decay-review` Step 0 makes).

| Input | Value | Source |
| --- | --- | --- |
| distinct engulfing signals, current panel | 6,062 | `DISTINCT (symbol, timeframe, direction, entry_time)` |
| relaxation multiplier | 2.613x | ST110's own pattern counts, 62,213 -> 162,574 |
| `n_obs`, full relaxed book | 15,840 | 6,062 x 2.613 |
| `n_obs`, recovered subset only | 9,780 | the incremental half |
| `sd` per trade | 2.11 | measured at `tp_r` 4.0, n=25,499 — the production setting on 15m+1h, which is 95% of signals |
| `n_series` / `n_eff` | 3 / **1.8199** | `effective_independent_series` on this panel's daily net R, mean pairwise rho 0.3242 over 342 days |
| `sr_variance` | 0.0155 | variance of per-trade Sharpe across engulfing's 7 populated cells |
| corpus best | +1.2390 | best cell at n>=100 — `fib_golden_zone 4h long`, n=548 |

⚠ **The deflator was computed, not quoted.** `AGENTS.md` records 1.42 / 1.452x for "three
perps"; this panel reads **n_eff 1.8199, deflator 1.2839**. Both satisfy `n_eff x deflator^2 ==
k`, so neither is wrong — they are different panels, which is exactly why the rule forbids
carrying a deflator between them.

## The pricing

`tools/distil_power.py --units per_trade`, run three ways:

| Framing | `n_obs` | effective n | trials | required effect | verdict |
| --- | --- | --- | --- | --- | --- |
| Full relaxed book, pooled | 15,840 | 9,609 | 1 | **+0.0354R** | REACHABLE |
| Recovered subset only | 9,780 | 5,932 | 1 | **+0.0451R** | REACHABLE |
| Per-cell scan | 15,840 | 9,609 | 8 | **+0.4190R** | reachable vs the corpus, not vs engulfing |

Engulfing as it stands today, pooled over every run in the DB: **n=42,507, avg_r +0.1136,
sd 1.9314, per-trade Sharpe +0.0588.** Per cell:

| tf | direction | n | avg_r | sd |
| --- | --- | --- | --- | --- |
| 15m | long | 14,406 | −0.1280 | 1.7612 |
| 15m | short | 17,660 | +0.2556 | 2.0209 |
| 1h | long | 3,706 | −0.0013 | 1.8537 |
| 1h | short | 4,583 | +0.3124 | 2.0615 |
| 4h | long | 809 | +0.1081 | 1.8587 |
| 4h | short | 1,158 | +0.4760 | 1.9854 |
| 1d | short | 135 | +0.5036 | 1.8574 |

The 8-trial bar of +0.4190R sits above six of those seven cells. Only `4h short` (+0.4760,
n=1,158) and `1d short` (+0.5036, n=135) clear it, and the second is too thin to carry a
verdict.

## What would make this wrong

Four things, and the first two run one way:

1. **`live_parity IS TRUE` returns ZERO engulfing runs.** Every run in the table is the
   non-live-parity shape, so the +0.1136R book is not gated the way production is. Read the
   headroom as an upper bound.
2. **Backtest reads positive where the live ledger reads negative** (pooled live is −0.13R to
   −0.17R). Costs here are modelled, not realised, so the error runs toward optimism.
3. **The 2.613x multiplier assumes recovered patterns convert to trades at the retained rate.**
   The backtest gates (volume, ADR, day filter) are independent of an `open == prev_close` tie,
   so the assumption is reasonable — but it is an assumption, and the relaxed book's true `n`
   is only knowable by running the variant.
4. **`sr_variance` 0.0155 is engulfing's own dispersion, not a searched family's.** If the
   eventual construction searches anything else — a tolerance band, a body-ratio floor — both
   the trial count and the variance rise, and the third row of the table is where that lands.

ST56's intrabar tie-break hazard does **not** bind here: the relaxation changes a detector
admission condition, not stop geometry, and entry stays at `curr_close` on both arms, so
neither arm is systematically tighter.

## If it is built

- **One pooled pre-registered test.** Not per cell, not per timeframe, not per direction. The
  table above is the entire argument.
- It is a **new construction** and owes the full three-leg gate (`DSR >= 0.95 ∧ PBO <= 0.5 ∧
  boot_lo > 0`) under its own pre-registration, via `analytics.research_guards.passes_gate`.
- It **moves every `engulfing` golden**, so `make test-regression` is in scope and the
  regeneration is a decision to state, not a side effect.
- Cluster the bootstrap on `utc_day_keys` per ST80, and do **not** also deflate by `n_eff` —
  those are one correction computed two ways.

## Reproduce

```bash
poetry run python tools/distil_power.py --units per_trade --n-obs 15840 --n-trials 1 \
  --sr-variance 0.0155 --n-series 3 --n-eff 1.8199 --sd 2.11 --corpus-best 1.2390
```

Swap `--n-trials 8` for the per-cell row and `--n-obs 9780` for the recovered-subset row.
