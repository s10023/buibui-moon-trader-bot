# P0a-3 — Acceptance proof: the wired guards re-flag the known OOS-decay cells

**Date:** 2026-06-09 · **Status:** validation complete (PASS) · **Closes:**
`docs/redesign/2026-06-05-p0-research-guardrails-spec.md` §6 step P0a-3 ·
**Consumers under test:** sweep gate (`analytics/sweep_guard.py`, PR #422), audit
tools (`analytics/audit_guard.py`, PR #424), recalibrate DSR
(`recalibrate_lib.compute_dsr_ratings`, PR #425).

## 0. What this validates

Spec §0 sets the acceptance target:

> P0 should *independently reproduce* the OOS-decay verdicts the live ledger
> already caught — i.e. the in-sample positives that decayed should show **low
> Deflated Sharpe / high PBO**. If the guard flags the cells the ledger already
> flagged, it works.

The live ledger's de-biased verdict is **−0.12R/alert, only ~5/20 strategies
positive** ([[project_profitability_regroup]]); the conditional-edge test found
imbalance (FVG/OB) loses and only DIRECTION carries OOS edge
([[project_conditional_edge_test]]). The acceptance question: do the three wired
guards, run blind over the *current* configs, independently flag those same cells?

**Verdict: PASS.** Every consumer re-flags the known-decay book. No
in-sample-attractive cell the ledger caught survives the de-biased statistics.

One honest nuance: the binding constraints in practice are **DSR (deflation)** and
**MinTRL (track-record length)**, not PBO. With short tp_r grids (9 trials) PBO
stays low (0.04–0.36, i.e. *passes*); the cells fail because their Sharpe does not
survive deflation against the search and they lack the observations to make a
significant Sharpe claim. That is the correct behaviour — the search here is thin,
so the deflation/track-record tests do the work, exactly as spec §5 anticipates.

## 1. Method (all read-only)

Run against the live `analytics.db` (153 MB, 853k `backtest_trades`); no writes.
Per-config scoping matches the live TOMLs:

| config | day_filter | adr_suppress_threshold |
| --- | --- | --- |
| `signal_watch` | `tue_thu` | 0.75 |
| `signal_watch_all` | `weekend` | 0.70 |
| `signal_watch_weekdays` | `mon_fri` | 0.65 |

Reproduce with:

```text
# Consumer 1 — recalibrate DSR (invokes compute_dsr_ratings per config)
poetry run python /tmp/p0a3_recal.py          # script body in §2

# Consumer 2 — sweep gate (the COMMIT-GATE stamp)
poetry run python buibui.py param-sweep --strategy liquidity_sweep \
  --symbol BTCUSDT --timeframe 1h --param tp_r=1.0:5.0:0.5 \
  --day-filter tue_thu --since 2025-09-12 --atr-sl-floor --atr-sl-multiplier 2.5
poetry run python buibui.py param-sweep --strategy engulfing \
  --symbol BTCUSDT --timeframe 1h --param tp_r=1.0:5.0:0.5 \
  --day-filter tue_thu --since 2025-09-12

# Consumer 3 — audit tools (bootstrap CI + Holm haircut)
poetry run python tools/adr_threshold_audit.py --config config/signal_watch.toml \
  --candidates 0.60,0.65,0.70
poetry run python tools/gate_audit.py volume-suppress --config config/signal_watch.toml
```

## 2. Consumer 1 — recalibrate DSR (book-wide)

`compute_dsr_ratings` pools `backtest_trades.pnl_r` over the same latest-run set the
star ratings use, computes per-trade Sharpe, and deflates it against the per-pass
cell family. Threshold for "clean" = DSR ≥ 0.95 (the commit-gate's
`sweep_guard.DSR_THRESHOLD`).

| config | cells w/ DSR (n≥30) | min | median | max | **count DSR ≥ 0.95** |
| --- | --- | --- | --- | --- | --- |
| `signal_watch` | 44 | 0.000 | 0.000 | 0.484 | **0** |
| `signal_watch_all` | 43 | 0.000 | 0.000 | 0.155 | **0** |
| `signal_watch_weekdays` | 36 | 0.000 | 0.000 | 0.748 | **0** |
| **book-wide** | **123** | — | — | — | **0** |

The single strongest cell in the entire rated book — `fib_golden_zone/4h`
(★5, avg_r **+0.969**, `mon_fri`) — deflates to **DSR = 0.748**, still short of
0.95. Not one rated cell's Sharpe survives the search correction. This is the
−0.12R weak-book verdict reproduced from a completely independent statistic.

**High-conviction suspects (★≥4 yet DSR < 0.95) — 11 across configs:**

| config | cell | stars | avg_r (IS) | DSR |
| --- | --- | --- | --- | --- |
| `signal_watch` | `engulfing/4h` | ★4 | +0.896 | 0.484 |
| `signal_watch` | `engulfing/1h` | ★4 | +0.627 | 0.001 |
| `signal_watch` | `morning_evening_star/4h` | ★4 | +0.697 | 0.002 |
| `signal_watch` | `orb/1h` | ★4 | +0.505 | 0.002 |
| `signal_watch` | `trend_day/4h` | ★4 | +0.509 | 0.006 |
| `signal_watch_weekdays` | `fib_golden_zone/4h` | ★5 | +0.969 | 0.748 |
| `signal_watch_weekdays` | `inside_bar/4h` | ★4 | +0.562 | 0.009 |

**Known OOS-decay families** (the cells the ledger / prior audits already flagged):
every one with enough trades to score lands at DSR ≈ 0.00.

| family | why known-bad | DSR range (cells w/ n≥30) |
| --- | --- | --- |
| `bos` | long-side −0.27R ([[project_bos_routing_audit]]) | 0.000–0.0001 |
| `fvg` | imbalance loser (conditional-edge) | 0.000–0.0006 |
| `order_block` | imbalance loser (conditional-edge) | 0.000–0.0009 |
| `ema` | 20/24 cells "fix detector" ([[project_ema_wfo_findings]]) | 0.0004–0.0784 |

## 3. Consumer 2 — sweep gate (cell-level commit decision)

The sweep has **two pre-existing prechecks** ahead of the new gate: (a) "no positive
IS edge" and (b) "all top configs failed OOS validation". Most known-bad cells are
refused by those alone (e.g. `liquidity_sweep/1h/tue_thu` short-circuits at best IS
−0.374R; `orb/1h` recommends nothing because every OOS row is negative). **The new
commit-gate is the last line of defense for cells that look good both IS *and* OOS
but are a statistical mirage** — precisely what the legacy "commit the best OOS row"
skill would have written to TOML.

All three cells below pass both legacy prechecks (positive IS, positive OOS, decay
≥ 0.4) → a recommendation *is* made → and are then stamped **DO-NOT-COMMIT**:

| cell (scope) | recommended | IS avg_r | OOS avg_r | decay | DSR | PBO | MinTRL | n | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `liquidity_sweep/BTC/1h` (F9 floor) | tp_r 3.5 | +0.194 | **+0.203** | 0.70 | 0.52 | 0.36 | 291 | 196 | **DO-NOT-COMMIT** (DSR<0.95; n<MinTRL) |
| `engulfing/BTC/1h` | tp_r 3.5 | +0.20 | + | — | 0.60 | 0.11 | 154 | 163 | **DO-NOT-COMMIT** (DSR<0.95) |
| `engulfing/BTC/4h` | tp_r 3.0 | +0.20 | + | — | 0.91 | 0.04 | 15 | 42 | **DO-NOT-COMMIT** (DSR<0.95) |

`engulfing/4h` is the instructive near-miss: DSR 0.91, PBO 0.04, n ≥ MinTRL — it
clears two of three checks and lands just shy of the 0.95 DSR line. The gate still
refuses. The legacy skill would have committed all three on their positive OOS row.

## 4. Consumer 3 — audit tools (gate verdicts)

Both audit tools replaced the crude ±0.05R point bar with **two gates that must both
hold**: a serial-correlation-aware bootstrap CI clearing the bar AND a Holm
multiple-testing-adjusted p < α. The §0 evidence is the verdicts that *flip* from
what the old bar would have said.

### 4a. `adr_threshold_audit` — aggregate sweep (signal_watch.toml)

| candidate | n_supp | supp_avg_r | CI | adj_p | verdict | old ±0.05R bar |
| --- | --- | --- | --- | --- | --- | --- |
| 0.60 | 1260 | +0.277 | [0.157, 0.400] | 0.0000 | DISABLE | DISABLE ✓ |
| 0.65 | 763 | +0.292 | [0.125, 0.459] | 0.0002 | DISABLE | DISABLE ✓ |
| 0.70 | 371 | **+0.246** | **[0.0016, 0.515]** | 0.0196 | **INSUFFICIENT** | DISABLE ✗ |

Candidate 0.70 suppresses **+0.246R** — 5× the bar; the crude rule would have
confidently said "DISABLE, you're throwing away +0.25R of edge." The bootstrap CI
lower bound (0.0016) grazes zero, so the de-biased verdict is **INSUFFICIENT** — no
action. Per-cell tally: 54 INSUFFICIENT / 5 DISABLE / 1 ENABLE.

### 4b. `gate_audit volume-suppress` (signal_watch.toml, grain = strategy)

| strategy | supp_avg_r | CI | adj_p | verdict | why it bites |
| --- | --- | --- | --- | --- | --- |
| `ema` | +0.342 | [-0.054, 0.791] | 0.218 | INSUFFICIENT | CI straddles bar |
| `eqh_eql` | +0.186 | [-0.125, 0.510] | 0.218 | INSUFFICIENT | CI straddles bar |
| `trend_day` | +0.497 | **[0.083, 0.928]** | 0.184 | INSUFFICIENT | **CI clears bar, Holm haircut fails** |
| `engulfing` | +0.800 | [0.516, 1.058] | 0.0000 | DISABLE | both gates hold |
| `pin_bar` | +0.175 | [0.092, 0.256] | 0.0001 | DISABLE | both gates hold |

`trend_day` is the textbook haircut case: its CI [0.083, 0.928] *excludes* the
+0.05R bar, but after Holm correction over the tested-cell family p = 0.184 > 0.05,
so the verdict is **INSUFFICIENT** — a point-estimate bar (and even a naive
single-test CI) would have DISABLEd it. Tally: 265 INSUFFICIENT / 25 DISABLE /
5 ENABLE / 4 CONCENTRATE — the guard is appropriately skeptical, acting only where
the evidence survives both gates.

## 5. Cross-reference to the live ledger

| ledger / audit finding | guard that re-flags it | evidence |
| --- | --- | --- |
| whole book ≈ −0.12R, ~5/20 positive | recalibrate DSR | 0/123 cells clear DSR 0.95 (§2) |
| in-sample positives decay OOS | sweep gate | 3 positive-OOS cells → DO-NOT-COMMIT (§3) |
| imbalance (FVG/OB) loses | recalibrate DSR | `fvg`/`order_block` DSR ≈ 0 (§2) |
| `bos` long-side negative | recalibrate DSR | `bos` DSR ≈ 0 (§2) |
| EMA 20/24 cells "fix detector" | recalibrate + sweep | `ema` DSR ≤ 0.08 (§2) |
| ADR/volume gate edges are noise | audit tools | +0.25R / +0.50R cells → INSUFFICIENT (§4) |

## 6. Side finding (not blocking)

The live `analytics.db`'s `confidence_ratings.dsr` column is currently **all NULL** —
this DB (written 2026-06-08 22:15) predates PR #425's runner wiring on `main`. The
additive column + migration guard are present, but the persisted ratings carry no
DSR yet. **To populate it in the live DB, run a fresh `buibui recalibrate`** with the
merged code (or `make db-update`). This validation therefore invoked
`compute_dsr_ratings` directly rather than reading the (empty) column, which is the
more faithful test of the consumer anyway. Recommend a recalibrate pass next time the
DB is refreshed so the Backtest UI / report path sees real DSR values.

## 7. Conclusion

All three wired guard consumers independently reproduce the known OOS-decay verdicts
the live ledger already caught. **P0a is closed** — the additive statistics layer is
shipped (P0a-1), wired with teeth into every commit/promotion path (P0a-2), and
validated against the ground-truth weak book (P0a-3, this doc).

**Remaining in P0:**

- **P0a-2 sub-PR 4** — P1-metrics bootstrap CIs. Ships *with* P1 sizing, not before.
- **P0b** — honest costs (funding + slippage). Behavioural; goldens move. Sequence
  after P0a-3 (spec §4).

See [[top-tier-redesign]] for where P0 sits in the roadmap.
