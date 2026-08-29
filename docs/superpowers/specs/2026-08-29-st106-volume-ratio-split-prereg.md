# ST106 — retrace/impulse volume ratio, split study (pre-registration)

**Date:** 2026-08-29 · **Status:** PRE-REGISTERED, unexecuted · **SoT:** ST106
**Owner surface:** none — stage 1 is read-only over existing `eqh_eql` trades

## Goal and success metric

Test the operator's stated entry tell — "bounce back into the level on **~10% of the impulse
volume** — a failure to accept" — as a predictor of outcome among existing sweep-reversal
fires. Success is a named verdict on the pre-registered split; a build decision is explicitly
NOT this study's output.

## Why a split and not a trigger

Volume enters the system today only as `volume_suppress`, a post-hoc High/Low split in the
backtest table — there is no volume-ratio primitive anywhere. A ratio
(`V_retrace ÷ V_impulse`) is deterministic and cheap from OHLCV. Testing it first as a split
over trades that already exist costs no new construction and prices whether a trigger build
is worth registering.

## Definitions — pinned before looking

On each `eqh_eql` fire (the sweep-reversal detector, the closest structural match to the
operator's framework):

- **V_impulse** = volume of the signal bar (the bar that wicks through the level).
- **V_retrace** = volume of the bar immediately AFTER the signal bar.
- **ratio** = V_retrace / V_impulse.

⚠ **Declared honestly: the ratio is one bar of lookahead relative to the logged entry** (the
detector enters at the signal bar's close; the retrace bar is not yet known). This split is
therefore a hypothesis screen, NOT a tradeable gate. If it predicts, the build that follows is
a **one-bar-delayed entry variant** — a NEW construction owing the full three-leg gate under
its own pre-registration, with the delay's cost measured, not assumed.

## The pre-registered test — one trial

- **Primary:** binary split at ratio ≤ 0.10 (the operator's stated tell, pinned — no
  threshold sweep) vs > 0.10, on deduped distinct `eqh_eql` trades, difference in `outcome_r`
  with `cluster_key=utc_day_keys` through `AuditCell`.
- **Secondary (exploratory, reported not gated):** Spearman rank of ratio vs `outcome_r`.
- Any negative claim goes through `analytics.audit_guard.powered_null` (CI containment) —
  never a restated sample-size floor or p-value.

## Power screen (first, before scoring)

Count the ratio ≤ 0.10 bucket. If it holds **<100 trades** in the primary cell (15m short,
matching ST104's primary), declare UNREACHABLE and stop. Otherwise price the split with
`distil_power` at the actual bucket n (1 trial), passing `--sd` and `--corpus-best` together.

## Decision Log

- **D1:** ratio ≤ 0.10 bucket <100 trades → UNREACHABLE, filed and closed until the ledger
  grows.
- **D2:** a predictive split does NOT license wiring anything — the only next step is the
  delayed-entry pre-registration described above. Wiring the split into sizing or gating
  directly would repeat the ensemble-scoring path this repo already closed (DSR 0.703).
- **D3:** if the signal-bar/retrace-bar definitions prove ambiguous on real fires (e.g. the
  detector's entry bar is not the sweep bar on inspection), stop and re-register with
  corrected definitions rather than adapting them mid-run.
