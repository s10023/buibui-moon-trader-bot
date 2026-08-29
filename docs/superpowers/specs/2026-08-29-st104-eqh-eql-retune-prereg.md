# ST104 — `detect_eqh_eql` retune pre-registration (three knobs, three trials)

**Date:** 2026-08-29 · **Status:** PRE-REGISTERED, unexecuted · **SoT:** ST104
**Owner surface:** `analytics/strategies/eqh_eql.py` + the backtest sweep path

## Goal and success metric

Test whether tightening `detect_eqh_eql` to the precision the operator's framework measurably
trades at produces gate-clearing cells. Success is that **each pre-registered arm returns a
named verdict** — pass, fail, INSUFFICIENT, or UNREACHABLE — not that any arm passes.
"Unreachable, do not build" is a successful output.

## Why this is exempt from "tuning existing detectors is dead"

The operator ruling of 2026-08-27 binds: the precision the framework reproduces at **is the
standard for an edge** (channel top to 0.0056%, PWH swept to the cent, EQL cluster spanning
0.064%). A detector whose tolerance admits levels 4.7× looser is not measuring the same
object — this is a **dimensional mismatch**, the same exemption class as the flat-2% SL
defect, not a knob search. The three axes were measured against real trades, not scanned for.

## Baseline (measured 2026-08-29, deduped on the 5-tuple)

Distinct `(symbol, timeframe, strategy, direction, entry_time)` rows in `backtest_trades`,
strategy `eqh_eql` (15,186 total — raw rows carry the ~5.29× duplication factor):

| cell | n | avg_r | sd |
| --- | --- | --- | --- |
| 15m long | 6,988 | −0.148 | 1.414 |
| 15m short | 7,648 | −0.122 | 1.424 |
| 1h long | 1,424 | +0.298 | 1.808 |
| 1h short | 1,554 | +0.332 | 1.915 |
| 4h long | 127 | −0.272 | 1.332 |
| 4h short | 159 | −0.032 | 1.432 |
| 1d short | 17 | +0.869 | 1.537 |

⚠ These pool 25 symbols per cell — any per-cell significance claim needs the day-cluster
correction below. The 1h cells being positive at n≈1.5K each is the strongest baseline fact;
the 15m cells (the live book's dominant timeframe) are negative.

## The three arms — exact values, derived not searched

Defaults: `lookback=50`, `tolerance_pct=0.003`, `swing_n=5` (`eqh_eql.py:12`).

- **T1 `tolerance_pct` 0.003 → 0.00075.** Derivation: measured cluster spans are 0.064%
  (BTC EQL) and 0.04% (ETH poor lows); 0.075% is the observed ceiling plus margin. 4× tighter.
- **T2 `lookback` 50 → 400.** Derivation: the levels actually traded sat 65 / 125 / 190 /
  ≥387 bars back; 400 covers the observed maximum (≈4.2 days on 15m against today's 12.5h).
- **T3 `swing_n` 5 → 2.** Derivation: a 4-touch shelf has no two spaced pivots at n=5 (the
  ETH poor lows were inside both other bounds and still silent); n=2 is the smallest
  confirmation window that lets adjacent-touch structure qualify. ⚠ A true shelf detector
  (adjacent touches folded into one level) is a NEW construction and is out of scope — T3 is
  the closest scalar, and the result doc must say which of the two it evidences.

**Exactly three trials, each knob alone against the default baseline. No joint arm.** A joint
retune is a follow-up pre-registration permitted only if ≥2 single arms clear their screens,
and it counts as a new trial in a new family.

## Preconditions

- **P1 — run_id namespacing (blocking).** Verify the three knobs enter `_backtest_run_id`
  before any saved run. ST86: an engine/TOML axis absent from the run_id silently overwrites
  every prior row measured under the old value, and the collision is unmeasurable afterwards.
  If absent, adding them joins the sweep in the SAME PR, and the read path follows the write
  path.
- **P2 — row selection by run_id.** Score only the rows the sweep's own run_ids wrote. This
  sidesteps the 5.29× duplication dedup entirely, by construction.
- **P3 — causality gate.** `tests/test_lookahead.py` must pass per arm. Parameter changes
  should not introduce lookahead, but the check is cheap and the `bos` precedent (100%
  non-causal, +0.35R/trade) says run it anyway.

## Measurement and gate

- **Primary endpoint: the 15m short cell** — largest n, the live book is 64.4% 15m, and
  direction (short) is the one OOS-robust conditioning axis. Every other cell is reported as
  exploratory.
- Per-cell scoring through `AuditCell` with `cluster_key=utc_day_keys` (fails closed). Apply
  day-clustering **or** `effective_independent_series`, never both — they are one correction
  with two estimators (ST80).
- Verdict via `analytics.research_guards.passes_gate` (DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0),
  trial family n=3. Any negative claim goes through `analytics.audit_guard.powered_null`
  (CI containment) — never a restated sample-size floor, MDE, p-value, or failure-to-clear;
  INSUFFICIENT vs NO-EDGE is that function's answer, not the runner's.
- **Tie-break exposure check (ST56/ST57).** Count ambiguous same-bar SL/TP rows per arm.
  Adverse-first resolution penalises the arm with more exposure; the result doc names the
  bias DIRECTION before any cross-arm number is compared. A tighter arm reading WORSE is
  conservative; a tighter arm reading BETTER at 1h/4h is in the bias's favoured direction and
  needs intrabar resolution before belief.

## Power — priced 2026-08-29 (`distil_power`, 3 trials, sd 1.42, sr_variance 0.0181 plug)

| n_obs | required effect / trade |
| --- | --- |
| 250 | +0.3127 |
| 500 | +0.2684 |
| 1,000 | +0.2373 |
| 2,000 | +0.2155 |
| 4,000 | +0.2000 |

The 1h baseline (+0.30/+0.33) already sits at the n=250 bar, so the study is powered **if the
retuned arms keep n in the high hundreds**. Two declared caveats: the `sr_variance` 0.0181 is
a plug from the recalibrate long-scope family and must be recomputed from the actual 3-trial
family at scoring time; and the bar must be read at **effective** n — day-clustering
concentrates in 15m (trade-weighted DEFF 4.991), so 15m n≈7,000 is effective n≈1,400.

**Fire-count screen (cheap, first):** generate signals per arm before any scoring. Any cell
whose retuned n < 500 is declared UNREACHABLE and not scored — T1 (4× tighter) is expected to
cut fires hard; T2 and T3 to raise them.

## Decision Log — observables that stop or reverse this spec

- **D1:** the knobs cannot join `_backtest_run_id` cheaply → STOP and file the blocker. Never
  run unnamespaced.
- **D2:** all three arms return n < 500 in the primary cell → UNREACHABLE verdict, filed as a
  result: the operator's precision may be undetectable at 15m bar granularity.
- **D3:** a positive verdict in an arm with systematically WIDER stops at 1h/4h lands in the
  tie-break bias's favoured direction (ST57) and is unproven without intrabar resolution; a
  null there needs no discount.
- **D4:** premise check — the retuned detector must reproduce the journal's measured 25–26 Aug
  levels (BTC EQL ≈77,800 cluster, ETH poor lows) on the same data. If it does not, the
  derivation is wrong: fix the derivation and re-register rather than adjusting knobs to fit.

## Execution

A sonnet subagent executes against this doc as the brief. The result is an audit in
`docs/audits/` with its verdict as prose under a Verdict heading; an actionable verdict needs
a SoT row naming the audit's filename. `make docs-index` after filing.
