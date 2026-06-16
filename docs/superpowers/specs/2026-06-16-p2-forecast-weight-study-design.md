# P2 EWMAC trend sleeve — forecast-weight study (design)

**Date:** 2026-06-16
**Status:** Approved (design); plan + implementation to follow
**Predecessor:** `docs/audits/2026-06-15-p2-ewmac-trend-g2.md` (G2 verdict)
**Spec/plan of sub-project A:** `docs/superpowers/specs/2026-06-15-p2-ewmac-trend-sleeve-design.md`

## Goal (success metric, one line)

Determine whether re-weighting the four EWMAC speeds lifts the *universe-combined*
Sharpe toward the fast-speed level (s8/32 ≈ **+0.83**) **and survives DSR / PBO
over the enlarged trial family**. Verdict: survives the haircut → re-test G2 with
the new weights; the lift evaporates under the haircut → equal-weight stays, the
lift was in-sample.

## Background

G2 (`docs/audits/2026-06-15-p2-ewmac-trend-g2.md`) found the universe equal-weight
combine at Sharpe +0.36 — sub-bar — while the single fast speed s8/32 alone is
+0.83 and the two slowest speeds are ≈ 0 (s64/256 the worst at +0.03, falsifying
the H2 slow-cycle hypothesis). The equal-weight mean is dragged down by the dead
slow legs. The obvious question: does down-weighting the slow legs recover the
fast-speed edge at the portfolio level?

**The methodological trap.** "Down-weight the dead slow speeds" is a choice made
*after* seeing the per-speed Sharpes on this same data — in-sample selection. A
naive re-weight will look better by construction. The only honest answer comes
from deflating the chosen scheme against the full family of schemes we examined
(DSR) and measuring the overfit probability of the selection itself (PBO/CSCV).
This study is therefore as much about the haircut accounting as the weights.

## Scope

In scope:

- A weighted combine in the forecast engine (additive; equal-weight unchanged).
- A small, labelled family of candidate weight schemes (a-priori + data-snooped).
- A read-only study front door + an honest two-family DSR/PBO haircut.
- A driver flag on `tools/forecast_audit.py` and a written verdict.

Out of scope (deferred, per the roadmap):

- P3 cross-sectional momentum / correlation / IDM portfolio layer.
- Carry sleeve.
- Any change to the live daemon, schema, or golden fixtures.

## Design

### 1. Weighted combine (engine)

- `ForecastConfig` gains `weights: tuple[float, ...] | None = None`.
  - `None` ⇒ equal weight (today's behaviour).
  - When set, `len(weights)` must equal `len(speeds)` (validated in
    `__post_init__`; the dataclass is frozen, so use `object.__setattr__`-free
    validation — raise on mismatch).
- `combine_forecasts(...)` takes the weights through `cfg` (or an explicit
  `weights` argument) and computes a **weighted** mean of the per-speed scaled
  forecasts, then × FDM, then re-cap to ±cap. Weights are normalised to sum 1
  internally so callers may pass unnormalised relative weights.
- **Invariant:** when `weights is None` the result is byte-identical to the
  current `.mean(axis=1)` path. This is what keeps the golden fixtures unmoved
  and the default G2 numbers stable.

### 2. Candidate weight schemes (`analytics/forecast/weights.py`, pure)

A new pure module — no DB, no IO — that derives weight vectors *from the speed
structure*, not from hard-coded magic numbers.

```text
WeightScheme = (weights: tuple[float, ...], a_priori: bool)
candidate_schemes(cfg: ForecastConfig) -> dict[str, WeightScheme]
```

Schemes (speeds ordered fast → slow, as in `_DEFAULT_SPEEDS`):

| name | a_priori | rule | rationale |
| --- | --- | --- | --- |
| `equal` | yes | uniform | baseline / control |
| `inverse_cost` | yes | wᵢ ∝ slowᵢ (slower legs trade less) | Carver cost logic; no look-ahead |
| `carver_handcraft` | yes | Carver-style handcrafted (down-weight adjacent correlated speeds) | a-priori structure, no realized-Sharpe input |
| `fast_tilt_linear` | no | wᵢ ∝ (N − i) | snooped: tilt to fast |
| `fast_tilt_geom` | no | wᵢ ∝ ρ^i, ρ<1 | snooped: stronger fast tilt |
| `drop_two_slowest` | no | fast two equal, slow two = 0 | snooped: kill dead legs |
| `fast_only` | no | s8/32 = 1, rest = 0 | snooped: degenerate upper bound |

`a_priori=True` schemes carry no look-ahead (defensible to ship as-is).
`a_priori=False` schemes are explicitly motivated by the realized per-speed
Sharpes and must be treated as data-snooped in the writeup.

### 3. Read-only study front door + two-family haircut

- `replay.py`: `replay_weight_schemes(conn, cfg, symbols) -> dict[str, np.ndarray]`
  — combined daily portfolio returns per scheme. Reuses `load_daily_inputs`;
  swaps `cfg.weights` per scheme via `dataclasses.replace`.
- **Two multiple-testing families, on purpose:**
  - **DSR** deflates each scheme's Sharpe against
    `{all weight schemes} ∪ {4 per-speed singles}` — counts everything we looked
    at (conservative).
  - **PBO / CSCV** matrix columns = `{weight schemes only}` — the configs we
    actually *select among*. Including the dead singles would make the chosen
    scheme beat the family median too easily and **inflate** apparent robustness
    (wrong direction), so they are excluded from the PBO matrix.
- Minimal additive change to `report.evaluate(...)`: optional
  `pbo_returns: dict | None = None` defaulting to `trial_returns` (so the
  existing G2 call site is byte-identical). The study passes the wider family as
  `trial_returns` (DSR) and the schemes-only family as `pbo_returns` (PBO).

### 4. Driver + verdict

- `tools/forecast_audit.py` gains `--weight-study`. It prints:
  - a table `scheme | a_priori | universe_sharpe | dsr | rank`,
  - the family-level PBO,
  - the best scheme's bootstrap-CI lower bound + MinTRL.
- **Decision rule** (mirrors `analytics/sweep_guard.py`): a scheme clears the bar
  iff `DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0`.
- Verdict written to `docs/audits/2026-06-16-p2-forecast-weight-study.md`:
  - If the best **a-priori** scheme clears the bar and lifts Sharpe materially →
    recommend re-testing G2 with that scheme (defensible, no look-ahead).
  - If only **data-snooped** schemes clear the bar → report honestly as
    suggestive-but-snooped; do not ship without OOS confirmation.
  - If nothing clears the bar → equal-weight stays; the lift was in-sample;
    breadth (P3) remains the binding constraint.

## Components & boundaries

| unit | purpose | depends on |
| --- | --- | --- |
| `ForecastConfig.weights` | carry the combine weights | — |
| `combine_forecasts` | weighted mean of per-speed forecasts | `scaled_forecast`, vol |
| `weights.candidate_schemes` | derive the labelled scheme family | `ForecastConfig` |
| `replay.replay_weight_schemes` | per-scheme daily returns (read-only) | `run_forecast_backtest`, DB |
| `report.evaluate(pbo_returns=…)` | two-family DSR/PBO stamps | research_guards, metrics |
| `tools/forecast_audit.py --weight-study` | study table + verdict | all of the above |

## Testing (TDD)

- `weights.py`: each scheme sums to 1; lengths match speeds; `a_priori` flags
  correct; `drop_two_slowest`/`fast_only` zero the right legs.
- `combine_forecasts`: weighted mean correctness; **equal weights == None path
  == old mean** (byte-identical); cap re-applied; FDM applied.
- `ForecastConfig`: weights length-mismatch raises.
- `replay_weight_schemes`: returns one key per scheme; equal-scheme returns ==
  the existing combined book.
- `report.evaluate`: `pbo_returns=None` reproduces the current behaviour;
  passing a distinct `pbo_returns` changes only the PBO, not the DSR.
- Causality is unchanged (the `.shift(1)` lives in `book.py`, untouched) — but
  add one guard test that a weighted combine still has no look-ahead
  (mid-series perturbation does not move earlier positions).

## Definition of Done

- `make lint-py` ✓ · `make typecheck` ✓ · `make test` green
- `make test-regression` goldens **UNMOVED** (default equal-weight path
  unchanged) — if any golden moves, the byte-identical invariant is broken; stop.
- `make lint-md` ✓ for this spec and the verdict doc.
- Read-only against `analytics.db`; no schema, no live-daemon change.

## Risks / open questions

- The whole point is that the snooped schemes may *not* survive the haircut.
  A negative result (equal-weight stays) is a valid, expected outcome and must
  be reported as such — not hidden.
- `carver_handcraft` weights need a defensible a-priori rule; if a clean rule
  isn't available without correlation estimation on this data (which reintroduces
  look-ahead), drop it from the a-priori set rather than fudge it.
