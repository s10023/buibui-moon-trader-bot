# M1 Indicator-State Layer — Design

**Date:** 2026-07-09 · **Status:** approved (brainstorm 2026-07-09) ·
**Milestone:** Brief-v2 roadmap M1

## Goal (one line)

Add seven pure, deterministic indicator-state components (EMA state, range
state, Monday-range state, yesterday candle pattern, PA character, BB/AVWAP,
volume-profile POC/VAH/VAL) to the daily brief's per-symbol panel — additive
everywhere, reusing existing primitives, with the reusable math placed at
`analytics/` top level so F2 and future research can consume it.

## Background

- Brief v1 shipped in PR #470 (`analytics/brief/` + CLI / `GET /api/brief` /
  Svelte Brief page); M0 correctness fixes merged in PR #473 (forming-bar
  sweep windows, fresh 1h ref price + `ref_price_source`, UI legend).
- The Brief-v2 roadmap (user-approved 2026-07-08) sequences M1 = indicator
  states → M2 sessions → M3 external context → M4 integration → M5 Stats
  rework. The F2 trade-card (M4) consumes M1–M3 components.
- The volume-profile primitive is a recurring want (~7 /ingest-x sightings)
  and is NOT data-blocked: POC/VAH/VAL approximate well from own OHLCV.

## Decisions (settled in brainstorm, 2026-07-09)

1. **Delivery:** one implementation branch → one PR, sonnet SDD with ~8–9
   tasks (per-component slices), per-task review + opus final review — the
   M0 execution pattern. (The earlier "one PR per component" note is
   superseded: 7 PRs = 7× CI minutes + 7 review cycles for no benefit.)
2. **Timeframes:** 1d only. All seven states compute on completed 1d bars
   (1h bars feed only AVWAP and the volume profile). 4h reads are a
   per-component follow-up if a state earns it.
3. **Range state = regime run-length + bounds** (no second range detector
   that could contradict the regime label on the same panel).
4. **PA character = efficiency × speed**, five a-priori labels.
5. **AVWAP anchors = weekly (Monday 00:00 UTC) + monthly (month open)** —
   the same period anchors the levels gauge already uses.
6. **Volume profile = single 60d composite** over 1h bars.
7. **Architecture Option B:** pure primitives at `analytics/` top level
   (`volume_profile.py`, `indicators.py`), one thin adapter in
   `analytics/brief/indicators.py` — the exact `reference_levels.py` /
   `brief/levels.py` precedent.

## Non-goals

- No 4h computes (decision 2).
- No new detectors, signals, gates, or live-daemon behavior — these are
  **display states**, not trading logic; the detector family stays FROZEN.
  No edge claim is made for any state; thresholds are descriptive display
  constants, not fitted parameters.
- No DB schema changes, no new tables, no writes — the brief stays
  read-only.
- No regression-golden movement: nothing on the backtest path changes.
- No external data (Coinglass/MMT is M3), no session layer (M2), no
  Telegram surface (brief v1.1 backlog).
- No dual profile windows, no HVN/LVN nodes (follow-ups).

## Architecture

New modules (→ = imports from):

```text
analytics/volume_profile.py     pure profile math (no DB, no brief imports)
analytics/indicators.py         pure indicator math (AVWAP, BB, ER/PA)
analytics/brief/indicators.py   panel adapter → volume_profile, indicators,
                                regime.classify_series, reference_levels,
                                strategies (candle detectors, compute_ema)
```

Touched (all additive):

```text
analytics/brief/types.py        + IndicatorState (+ sub-dataclasses),
                                SymbolPanel.indicators: IndicatorState | None
analytics/brief/bundle.py       + adapter call in _compute_panel;
                                _H1_FETCH_DAYS 3 → 62; compute the 1d regime
                                series ONCE and share (label + adapter)
analytics/brief/render.py       + "Indicators" block per panel
web/api/models/brief.py         + additive optional nested models
web/ui/src/pages/Brief.svelte   + indicators sub-section + legend entries
```

### `analytics/volume_profile.py` (new, pure)

- `build_profile(hourly_df, n_bins=100) -> Profile` — bins span
  `[min(low), max(high)]` of the window; each bar's volume is spread over
  the bins its `[low, high]` range overlaps, proportional to overlap
  (degenerate `high == low` bar → all volume into that price's bin).
- `value_area(profile, pct=0.70) -> (poc, vah, val)` — POC = center of the
  max-volume bin; greedy expansion adding the higher-volume neighbor bin
  until cumulative volume ≥ `pct` of total; VAH/VAL = outer edges of the
  expanded region. Deterministic tie-breaks: equal-volume POC candidates →
  lowest-price bin; equal-volume neighbors during expansion → the upper
  bin.

### `analytics/indicators.py` (new, pure)

- `anchored_vwap(hourly_df, anchor_ms) -> float | None` — Σ(typical × vol)
  / Σ(vol) over completed bars with `open_time >= anchor_ms`; typical =
  (high + low + close) / 3; None when no bars or zero total volume.
- `bollinger_state(close, period=20, k=2.0)` — %B of the last value,
  bandwidth = (upper − lower) / middle, bandwidth percentile vs the
  trailing window (see constants), squeeze flag.
- `efficiency_ratio(close, n) -> float` — |close[-1] − close[-1-n]| /
  Σ|Δclose| over the n steps (0.0 when the denominator is 0).
- `pa_character(close, atr, n) -> PA label + raw ER/speed` — see semantics.

### `analytics/brief/indicators.py` (new adapter)

`build_indicator_state(...) -> IndicatorState` consuming frames the bundle
already fetched (completed 1d, completed 1h, the shared 1d regime series,
ref_close, atr14, as_of_ms). Exact signature frozen at plan time. MonH/MonL
come from a `reference_levels.compute_levels` call inside the adapter
(cheap; the plan may instead share the levels gauge's call — either is
acceptable, byte-equivalent).

## Determinism & anchors (load-bearing)

- Every compute consumes **completed bars only** (`completed_bars`), same
  as the rest of `analytics/brief/`. No forming-bar leakage into any state.
- AVWAP anchors derive from `as_of_ms`: week anchor = the `week_monday`
  convention of `reference_levels.py` (Monday 00:00 UTC of the current
  week); month anchor = first of the current month 00:00 UTC. The M0
  invariant (anchor code byte-identical in intent to `reference_levels`)
  applies: a defining bar can never leak into its own reference window.
- `_H1_FETCH_DAYS` 3 → 62: 60d profile window + 2-day margin; the monthly
  VWAP (≤ 31d) is covered by the same fetch. ~1,490 1h rows per symbol —
  negligible.
- Same `as_of` ⇒ byte-identical markdown (existing brief determinism test
  extends to the new block).

## Component semantics (pre-committed)

All price comparisons use `ref_close` (the M0 fresh reference price); all
distances are ATR14-normalised — consistent with the levels/zones gauges.

### Constants (a-priori display constants — never fitted)

| Constant | Value |
| --- | --- |
| EMA spans | 20 / 50 / 200 (1d closes) |
| EMA200 slope lookback | 5 bars |
| PA lookback `n` | 10 bars |
| PA ER threshold (directional) | ≥ 0.40 |
| PA speed threshold (impulse) | mean abs Δclose / ATR14 ≥ 0.8 |
| BB period / k | 20 / 2.0 |
| BB bandwidth percentile window | trailing 180 bars (min 60, else None) |
| BB squeeze flag | bandwidth percentile ≤ 0.10 |
| Volume-profile window | 60 days of completed 1h bars |
| Volume-profile bins | 100 uniform |
| Value area | 70% |

### 1. EMA state

EMA 20/50/200 via `strategies/_shared.compute_ema` on completed 1d closes.
Reports: ref_close above/below each EMA; stack label (`bullish` =
EMA20 > EMA50 > EMA200, `bearish` = EMA200 > EMA50 > EMA20, else `mixed`);
EMA200 slope `rising` when EMA200[-1] > EMA200[-6], else `falling` (floats
make exact equality irrelevant). Fewer than 200
completed bars → the unavailable EMA reads None (no error; stack/slope
require the EMAs they reference, else None).

### 2. Range state

Run-length of the trailing identical label on the shared 1d regime series
(`analytics/regime.classify_series` — the same series that produces
`regime_1d`). Reports: current label, `since` (open date of the run's
first bar), bars-in-state. When the label is `range`: run high/low
(max high / min low over the run's bars) and ref_close position in that
band clipped to [0, 1] (zero-width band → position None). `unknown`
label → duration only.

### 3. Monday-range state

Vs MonH/MonL from `compute_levels`: `above` (ref_close > MonH), `below`
(ref_close < MonL), else `inside` with position fraction
(ref − MonL) / (MonH − MonL) (zero-width range → fraction None). When
`as_of` falls on a Monday (UTC), the
state is `forming` — the current week's Monday range is not yet a
reference, the same exclusion the levels gauge applies. Levels absent →
sub-block None.

### 4. Yesterday candle pattern

Run the six anatomy detectors — `engulfing`, `pin_bar`, `doji`,
`inside_bar`, `hammer_hanging_man`, `morning_evening_star` — with
registry-default params over the completed 1d frame; keep only signals
whose signal bar is the **last completed** 1d bar; report the
`(pattern, direction)` list. Empty list = "none" (a valid state, not an
error). `marubozu_retest` is excluded: its signal fires on the retest bar,
not the pattern bar, so "yesterday was a marubozu" cannot be read from it.

### 5. PA character

Over the last 10 completed 1d bars: ER = |net Δclose| / Σ|Δclose|;
speed = mean |Δclose| / ATR14. Labels:

| ER | speed | label |
| --- | --- | --- |
| ≥ 0.40 | ≥ 0.8 | `impulse_up` / `impulse_down` (net-move sign) |
| ≥ 0.40 | < 0.8 | `grind_up` / `grind_down` |
| < 0.40 | any | `chop` |

Raw ER and speed are reported alongside the label.

### 6. BB / AVWAP

BB(20, 2σ) on completed 1d closes: %B of ref_close, bandwidth, bandwidth
percentile vs the trailing 180 completed bars (min 60 bars, else
percentile None), `squeeze` flag at ≤ 10th percentile. AVWAP: weekly and
monthly anchors per the Determinism section, computed over completed 1h
bars from anchor; report signed ATR-distance of ref_close from each
(+ = price above VWAP). An anchor with no completed 1h bars yet (as_of at
the anchor itself) → that VWAP None.

### 7. Volume profile

60d of completed 1h bars ending at `as_of`; profile + value area per
`analytics/volume_profile.py`. Reports: POC/VAH/VAL prices, ref_close
`above`/`inside`/`below` the value area, signed ATR-distance to POC.

### Independence contract

Mirrors the seasonality strip: every sub-block computes independently
inside the adapter (per-component try/except; BB and AVWAP — one roadmap
component — are two independent sub-blocks, so eight in total); one
failure → that sub-block None + a health note
(`"SYM: indicator <name> failed"`); only all sub-blocks failing makes
`SymbolPanel.indicators` None. Per-symbol isolation (`error_panel`)
unchanged.

## Types (sketch — exact fields frozen at plan time)

```text
EmaState(above_20/50/200: bool | None, stack: str | None,
         slope_200: str | None)
RangeState(label: str, since_ms: int, bars: int,
           range_low/high: float | None, pos: float | None)
MondayState(state: str, pos: float | None)
CandleHit(pattern: str, direction: str)
PaState(label: str, er: float, speed_atr: float)
BbState(pct_b: float, bandwidth: float, bw_pctile: float | None,
        squeeze: bool | None)
VwapState(weekly_dist_atr: float | None, monthly_dist_atr: float | None,
          weekly/monthly price: float | None)
ProfileState(poc/vah/val: float, vs_value: str, poc_dist_atr: float)

IndicatorState(ema, range_state, monday, candles: list[CandleHit] | None,
               pa, bb, vwap, profile)   # every sub-block optional
```

All frozen dataclasses in `analytics/brief/types.py`, serialised through
the existing `asdict`-based `bundle_to_dict` (additive keys only).

## Rendering (`render.py`)

A compact "Indicators" block per panel, placed directly after the regime
header line (it is context, like regime), before the levels gauge. One
deterministic line per component reusing `fmt_price`/`fmt_dist`; exact
strings frozen at plan time. Sketch:

```text
EMA: ▲20 ▲50 ▼200 · stack mixed · 200 falling
State: range since 21 Jun (18d) · 105.2k–112.8k · price 62%
Monday: inside (43%)
Candle(y): bullish_engulfing (long)
PA: grind_up · ER 0.55 · 0.4 ATR/bar
BB: %B 0.71 · bw 8.3% (p23) | AVWAP: W +0.4 ATR · M −1.2 ATR
VP 60d: POC 108.4k (−0.3 ATR) · VA 104.1k–113.9k · inside
```

A failed sub-block **drops its line** (seasonality-strip behavior; the
health footer records why). The shared `BB | AVWAP` line drops only the
failed half and renders the surviving half alone; it disappears only when
both fail. Valid empty states still render (`Candle(y): none`).

## API + UI

- `web/api/models/brief.py`: additive optional nested Pydantic models
  mirroring the dataclasses; `GET /api/brief` response grows the
  `indicators` key per panel. No breaking change.
- `Brief.svelte`: an indicators sub-section per symbol card mirroring the
  markdown block; the M0 legend ⓘ card gains one entry per new row
  explaining how to read it. Frontend tasks load `/frontend-design` +
  `/frontend-svelte` first (repo rule — carried in the sonnet task brief).

## Error handling

- Adapter-level: per-component try/except → None sub-block + health note
  (see Independence contract). No exception escapes the adapter.
- Bundle-level: unchanged — `BriefDataError` / per-symbol `error_panel`
  isolation as today.
- Insufficient data is a **state**, not an error, wherever it can be
  (EMA200 None under 200 bars, percentile None under 60 bars, VWAP None
  with no bars past anchor).

## Testing

- **Primitives** (`tests/test_volume_profile.py`, extend indicator tests):
  hand-computed fixtures — tiny frames with known POC/VAH/VAL, hand-math
  AVWAP, BB values, the 5-label PA matrix including threshold boundaries
  (ER = 0.40, speed = 0.8); degenerate inputs: empty frame, constant
  price, zero volume, `high == low` bars.
- **Adapter**: synthetic 1d/1h frames → `IndicatorState` assertions:
  Monday-`forming` case, range-run bounds + clipping, candle filter keeps
  only last-bar signals, one-component-failure isolation (poisoned input
  for one component → its sub-block None, others intact, health note
  emitted).
- **Bundle/API**: additive fields present; determinism — two
  `compute_brief` calls at the same `as_of` produce byte-identical
  markdown (extends the existing test).
- **Renderer**: fixed-input string assertions per line type, including
  dropped-line and `Candle(y): none` cases.
- **Regression**: `make test-regression` goldens UNMOVED (nothing on the
  backtest path changes) — run and stated explicitly in the PR.

## Definition of Done

- `make lint-py` ✓ · `make typecheck` ✓ (mypy strict) · `make test` green
- `make test-regression` — goldens UNMOVED
- `make web-build` clean
- `make lint-md` ✓ (docs)
- CLAUDE.md brief section + README synced (post-branch sweep)

## Execution shape

Single implementation branch (`feat/m1-indicator-state`) → one PR. Sonnet
SDD, ~8–9 tasks, expected slicing (frozen in the plan doc):

1. `analytics/volume_profile.py` + tests
2. `analytics/indicators.py` (AVWAP / BB / ER / PA) + tests
3. Types + adapter scaffold + EMA/range/Monday states + tests
4. Candle-pattern + PA states + tests
5. BB/AVWAP + volume-profile wiring in adapter + tests
6. Bundle wiring (`_H1_FETCH_DAYS`, shared regime series, health notes)
7. Renderer block + determinism test extension
8. API models + Svelte section + legend entries
9. Docs sync (CLAUDE.md / README)

Per-task review + opus final whole-branch review, as in M0.

## Follow-ups (out of scope for M1)

- 4h reads for trend-ish states (EMA / range / PA) if earned.
- Dual profile windows (30d/90d), HVN/LVN nodes.
- State-tag research over these states (e.g., avg_r conditioned on
  PA-character or value-area position) — uses the top-level primitives
  directly, gated by `audit_guard` like every other research question.
- Telegram brief surface (v1.1 backlog); F2 trade-card consumption (M4).
