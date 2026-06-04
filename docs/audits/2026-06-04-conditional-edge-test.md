# Conditional-Edge Test — does "location/context" rescue the losing strategies? (2026-06-04)

**Status: ANALYSIS ONLY. No code, config, or DB change.** This doc resolves the strategic
fork left open by `project_profitability_regroup` (2026-06-03): is the user's
counter-thesis — *"bad-on-average strategies perform well when triggered at the right
time/place/trigger, so restructure trades around location (range extreme / NPOC / EMA
line / Bollinger Bands)"* — supported by the data we already have, **before** committing
months to net-new location primitives.

## The question, made falsifiable

> Do the losing strategies turn **positive** at contexts we already track
> (timeframe × regime × session), with **n ≥ 30 AND out-of-sample robustness** —
> or is "the right place" a mirage?

YES (specific cells) → a location-first rebuild is justified *for those cells*, and the
audit names which context to build first. NO → the keep-20-and-hope premise is falsified
→ prune to the positive core.

## Two data sources

| Source | Population | Nature |
| --- | --- | --- |
| **Edge audit** `tools/strategy_edge_audit.py` | 843,424 permissive-baseline `backtest_trades`, aggregated strategy × tf × regime × session | In-sample, ungated (fires *everything*), multi-regime backtest history |
| **Live ledger** `tools/live_outcomes_report.py --days 0 --min-n 10` | `signal_alert_outcomes`, 2,410 resolved live alerts, BTC/ETH/SOL, 2026-03-25 → 06-04 | Out-of-sample, post-gate (what the daemon actually fired), single realised regime |

The audit answers "where is there in-sample edge if you fire everywhere?" The live ledger
is the OOS reality check. Where they disagree, **the live ledger wins** — and the
disagreement *is* the finding.

## Finding 1 — the audit's KEEP verdict is an artifact; fire-weighted edge is mostly negative

The tool's built-in verdict returned **KEEP for 18/19 strategies** (only `wick_fill`
DEMOTE). This is misleading by construction: the rule assigns KEEP on ≥3 *positive
slices* with **no n-floor on those slices and no fire-weighting** — so a strategy that is
negative on 95% of its fires but has three tiny positive corners reads KEEP. That is
precisely the overfitting trap the regroup warned about.

Re-aggregating with an n ≥ 30 floor and weighting each cell by its share of fires (the
honest per-strategy expectancy proxy) collapses the story:

| strategy | total fires | fw avg_r (all cells) | fw avg_r (n≥30 cells) | % of fires in robust-**positive** cells | best robust-positive cell |
| --- | ---: | ---: | ---: | ---: | --- |
| liquidity_sweep | 45,648 | −0.265 | −0.266 | **3.5%** | 1h/unknown/asia +0.682 (n=82) |
| marubozu | 3,640 | −0.226 | −0.227 | 7.1% | 1h/range/london +0.878 (n=101) |
| fvg | 31,051 | −0.171 | −0.171 | **5.8%** | 1h/high_vol/off +0.209 (n=176) |
| wick_fill | 125,488 | −0.159 | −0.159 | **1.2%** | 1d/unknown/asia +0.394 (n=85) |
| order_block | 14,951 | −0.148 | −0.147 | 17.8% | 15m/trend/asia +0.671 (n=185) |
| eqh_eql | 57,053 | −0.130 | −0.131 | 8.5% | 4h/high_vol/ny +1.760 (n=31) |
| bos | 79,508 | −0.073 | −0.073 | 18.4% | 4h/range/ny +0.522 (n=202) |
| fib_golden_zone | 12,997 | −0.010 | −0.012 | 34.0% | 1h/high_vol/london +2.151 (n=48) |
| pin_bar | 73,683 | +0.068 | +0.069 | 72.4% | 4h/high_vol/london +1.417 (n=160) |
| smt_divergence | 10,012 | +0.122 | +0.121 | 80.5% | 1h/range/london +0.578 (n=119) |
| doji | 20,902 | +0.157 | +0.155 | 85.3% | 1h/range/off +1.423 (n=37) |

The killer column is **% of fires in robust-positive cells**. For the canonical imbalance
losers, the positive corners exist but capture almost none of the actual fires —
**fvg 5.8%, wick_fill 1.2%, liquidity_sweep 3.5%**. You cannot "route to the good
location" because the strategy almost never fires there. Discarding 82–99% of a
strategy's fires to harvest a thin in-sample corner is the definition of curve-fitting.

## Finding 2 — the contexts we already track carry no edge; only timeframe shows a gradient

Pooled fire-weighted edge across all strategies, by axis:

| regime | fw avg_r | fires | | session | fw avg_r | fires | | timeframe | fw avg_r | fires |
| --- | ---: | ---: | --- | --- | ---: | ---: | --- | --- | ---: | ---: |
| range | −0.020 | 515,461 | | off | +0.001 | 57,490 | | 15m | −0.065 | 629,507 |
| high_vol | −0.050 | 186,910 | | london | −0.022 | 182,084 | | 1h | −0.004 | 167,609 |
| trend | −0.120 | 119,341 | | ny | −0.053 | 308,605 | | 4h | **+0.043** | 42,243 |
| unknown | −0.209 | 21,712 | | asia | −0.062 | 295,245 | | 1d | **+0.259** | 4,065 |

- **Regime: every value negative.** There is no "high-value regime" to do business in.
- **Session: every value flat-to-negative** (best is "off" at +0.001).
- **Timeframe is the only axis with a real gradient** (15m → 1d monotonic up), but fires
  collapse 155× from 15m to 1d. HTF edge is real-but-rare, and it is a *grain/frequency*
  lever — not a "location" primitive.

**Cluster check** — where does each strategy's single best robust cell live?
regime: range 6 / high_vol 5 / unknown 5 / trend 3 · session: london 8 / asia 6 / off 3 /
ny 2 · timeframe: 1h 8 / 4h 5 / 1d 4 / 15m 2. The best cells are **smeared across every
context**, not clustered. If "location = edge" were real you would see a tight cluster
(e.g. everything wins in range/london). Smear is the signature of noise-mining.

## Finding 3 — in-sample positives do not survive out-of-sample (the F8 pattern repeats)

The strategies that looked fire-weighted-positive in-sample are the candlestick patterns.
Every one of them is **negative in the live OOS ledger**:

| strategy | fw avg_r in-sample (audit) | live rolled-up avg_r (OOS) |
| --- | ---: | ---: |
| doji | +0.157 | **−0.089** |
| smt_divergence | +0.121 | +0.256 ✓ (small n=21) |
| engulfing | +0.114 | **−0.127** |
| pin_bar | +0.068 | **−0.139** |
| morning_evening_star | +0.058 | **−0.002** |
| inside_bar | +0.043 | **−0.151** |

This is the exact decay seen with F8 (in-sample 6/7 families RELAX → OOS 2/7;
`project_direction_axis_hard_flip`). In-sample positive cells are a **ceiling, not a
forecast.** Even Finding 2's one "real" axis — the HTF gradient — does not clearly hold
live: live 4h cells are mostly negative (inside_bar 4h short −0.415, order_block 4h long
−0.727, pin_bar 4h short −0.681, trend_day 4h −0.35/−0.10), though live 4h/1d n is thin.

## Finding 4 — the ONE conditional axis that survives OOS is DIRECTION, which we already track

The live per-(strategy, tf, direction) table shows short ≫ long pervasively — the same
axis the `project_bos_routing_audit` memory flagged, now confirmed across the book:

| cell | long avg_r | short avg_r |
| --- | ---: | ---: |
| bos 1h | −0.495 | **+1.460** (69.6% win, n=40) |
| bos 15m | −0.346 | +0.310 |
| wick_fill 15m | −0.906 | +0.185 |
| pin_bar 15m | −0.711 | +0.086 |
| inside_bar 15m | −0.706 | +0.040 |
| morning_evening_star 15m | −0.488 | +0.247 |
| doji 15m | −0.186 | +0.358 |

The conditional edge the user intuited is **real — but the conditioning variable is
direction, not exotic location.** We already track it and already act on it in soft mode
(F8 `suppress_directions=["long"]` PR #409; bos T2b/T2c). No new primitive is needed to
capture it; it is gated by the ≥2-week soft clock, not by a missing detector.

## Finding 5 — the true imbalance losers don't recover even on the direction split

`fvg` is negative **both** directions live (15m long −0.705 / short −0.783; 1h long
−0.034). `order_block` 4h long −0.727; live rolled-up −0.374. These are not
"right-place-wrong-time" strategies — they are structurally negative. This sharpens the
regroup's rule: **liquidity locations carry edge, imbalance (FVG/OB) locations do not** —
and the audit confirms it is not a context-routing problem.

## Verdict

**The answer is NO.** The losing strategies do **not** turn reliably positive at contexts
we already track:

- Regime and session carry no pooled edge (every value ≤ 0).
- Where in-sample positive cells exist for the losers, they capture 1–18% of fires and
  the strategy stays fire-weighted-negative.
- In-sample positives decay OOS (candlestick patterns; the F8 precedent).
- Best cells are scattered, not clustered → noise, not structure.
- The one conditional axis that *does* survive OOS is **direction (short)**, already
  tracked and already in soft-mode action — not a new location concept.

→ **Fork (A) "location-first rebuild" (NPOC / BB / VWAP / value-area) is not justified by
the evidence.** These primitives don't exist in the codebase (months of net-new work) and
the cheap test says the contexts we *do* have don't rescue the losers. Building more
context after already gating heavily (regime/F8/ADR/session/volume) to still net −0.12R is
a tested-negative bet.

→ **Fork (B) — prune-to-positive-EV core + the direction overlay + Carver's sizing/risk +
exits — is the supported path.** It does not depend on (A) and is where the missing profit
multiplier lives.

## What this means concretely (next moves, in leverage order)

1. **Prune / down-weight to the positive core** (frequency discipline; the
   `low-alert-bar-intentional` rationale has expired). Live OOS net-positive at min_n=10:
   `liquidity_sweep +0.875`, `fib_golden_zone +0.629`, `smt_divergence +0.256`,
   `eqh_eql +0.193`, `bos +0.189` (cvd +0.130 marginal). The other ~11 bleed.
2. **Make the direction overlay the headline conditional**, not a new location detector —
   it is the only OOS-robust "right place." Continue the soft clock on F8/bos hard-flips
   per `project_direction_axis_hard_flip`; do not flip early.
3. **Sizing / risk / exits (Carver)** — necessary-not-sufficient: the best entry still
   bleeds flat-sized into a 40% expiry rate. This is the largest untouched multiplier and
   proceeds regardless of (A)/(B).
4. **Do NOT** start NPOC/BB/volume-profile work on this evidence. Re-open only if a future
   live regime shift moves the direction axis or a positive core cell decays.

## Caveats

- The audit is **in-sample** (`tools/strategy_edge_audit.py` does no IS/OOS split). Its
  positive cells are an optimistic ceiling; Finding 3 shows they decay live. Treat every
  audit positive as unconfirmed until it appears in the live ledger.
- The two populations are **not directly comparable** (live = post-gate subset; audit =
  ungated everything). They are used as ceiling (audit) vs reality (live), not equated.
  This is why live `liquidity_sweep` is +0.875 (gated to enabled F9 cells) while the
  ungated audit fire-weights it −0.265.
- Live samples are one realised market regime (~10 weeks). Direction dominance and the
  imbalance-loser finding are robust across all three symbols; thin per-cell 4h/1d reads
  (n<30) are indicative only.
- This is analysis, not a flip. F8 #409 + bos T2b/T2c remain soft on the ≥2-week clock.
