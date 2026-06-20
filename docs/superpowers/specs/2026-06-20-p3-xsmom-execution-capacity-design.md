# P3 — XS-momentum execution-realism capacity stress test

**Date:** 2026-06-20
**Status:** design (pre-implementation)
**Sub-project:** "harden XS-solo toward deployment" #1 of 4 (the cheapest kill-test)

## Goal + success metric

**Goal.** Decide, trustworthily and cheaply, whether the gate-clearing XS
cross-sectional-momentum sleeve (universe Sharpe **+1.375** under a flat 2 bps/leg
cost) survives **realistic execution at size**, and up to **what AUM** — the load-bearing
input to the go/no-go on building the rest of the deployment stack (live wiring, risk
overlay).

**Success metric.** A **capacity curve**: the XS book's de-biased gate verdict
(`DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0`, with MinTRL reported) evaluated across a grid
of target capital `C`, yielding the **maximum AUM at which the edge still clears the
gate**. The verdict is a *band*, not a point — reported across an impact-coefficient
sensitivity (low/base/high) and a √-vs-linear robustness check, so a "survives" reading
is conservative and not an artifact of one cost parameter.

**Anti-drift.** This is a **kill-test**. The bias is conservative: where the model is
uncertain it should lean *pessimistic* on the thin alt names (the edge reverses to
majors — it lives in the alt breadth, exactly where liquidity is worst). An
optimistically-biased "survives" verdict is the worst failure mode and must be avoided.

## Background — why this, why now

The XS sleeve is the **only** gate-clearing, deployment-grade alpha in the system
(+1.375, DSR 0.997, PBO 0.295, boot CI [+0.60, +2.11], alpha-not-beta, persistent;
`docs/audits/2026-06-16-p3-xsmom-sleeve.md` +
`docs/audits/2026-06-17-p3-xsmom-beta-neutral-persistence.md`). The second-edge hunt has
failed four times (exits, trend-weight study, combine-can't-lift, funding carry), so the
shortest path to a *profitable* system is to monetize the edge we have, not to find
another.

The single assumption most likely to invalidate the deploy thesis is **execution cost at
size**. The current book models honest costs as `turnover × (fee + slippage)` with a
**flat** 2 bps/leg slippage — a constant across every instrument and every trade size.
Two things that flat constant cannot capture:

1. **Per-instrument spread** — alt half-spreads are far wider than majors.
2. **Market impact** — cost grows with trade-notional ÷ ADV. The XS book is **equal-risk**
   across the universe, so it puts the *same* risk on a thin alt as on BTC; impact
   concentrates precisely in the illiquid names that carry the edge.

This sub-project replaces the flat constant with a per-(instrument, day) **size-aware**
rate and sweeps capital to find where the edge breaks.

## Cost model

Per-instrument turnover cost as a fraction of capital, on day `d`:

```text
cost_rate_i(d) = fee_pct + half_spread_i  +  k · impact( |Δlev_i(d)| · C / ADV_i(d) )

turnover_cost_fraction_i(d) = |Δlev_i(d)| · cost_rate_i(d)
```

`fee_pct` is the size-independent maker/taker fee (additive constant); `half_spread_i` is
the per-instrument spread term; the `k · impact(...)` term carries the size-dependence.

where `impact(x) = √x` (headline) or `impact(x) = x` (linear, reported as a robustness
alternative).

- `|Δlev_i(d)|` — absolute day-over-day change in the instrument's vol-parity leverage
  (already computed inside the book; `leverage_i` is a fraction of capital).
- `half_spread_i` — **a-priori** half-spread in bps assigned by **trailing-median-ADV
  tier** (causal, point-in-time): majors / mid / alt. Default tiers
  `{major: 1 bp, mid: 3 bp, alt: 8 bp}` (a-priori, sensitivity-swept — not fit). Tier
  cutoffs are dollar-ADV thresholds (defaults proposed at implementation, e.g. ≥ \$1 B /
  ≥ \$100 M / else), assigned per instrument per day from trailing ADV so the assignment
  is causal and adapts as a name's liquidity changes.
- `ADV_i(d)` — trailing-median dollar volume `= median_{window}(volume × close)`, **causal**
  (through `d-1`, i.e. `.shift(1)` after the rolling reduction). Default window 30 d.
- `C` — target capital (USD). The **swept** axis: a grid (e.g. `[1e5, 1e6, 5e6, 1e7,
  2.5e7, 5e7, 1e8]`), tuned at implementation to bracket the break-point.
- `k` — **a-priori** impact coefficient (dimensionless under the √ form), **sensitivity-
  swept** low/base/high. Base anchored to a conventional crypto value at implementation
  (documented), never fit to maximize the verdict.

**Default-off, byte-identical.** With `k = 0` and `half_spread_i ≡ slippage_pct`, the
rate collapses to the current flat constant and the book output is **byte-identical** to
today's `run_xs_backtest`. Guarded by a regression test.

**Why a-priori (Option A), not an estimated spread (Corwin-Schultz) or impact-only.**
A kill-test must be conservative and free of overfit. Estimated spreads add a noisy
estimator for the *smaller, size-independent* term while the *impact* term decides
capacity — more machinery, no sharper answer. Impact-only (flat spread) understates alt
cost — optimistically biased exactly where the edge lives. A-priori tiers carry a wider
spread on the thin names (conservative where it matters) and every parameter is a-priori,
so there is nothing to overfit; we sensitivity-sweep instead.

## Architecture / packaging

All additions are **pure, causal, read-only, additive, default-off**. No schema change,
no golden change, no live-daemon contact.

### `analytics/xsmom/execution.py` (new, no DB)

- `ExecutionCostConfig` frozen dataclass: `capital`, `k`, `impact` (`"sqrt"`/`"linear"`),
  `adv_window`, spread tiers (`major_bps`/`mid_bps`/`alt_bps` + dollar-ADV cutoffs).
- `dollar_adv(volumes, closes, window) -> dict[str, pd.Series]` — trailing-median
  `volume × close`, causal (`.shift(1)`).
- `turnover_cost_rate(leverage, dollar_adv, exec_cfg) -> pd.DataFrame` — the per-(instrument,
  day) `cost_rate` matrix from the formula above, aligned to the leverage index. Computes
  `|Δlev|` internally (it is also recomputed in the book; the leverage matrix is
  deterministic, so the two agree).

### `analytics/xsmom/book.py` (one additive kwarg)

`run_xs_backtest(..., turnover_cost_rate: pd.DataFrame | None = None)`:

- `None` → existing scalar `cost = fee_pct + slippage_pct` path (**byte-identical**).
- provided → `turnover = |Δlev| · cost_rate_aligned` (reindexed to the union index). The
  `fee_pct` maker/taker fee is size-independent, so it stays an additive constant inside
  `cost_rate_i(d)` alongside `half_spread_i`; only the `slippage`/spread + impact terms
  carry the size- and instrument-dependence.

The book stays the single source of the long-short aggregation + governor; only the
turnover term is parameterized.

### `analytics/xsmom/replay.py` (read-only front door)

- `load_daily_dollar_volumes(conn, symbols) -> dict[str, pd.Series]` — reuses `get_ohlcv`,
  returns daily `volume × close` per symbol (sibling to `load_daily_inputs`, which stays
  untouched so the trend sleeve is unaffected).
- `replay_xs_capacity(conn, cfg, exec_cfg, capitals) -> dict[float, dict]` — for each `C`
  in `capitals`: build `dollar_adv` once, build the `turnover_cost_rate`, run the book and
  the 5-trial gate family (per-speed + combined, mirroring `replay_xs_trials`) under that
  cost. Returns, per capital, the `XSBookResult` + trial returns needed by the report.

### `analytics/xsmom/report.py` (reuse existing evaluator)

- A thin capacity wrapper that calls the existing `evaluate_xs` per capital and assembles a
  capacity table (`C` → Sharpe, DSR, PBO, boot_lo/hi, MinTRL, gate bool). The headline =
  the largest `C` whose row clears the gate. No new metric machinery — same research-guard
  stamps the sleeve already uses.

### `tools/xsmom_capacity_audit.py` + `make buibui-xsmom-capacity-audit`

Read-only (`duckdb.connect(..., read_only=True)`) over `analytics.db`. Prints:

1. **Capacity sweep** — capital grid × {Sharpe, DSR, PBO, boot CI, MinTRL, gate verdict}.
2. **Impact-`k` sensitivity** — capacity under low/base/high `k`.
3. **Spread-tier sensitivity** — capacity under a tighter and a wider tier set.
4. **√-vs-linear robustness** — capacity under each impact form.

Verdict written to `docs/audits/2026-06-20-p3-xsmom-capacity.md`.

## Causality / invariants

- **ADV is causal.** Trailing-median over a closed window, `.shift(1)` so day-`d` cost uses
  liquidity through `d-1`. No same-day volume leaks into the day's cost. A perturbation
  test asserts bumping `volume[k]` leaves day-`k` cost unchanged.
- **No new look-ahead in sizing.** The cost term only scales an already-causal leverage
  path; it never feeds back into the leverage/forecast. Positions are unchanged by capital
  — only the *net return* changes — so the capacity sweep is a pure cost re-scoring of one
  fixed position path.
- **Default-off byte-identical.** Regression test: `run_xs_backtest(...,
  turnover_cost_rate=None)` equals the pre-change output exactly.

## Testing

- `execution.py` unit tests: `dollar_adv` causality (shift) + trailing-median correctness;
  `turnover_cost_rate` — √ vs linear, tier assignment by ADV, monotonic increase in `C`,
  collapse to the flat constant at `k=0` + flat spread.
- `book.py`: byte-identical default-off; with a constant `cost_rate` matrix equal to the
  scalar, output matches the scalar path.
- `replay.py`: `replay_xs_capacity` monotonicity — net Sharpe is non-increasing in `C`
  (more capital ⇒ more impact ⇒ weakly worse), on a small synthetic/fixture DB.
- A perturbation test for the ADV causality invariant (RED without the `.shift(1)`).

## Definition of done

- `make lint-py` ✓ · `make typecheck` ✓ · `make test` green · `make test-regression`
  goldens **unmoved** (no behavioural change to any existing path).
- Verdict doc committed; CLAUDE.md `analytics/xsmom/` + `tools/` entries updated.

## Out of scope (YAGNI)

- Live wiring / order routing (deployment sub-project #3).
- Risk overlay / kill-switch (sub-project #4).
- Survivorship dead-name backfill (separate pre-capital magnitude check).
- Intraday execution / TWAP scheduling, real bid-ask ingestion, per-venue fee tiers — the
  a-priori model is deliberately a conservative screen, not an execution simulator.
- Applying the size-aware cost to the trend / carry / combine sleeves (they are shelved;
  revisit only if a sleeve is deployed).
