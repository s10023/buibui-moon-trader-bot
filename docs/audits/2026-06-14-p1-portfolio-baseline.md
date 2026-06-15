# P1 Paper-Portfolio Baseline — first risk-adjusted numbers (2026-06-14)

**What:** First run of the new `portfolio/` package — replays the de-biased
`signal_alert_outcomes` ledger through the Carver two-layer sizing model
(`docs/redesign/2026-06-05-p1-sizing-spec.md`) into an overlapping-position
paper book, under **policy #0** (today's exits — no time-stop / BE / partial
yet). Read-only over `analytics.db`. Command:

```bash
PYTHONPATH=. poetry run python buibui.py portfolio replay
```

**TL;DR:** The system's first historical risk-adjusted number is
**Sharpe −0.53** (fixed-notional, 196 sized trades over 82 days). Position
sizing + portfolio construction did **not** rescue the −0.12R/alert edge — you
cannot size your way out of negative expectancy. Separately, the run exposed a
**structural throttle**: 92% of resolved alerts are un-deployable under the
risk caps because the entire live universe is one correlated cluster
(BTC/ETH/SOL). This is *not* the G1 gate (which is forward-paper, ≥1.0); it is
the historical baseline that motivates the exit branch + breadth.

## Headline (fixed-notional / constant-R — the headline basis)

| Metric | Value |
| --- | --- |
| Sharpe | **−0.53** |
| Sortino | −0.60 |
| Calmar | −0.76 |
| Max drawdown | −11.1% |
| Annualized return | −8.4% |
| Annualized vol | 14.9% (target 20%) |
| Avg gross exposure | 0.84% open risk |
| Risk turnover | 0.5x |
| Final equity | 9,794 (from 10,000) |
| Trades sized / skipped | 196 / 2,359 |
| Days | 82 |

Compounding curve (the vol-governor's feedback basis): Sharpe **−0.60**, max
drawdown −10.9%, final equity 9,780. The two bases agree — there is no
sizing-feedback magic hiding in the compounding path.

Regime halving (`apply_high_vol_halving=True`, 1d regime) is **on** in this
headline run.

## The structural finding: the risk caps throttle 92% of the ledger

| Quantity | Value |
| --- | --- |
| Resolved rows feeding the book | 2,555 |
| Symbol mix | BTCUSDT 924 · ETHUSDT 854 · SOLUSDT 777 |
| Sized | 196 |
| Skipped | 2,359 — **100% `cap_breach`** (0 `zero_risk`, 0 `before_grid`) |

Every skip is a risk-cap breach. The cause is concentration: the live daemon
only trades `coins.json` (BTC/ETH/SOL), which are **all one cluster** under the
spec's majors definition. With `r_cluster_max = 1%` and `r_base = 0.25%`, at
most ~4 concurrent positions fit across the three majors combined — so the
densely-overlapping alert stream is refused the moment the cluster is full.

Sensitivity — drop the cluster cap entirely (each symbol its own cluster, only
the 2% concurrent-total cap binds):

| Config | Sized | Skipped |
| --- | --- | --- |
| Default (1% majors cluster) | 196 | 2,359 |
| No cluster cap (2% total only) | 602 | 1,953 |

Even with no correlation cap, the 2% concurrent-total cap still skips 1,953 —
the alert stream is simply too dense to deploy at a sane per-trade risk. The
governor is doing exactly its job (refusing to over-concentrate correlated
risk); the binding constraint is **the caps, not the vol target**. Realized vol
(14.9%) sits *below* the 20% target because the caps bind before the governor
can lever up. Raising the vol target to 30% makes it **worse**, not better —
bigger per-trade risk fills the cap sooner:

| Vol target | Sized | Sharpe | Ann. vol |
| --- | --- | --- | --- |
| 20% (default) | 196 | −0.53 | 14.9% |
| 30% | 160 | −0.55 | 15.4% |

## Attribution (fixed basis, by strategy × tf × direction)

Top cells (of the 196 sized):

| strategy | tf | dir | n | total_r | avg_r |
| --- | --- | --- | --- | --- | --- |
| morning_evening_star | 15m | short | 9 | +14.63 | +1.63 |
| inside_bar | 15m | short | 15 | +6.54 | +0.44 |
| pin_bar | 1d | short | 3 | +5.50 | +1.83 |
| inside_bar | 1h | short | 6 | +6.36 | +1.06 |
| engulfing | 15m | short | 10 | +7.76 | +0.78 |
| trend_day | 4h | long | 5 | +3.14 | +0.63 |

Bottom cells:

| strategy | tf | dir | n | total_r | avg_r |
| --- | --- | --- | --- | --- | --- |
| orb | 1h | long | 6 | −6.12 | −1.02 |
| morning_evening_star | 4h | short | 5 | −5.00 | −1.00 |
| pin_bar | 15m | short | 16 | −6.54 | −0.41 |
| pin_bar | 1h | long | 5 | −2.50 | −0.50 |
| trend_day | 1d | short | 3 | −3.00 | −1.00 |
| morning_evening_star | 15m | long | 10 | −2.93 | −0.29 |

The attribution rhymes with the standing direction-axis evidence: the positive
cells skew **short**, the worst cells skew **long** (`orb 1h long`,
`pin_bar 1h long`, `morning_evening_star ...long`). Most negative cells are
small-n full-loss singletons (−1.00R) — noise at this sample size, but the net
is unambiguously negative.

## G1 framing

This is the system's first **historical** risk-adjusted baseline. It is **not**
the G1 gate. G1 = automation only after **≥3 months of forward paper trading at
Sharpe ≥ 1.0** (`docs/redesign/2026-06-10-fresh-eyes-second-opinion-and-roadmap.md`).

The historical baseline of **−0.53** is nowhere near 1.0, and that is the
honest, expected result: a ledger that averages −0.12R/alert cannot be rescued
by sizing or portfolio construction. What the baseline *does* establish:

1. **Sizing is not the missing edge.** The two-layer model behaves correctly
   (caps bind, governor is causal, dual bases agree) and still lands negative.
   The lever is **entry quality + exits**, not risk fractions.
2. **The current alert stream is undeployable as-is** — 92% cap-breached
   because it is one correlated cluster firing far too densely. This is direct
   quantitative support for the north-star pivot: **breadth** (the N3 25-perp
   universe, now backfilled) and a **positive-EV core**, not more single-symbol
   TA on three majors.
3. **Next lever = exits.** The N2 MFE/MAE diagnostic already returned GO
   (`docs/audits/2026-06-11-mfe-mae-diagnostic.md`): the 40%-expiry leak is
   exit-fixable. Sub-project B (exit-policy replay: #1 time-stop, #2 breakeven,
   #6 partial-at-1R) reuses this exact `PaperBook` for a joint A/B vs policy #0
   — that is where the curve can actually move.

**Recommendation:** keep building toward the G1 *forward* test; do not tune
sizing parameters against this in-sample curve (there is nothing to tune — the
caps, not the parameters, are binding). Proceed to the exit branch.

## Caveats

- **`outcome_r` cost-mix.** The ledger mixes pre-PR-3 rows (raw R) and post-PR-3
  rows (net of funding + slippage). The true cost-laden Sharpe is marginally
  worse than shown. A gated retro re-score of the pre-PR-3 rows would tighten
  this.
- **Daily-granularity marking.** Open positions are marked to the symbol's 1d
  close; intraday overlap is compressed to daily marks, so the concurrency-vol
  estimate is approximate (the spec's accepted simplification).
- **1d-regime coarsening.** The sizing regime uses the symbol's 1d regime at
  entry (a macro vol state), not the per-tf live-gate regime.
- **Short window.** 82 days — Sharpe annualization (√365) over this span is
  noisy; treat the sign and magnitude as directional, not precise.
- **Majors-only.** Every resolved row is BTC/ETH/SOL, so the singleton-cluster
  path for non-majors was not exercised here.
