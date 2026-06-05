# Direction-Axis Hard-Flip Decision Doc — F8 `suppress_directions` + bos long-suppress (2026-06-03)

**Status: STAGING ONLY. No config edits. No flips.** The F8 `suppress_directions=["long"]`
(PR #409) and the T2b/T2c bos long-suppress candidates remain in **soft mode** on a
≥2-week live-observation clock. This doc assembles the evidence base those hard flips
will be decided on and writes the "flip when" checklist — it does **not** authorise a
flip. The soft clock has not elapsed and the OOS read below is a hard blocker.

## Question

Now that the `signal_alert_outcomes` ledger is de-biased (PR #410 closed the 89%
NULL-`tp_price` hole; 2,410/2,414 resolved, `open_no_tp = 0`), does the live "shorts win
/ longs lose" asymmetry — first seen on `bos` — hold **broadly across all strategies**,
and is it structural enough to justify hard-flipping long-side suppression?

## Two evidence sources (they disagree, and that disagreement is the finding)

| Source | Population | What it measures |
| --- | --- | --- |
| **LIVE ledger** `signal_alert_outcomes` | 2,410 resolved live alerts, 2026-03-25 → 2026-06-03 (~10 weeks), BTC/ETH/SOL | What the daemon actually fired and how it resolved on forward OHLCV. Single realised market regime. |
| **F8 replay** `tools/htf_ema_gate_replay.py --oos-frac 0.3` | 842K permissive-baseline `backtest_trades` | Counter-trend signals F8 *would* suppress, with a 70/30 IS/OOS split. Multi-regime backtest history. |

## Finding 1 — the live asymmetry is real, broad, and consistent

Live ledger, resolved rows, aggregate by direction:

| direction | n | avg_r |
| --- | --- | --- |
| long | 732 | **−0.418** |
| short | 1,678 | **+0.015** |

**Spread = +0.433R in favour of short.** It holds across **all three symbols**
(not a one-coin artifact):

| symbol | long avg_r | short avg_r | short−long |
| --- | --- | --- | --- |
| BTCUSDT | −0.414 | −0.112 | +0.302 |
| ETHUSDT | −0.267 | −0.008 | +0.259 |
| SOLUSDT | −0.612 | +0.180 | +0.792 |

And it is the dominant axis per strategy where both directions clear n≥10 (15m/1h cells):
`bos 1h` long −0.495 / short **+1.460** (69.6% win, n=40); `wick_fill 15m` long −0.906 /
short +0.185; `inside_bar 15m` long −0.706 / short +0.002; `pin_bar 15m` long −0.711 /
short +0.057; `morning_evening_star 15m` long −0.488 / short +0.169; `hammer 15m` long
−0.600 / short −0.124. **Longs are near-universally negative live** (most long cells are
0% win-rate); shorts are frequently positive or near-zero.

This **confirms** the `project_bos_routing_audit` thesis (short +0.14 vs long −0.27) live,
and validates the *direction* of F8 #409 and the bos long-suppress bets.

## Finding 2 — the edge is concentrated at 15m/1h and **reverses at 4h**

Live ledger, by tf × direction:

| tf | long avg_r | short avg_r | note |
| --- | --- | --- | --- |
| 15m | −0.509 | **+0.073** | short edge strongest |
| 1h | −0.363 | +0.009 | short edge holds |
| 4h | **−0.159** | −0.354 | **LONG better** — asymmetry inverts |
| 1d | −0.377 | −0.278 | both negative, small n (61) |

At **4h the sign flips**: longs lose less than shorts. A global `suppress_directions=["long"]`
is therefore mis-specified at the 4h anchor. Any eventual hard flip must be **per-TF**, not
global — suppress longs at 15m/1h, but **not** at 4h.

## Finding 3 — the OOS backtest read does NOT generalise (the blocker)

`htf_ema_gate_replay --oos-frac 0.3`, short-side IS vs OOS by strategy family:

| family | IS short_r | OOS short_r | verdict |
| --- | --- | --- | --- |
| candlestick | +0.721 | +0.067 | RELAX |
| price_action | +0.154 | +0.034 | RELAX |
| fib | −0.061 | −0.196 | KEEP |
| flow | +0.892 | −0.428 | KEEP |
| session | +0.612 | −0.024 | KEEP |
| structural | +0.119 | −0.092 | KEEP |
| trend | +0.979 | −0.421 | KEEP |

Only **2 of 7 families** (candlestick, price_action) keep a positive short-side edge
out-of-sample. The other 5 collapse — `flow` and `trend` invert hard (+0.89 → −0.43,
+0.98 → −0.42). The big in-sample short-win numbers are **regime-fitted**, not structural.

## Reconciliation — the asymmetry is regime-contingent, not permanent

The live ledger covers a **single ~10-week realised regime** (late-Mar → early-Jun 2026)
in which longs lost across the board. That a 10-week, 3-symbol, 2,410-trade window all
points one way is *consistent* with a persistent directional regime — but the OOS
backtest split is precisely the held-out test that says this short-bias does **not**
survive a regime change for 5/7 families. **Convergent read: F8 `["long"]` is a correct
bet on the current regime, but a wrong bet as a permanent structural rule.** This is
exactly why #409 shipped soft. Hard-flipping to permanent long-suppression would overfit
the realised window.

## Recommendation

1. **Keep F8 `suppress_directions` soft.** Do not promote `["long"]` to a hard gate
   globally.
2. **bos long-suppress (T2b/T2c) is the strongest hard-flip candidate** — it is supported
   on *both* axes (live ledger `bos` short ≫ long across 15m/1h; bos routing audit; CR).
   If anything flips first, it is bos at 15m/1h short-only, **not** a global rule.
3. **Any hard flip is per-TF** (15m/1h only) and **per-family** — candlestick +
   price_action are the only OOS-survivable short-relax families; never 4h; never `flow`
   or `trend`.

## "Flip when" checklist (all must hold before promoting any cell soft → hard)

- [ ] **Soft clock elapsed** — ≥2 weeks of live observation since the soft ship date of the
      specific gate (F8 #409 / bos T2b/T2c), measured on `fired_at_ms`.
- [ ] **Live sign stable** — re-run `tools/live_outcomes_report.py --days 14 --min-n 10`;
      the cell's short−long spread is still ≥ +0.10R and same sign as the all-time read.
- [ ] **OOS confirms** — the cell's family reads RELAX (OOS short_r > 0) in
      `htf_ema_gate_replay --oos-frac 0.3`. Currently only candlestick + price_action pass.
- [ ] **TF-scoped** — the flip is at 15m and/or 1h only. 4h is excluded (live sign inverts);
      1d excluded (n < 30 per direction).
- [ ] **Regime unchanged** — no macro regime flip (e.g. sustained bull leg) since the soft
      ship; if the realised regime has turned, restart the clock — the live edge is
      regime-contingent (Finding 3).
- [ ] **CR agreement** — `confidence_ratings` for the cell agrees with the ledger sign
      (dual-view protocol, per the ADR/Bucket-C audits).

## How to re-derive

```bash
# live ledger direction read (all-time and windowed)
PYTHONPATH=. poetry run python tools/live_outcomes_report.py --days 0  --min-n 10
PYTHONPATH=. poetry run python tools/live_outcomes_report.py --days 14 --min-n 10
# OR the never-cached API: GET /api/live-outcomes?days=14&min_n=10
# OOS short-side split over the permissive baseline
PYTHONPATH=. poetry run python tools/htf_ema_gate_replay.py --oos-frac 0.3
```

Aggregate long/short and tf×direction tables above are from direct `signal_alert_outcomes`
queries (`avg(outcome_r)` over `outcome IS NOT NULL`).

## Cross-references

- `project_outcome_ledger_tp_hole` — the de-bias that made this ledger trustworthy (#410).
- `project_bos_routing_audit` — the original direction-is-dominant-axis finding for bos.
- `project_f8_implementation` / PR #409 — F8 `suppress_directions` soft ship.
- `docs/superpowers/specs/2026-06-01-asymmetric-f8-htf-ema-gate-design.md` — F8 spec.
