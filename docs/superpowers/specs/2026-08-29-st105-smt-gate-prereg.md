# ST105 — cross-symbol SMT divergence gate (pre-registration)

**Date:** 2026-08-29 · **Status:** PRE-REGISTERED, deliberately NOT scheduled · **SoT:** ST105
**Owner surface:** none yet — a read-only study over the existing backtest book

## Goal and success metric

Decide whether an SMT-divergence gate (BTC vs ETH) adds measurable edge to the existing
signal book. Success is a named verdict on ONE pre-registered construction; the honest prior
is stated up front and is why this spec sits unscheduled.

## The prior, stated before any data is touched

Conditioning axes are **6-for-6-plus-one-amended no-edge** in this repo, and the standing
diagnosis has moved off conditioning and onto the signal book. SMT is genuinely new
cross-symbol information — but so were H14 (Coinbase premium) and H15 (USD/JPY carry), and
both found nothing. This spec exists so that when the queue is empty or the operator promotes
it, the study runs pre-registered rather than being re-derived hot. Running it now is not
recommended.

## What was measured (2026-08-27, the reason the row exists)

SMT verified in both directions on consecutive days: bearish BTC HH +3.20% / ETH LH −0.62%
(25 Aug); bullish BTC LL −0.27% / ETH HL +0.76% (26 Aug). ⚠ **The pivot rule is load-bearing:**
measured from a 24-Aug window instead of the operator's 22-Aug pivot, the clean LH degrades to
an equal high — the signal is an artifact of pivot choice unless the rule is pinned in code.

## The pinned pivot rule (the design content of this spec)

- Pivots are **confirmed swings only**: a swing high/low at bar `i` with `swing_n=5` bars
  each side exists only from bar `i+5` onward. The comparison at decision time uses the most
  recent PRIOR confirmed pivot pair on each symbol — never a `center=True` window (the `bos`
  leak class, 100% non-causal, is exactly this mistake).
- Window: the most recent confirmed pivot within 200 bars (`scan_window`), same timeframe as
  the gated signal. No pivot on either symbol inside the window ⇒ gate state `no-reading`,
  excluded from both buckets.
- Gate definition (one construction, one trial): for a SHORT signal, SMT-agree ⇔ one of
  BTC/ETH printed a higher high while the other printed a lower high across the latest
  confirmed pivot pair; for a LONG, lower low vs higher low. Everything else is disagree.

## Measurement

- Split the existing live-parity backtest book (all detectors) into agree / disagree /
  no-reading. Difference CI between agree and disagree buckets on per-trade R with
  `cluster_key=utc_day_keys` through `AuditCell` — or on book-day rows with no further
  deflation, never both (ST80).
- Verdict on the difference via the gate legs only if the screen below passes; any negative
  claim goes through `analytics.audit_guard.powered_null` (CI containment), not a restated
  floor.

## Power screen (before scoring anything)

Count coverage first: if SMT-agree covers **<20% of trades** in the primary cell (15m short),
the difference CI cannot reach the bar at any plausible effect — declare UNREACHABLE and stop.
Price the reachable case with `distil_power` at the actual bucket n (1 trial), passing `--sd`
and `--corpus-best` together.

## Decision Log

- **D1 (artifact check, runs first):** recompute gate labels at `swing_n=3` and `swing_n=7`.
  If agree/disagree flips on >20% of signals, the gate is an artifact of pivot choice —
  verdict ARTIFACT, never proceed to the gate legs.
- **D2:** coverage <20% in the primary cell → UNREACHABLE, filed and closed.
- **D3:** the prior reverses only on a measured positive that clears all three gate legs at
  the pre-registered construction — a nominal split with a wide CI re-litigates nothing.
