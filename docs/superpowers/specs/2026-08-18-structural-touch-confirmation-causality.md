# Structural touch geometry — confirmation causality

**Date:** 2026-08-18 · **Status:** pre-registered, not yet run
**Corrects:** `docs/audits/2026-06-26-structural-entry-sim-harness.md` (the BUILD that motivated SoT ST31)
**Touches:** `analytics/zones_lib.py`, `analytics/structural_touch.py`, `tools/structural_entry_sim_audit.py`

---

## 1. What this answers

The structural entry-sim harness indexes a zone's touches from its **formation** bar. For every
zone type the formation bar precedes the bar at which the zone is knowable from closed bars, so
the harness counted — and the production engine then traded — touches that no live detector
could ever have seen.

`_zone_from_dict` drops the extractors' `active` / `close_ms` fields, and `index_touches` admits
any bar with `open_time > zone.start_ms`. Nothing in the chain carries a confirmation concept.

| zone | `start_ms` is | knowable only at | lag |
| --- | --- | --- | ---: |
| `fvg` | bar `i-1` | close of `i+1` — the gap needs that bar | 2 bars |
| `ob` | bar `i` | close of `i+1` — needs the displacement candle | 1 bar |
| `bos` | the swing bar `i` | `i + swing_lookback` (5) | 5 bars |
| `eqh_eql` | the **first** swing of the pair (`idx_j`) | `idx_k + swing_n` (5) | unbounded |

Measured 2026-08-18 on 5 symbols × `1d` from `analytics.db` (the audit used 25, so shares will
shift):

- **`fvg` first touch lands exactly 1 bar after formation for 100.0% of 2,148 zones** (mean lag
  1.00, no spread). The audit's `fvg` "first touch" *is* the impulse candle every time, and the
  engine's next-bar-open entry falls on the bar whose low defines the gap's own top edge.
- Applying a per-type confirmation offset, the first tradable touch carries audit `touch_index`:
  **`fvg` — 0.0% are touch 1, 100% are repeats**; `bos` — 60.8% touch 1; `eqh_eql` — 57.2% touch 1
  (an upper bound: the `+5` offset understates `eqh_eql`'s true lag).

Arithmetic on the audit's own published decay lifts — an estimate, not a re-run — puts the
tradable populations near `fvg/long` **−0.11R**, `fvg/short` **−0.04R**, `bos/long` **+0.09R**,
`bos/short` **+0.09R**, `eqh_eql/long` **+0.22R**. The two cells carrying 59% of the first-touch
population and the best headline R invert to about zero or negative.

**This is not a live-alerting defect.** `analytics/strategies/{fvg,eqh_eql,market_structure}.py`
are separate detectors with their own logic; the defect is confined to the research harness.

### Why no gate caught it

`tests/test_structural_touch.py::test_build_touch_table_is_causal_no_lookahead` perturbs only the
**final** bar and asserts early rows are unchanged. That proves no distant-future leakage into
already-formed rows; it is structurally blind to a 1-bar *local* look-ahead, which is the whole
defect → [[vacuous-guard-fixture-trap]], [[mutation-tests-prove-reachability-not-scope]]. A green
test named `is_causal` is why this survived seven weeks as the board's only cleared BUILD.

### 1a. The parent audit inherits it

`build_touch_table` calls the same `index_touches`, so
`tools/structural_touch_decay_audit.py` — which produced the touch-decay audit this whole
chain escalated from — measured the same formation-time population. Its "first touches run
further in excursion space" result is therefore partly *impulse continuation vs everything
else* rather than *first visit vs repeat visit*, since `fvg`'s first touch is the impulse
candle 100% of the time. That tool gets the same legacy switch so its filed run stays
reproducible; **re-running it is not in this change's scope** and its numbers stand as
superseded-but-unrecomputed until someone does.

## 2. Non-goals

- **Not** a change to any live detector, alert path, or `signal_watch*.toml`.
- **Not** a 4h validation. 4h stays UNVALIDATED — the OOM in `build_realized_table` is untouched.
- **Not** a re-tune. The gate below is the original gate, unmodified, applied to a corrected
  population. No threshold moves.

## 3. Design

### 3a. `confirm_ms` on every zone dict

Each `analytics/zones_lib.py` extractor emits an additive `confirm_ms` key: the `open_time` of
the earliest bar at which the zone is knowable using only bars closed at or before it.

| extractor | `confirm_ms` |
| --- | --- |
| `extract_fvg_zones` | `open_times[i + 1]` |
| `extract_order_block_zones` | `open_times[i + 1]` |
| `extract_bos_zones` | `open_times[i + swing_lookback]` |
| `extract_eqh_eql_zones` | `open_times[idx_k + swing_n]` |
| `extract_fib_golden_zones` | supplied by the caller's window (see 3b) |

The swing loops already bound `i < n - swing_n`, so every index above is in range. Existing
consumers ignore the new key.

`extract_fib_golden_zones` returns only the single current zone, so it cannot know its own
confirmation bar; `_fib_walk_forward` stamps `confirm_ms` from the last bar of the expanding
window that first produced the zone, which is exactly when it became knowable.

### 3b. `Zone.confirm_ms`

`Zone` gains `confirm_ms: int = 0`. `_zone_from_dict` reads the key when present and falls back
to `start_ms`, so a caller passing legacy dicts keeps the old behaviour rather than crashing.

### 3c. `index_touches` — two independent switches

```python
def index_touches(zone, bars, *, min_gap_bars=1,
                  respect_confirmation=True, require_outside_first=True) -> list[Touch]
```

- `respect_confirmation` — a bar is eligible only when `open_time >= zone.confirm_ms`. Equality is
  correct: the zone is known at that bar's close and the engine enters at the next bar's open.
- `require_outside_first` — `outside_run` starts at `0` instead of `min_gap_bars`, so the first
  touch must be a genuine **return** from outside the band. Without it, band zones whose edge is
  defined by the confirmation bar (`fvg`, `ob`) report a mechanically-guaranteed touch on that
  bar, which is momentum continuation rather than the "levels of interest" thesis under test.

Both default **True** (causal). Setting both `False` reproduces the original numbers exactly,
which is what makes the correction auditable rather than a replacement.

Touch numbering restarts at 1 from the first eligible touch — the first touch a live detector can
see IS its first touch. The re-run therefore measures first-visible vs repeat-visible, the
tradable analogue of the original first-vs-repeat cut.

## 4. Decision Log

| # | Decision | Reversing observable |
| --- | --- | --- |
| D1 | `confirm_ms` lives in `zones_lib`, not re-derived in `structural_touch` | A consumer needs a *different* confirmation rule for the same zone type — then the rule belongs at the call site, not the extractor |
| D2 | Eligibility is `>= confirm_ms`, not `>` | Evidence the engine can enter on the confirmation bar's own open — it cannot; entry is next-bar-open |
| D2a | On the **first** eligible bar D3 necessarily dominates D2, so an already-inside zone yields no touch until price leaves the band and returns | Only if D3 is turned off; the inclusive boundary is then observable on its own, and is tested that way |
| D3 | `require_outside_first` defaults True | The corrected `fvg` cells clear the gate **only** with it False, i.e. the edge is impulse continuation. That is a different, nameable strategy — file it as a new hypothesis, do not fold it back into "touch" |
| D4 | Original gate reused unmodified | Never. A moved threshold on a corrected population is unfalsifiable |
| D5 | 1d only | 4h `build_realized_table` stops accumulating in memory |

## 5. Pre-registered gate and expectation

**Locked before running.** On the headline config (`tp_r=2.0 × sl_model=atr_floor`), evaluated for
`tf=1d` alone, first-**visible**-touch net realized R must clear all of: `n_first >= 30`; block-
bootstrap CI lower bound `> 0.0`; Holm-adjusted `p < 0.05` across the (zone_type × direction)
family; `n_first >= MinTRL(0.95)`; and `DSR >= 0.95 ∧ PBO <= 0.5` over the `tp_r × sl_model` trial
family. Params, fees and seed identical to the 2026-06-26 run: `fee_bps=5.0`, `slippage_bps=2.0`,
`n_boot=10000`, `seed=12345`, grid `tp_r=[1.0,1.5,2.0,3.0] × sl_model=[structural,atr_floor,fixed_atr]`.

**Stated expectation, so the run can falsify it:** `fvg/long` and `fvg/short` fail; `bos/long`,
`bos/short` and `eqh_eql/long` clear at roughly half their published R. If `fvg` clears anyway, D3
is the first thing to inspect.

**What ships is decided by the run, not by this doc.** Cells that clear get a `structural_touch`
detector via `/new-strategy`; cells that do not are recorded as closed. Zero surviving cells is a
successful outcome and closes ST31.
