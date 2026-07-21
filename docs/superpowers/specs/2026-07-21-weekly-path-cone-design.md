# Weekly Path Cone + Monthly Context — design

- **Date:** 2026-07-21
- **Status:** approved (brainstorm), plan pending
- **Origin:** operator shower-thoughts 2026-07-21 — *"missing big picture context;
  does BTC behave more like a bullish or bearish month/week/day?"*
- **Predecessor:** `docs/superpowers/specs/2026-07-18-m5-price-distribution-cone-design.md`
  (Daily Path Cone, shipped PR #493 + polish #496)
- **Roadmap slot:** Brief-v2 **M5**, replacing External-block presentation as the lead
  slice. See memory `brief-v2-roadmap`.

## 1. Problem

The system frames the market at exactly one horizon: today. `analytics/stats/path_cone.py`
gives an ADR-normalized conditional distribution of intraday paths, and the Brief and
Stats tab render it. Above that, there is nothing — no representation of where the
current week sits in the distribution of past weeks, and no monthly context at all.

The operator's stated end goal is a nested read: month → week → day, used to form a
directional frame, from which ideas and invalidations follow. This spec builds the
**weekly** rung and a cheap **monthly** strip. It deliberately does **not** build the
inference step that turns a frame into an expectation; see §8.

## 2. Scope decisions (operator-approved)

| Decision | Choice | Rejected alternatives |
| --- | --- | --- |
| Weekly conditioning | Direction only, weekday becomes the x-axis | Prior-week outcome (drifts into forecasting); unconditional-only |
| Unconditional reference | Rendered underneath the conditional cone | Omitting it |
| Weekly granularity | 168 hourly bars | 42 × 4h; 7 × 1d |
| Monthly | Context line, **no cone** | Full monthly cone |
| Surfaces | Stats card + Brief block | Stats-only; or also F2 card |
| F2 trade card | Explicitly deferred, gated on H10 | Wiring it now |

### 2.1 Why no monthly cone

Months are ragged (28–31 days), so a monthly cone needs a resampled
fraction-of-month-elapsed x-axis — different machinery, not a parameter change. At the
same time the power collapses:

| Layer | Population | After direction split |
| --- | --- | --- |
| Daily | ~2,700 days | ~1,350, then ÷7 weekdays ≈ 190 |
| Weekly | ~390 weeks | ~195 |
| Monthly | ~90 months | ~45 |

At n≈45 each tail band is pinned by three or four observations. The chart would render
and look authoritative while being noise in exactly the tails a trader reads. That is
the failure mode behind the 2026-07-20 `bos` "100% win" misreading (memory
`live-outcomes-reading-traps`).

### 2.2 Why the F2 card is deferred

`card/prompt.py` pins `PROMPT_VERSION = "card-v2"`; every card appends to
`docs/plans/ai-cards.jsonl` and dual-writes a pundit-calls row. A rubric change is a
gated `card-v3` bump that breaks comparability with the existing card history. More
importantly, the card is the component that recommends position sizes — it must not
receive a framing that has no verdict yet. After H10 (§8) returns a verdict, wiring is
additive and small, because the Brief block **is** the payload the card would consume.

## 3. What the cone is, and is not

**The cone is conditional on outcome. It is not a forecast.**

A "bull week" is *defined* as one closing above its open. The bull cone therefore sits
above the unconditional cone by construction, and that separation carries zero
predictive information. The cone answers: *given a week turned out bullish, what did its
path typically look like?* — genuinely useful for shape (do bull weeks run from Monday,
or dip Tuesday and reverse?).

It does **not** answer *"will this week be bullish?"* At hour 63 the operator does not
know which cone they are in. This constraint is binding on UI copy (§6.2) and on the
non-goals (§9).

## 4. Architecture

New standalone module **`analytics/stats/weekly_cone.py`**, structurally parallel to
`path_cone.py` but sharing no internals with it.

`path_cone.py::_fetch_hourly` groups bars **by UTC date**, which the weekly cone does not
want; the only genuinely shared code is a nine-line SQL `SELECT`. Refactoring a shipped,
cached, spec'd module to deduplicate nine lines is a bad trade. **No change to
`path_cone.py`.**

### 4.1 Module surface

```python
BAND_PCTS = (10.0, 25.0, 50.0, 75.0, 90.0)
EXCURSION_PCTS = (10.0, 50.0, 90.0)
PIVOT_PCTS = (50.0, 80.0)
DIRECTIONS = ("all", "bull", "bear")
WEEK_BARS = 168
_AWR_WINDOW = 14  # weeks

@dataclass
class WeeklyConeCombo:
    direction: str            # "all" | "bull" | "bear"
    n: int
    bands: list[list[float]]  # 168 steps × 5 percentiles (×AWR)
    low_in_by: list[float]    # 168 cumulative P(week low set by hour h)
    high_in_by: list[float]
    mae_p: list[float]        # [p10, p50, p90] of path minimum
    mfe_p: list[float]
    high_piv: list[float]     # [p50, p80] of (week_high − open)/open/awr14
    low_piv: list[float]

@dataclass
class WeeklyConeBundle:
    combos: dict[str, WeeklyConeCombo]  # keyed by direction
    total_weeks: int

@dataclass
class CurrentWeekPath:
    points: list[float]   # normalized hourly closes, forming bar last
    elapsed_h: int        # 0–168
    awr14_current: float
    week_open: float

def compute_weekly_cone(conn, symbol, *, now_ms: int | None = None) -> WeeklyConeBundle: ...
def compute_current_week_path(conn, symbol, *, now_ms: int | None = None) -> CurrentWeekPath | None: ...
```

The operator's chosen (a)+(c) pairing needs no new concept: `DIRECTIONS` already models
`"all"` as the unconditional pool, so the bundle is **three combos**, where `all` **is**
the reference band rendered underneath.

Payload: 3 × (168×5 + 168 + 168) ≈ 3.5k floats — comparable to the daily cone's ~4k
across 24 combos.

### 4.2 Integration points

| File | Change |
| --- | --- |
| `analytics/stats/bundle.py` | Additive `weekly_cone: WeeklyConeBundle` on `StatsBundle`, computed in `compute_all` |
| `web/api/routers/stats.py` | `WeeklyConeResponse` mirroring `PathConeResponse`; live current-week overlay served **uncached**, mirroring `compute_today_path` |
| `analytics/brief/` | New weekly-state sub-block on `SymbolPanel`, following the `indicators.py` / `sessions.py` isolation pattern |
| `web/ui/src/components/WeeklyCone.svelte` | New sibling component |
| `web/ui/src/lib/cone.ts` | New — pure band/price math shared with `PathCone.svelte` |

### 4.3 Named risk — cached bundle shape

**RESOLVED 2026-07-21 — already mitigated, no work required.** `StatsBundle` is cached
via `analytics/store/stats_cache.py`, keyed `(symbol, days, computed_date)` with **no
schema version**, so a warm entry written before this change will lack the new field.
That is safe anyway: `web/api/routers/stats.py:202-208` wraps
`StatsResponse.model_validate_json(cached)` in `try/except Exception` and falls through
to a full recompute on failure. A stale entry therefore degrades to one recompute, not a
500.

Implementation must **not** remove that try/except, and must not make the new field
required in a way that bypasses it (e.g. validating the cache payload elsewhere).

## 5. Data contract

| | Daily cone (shipped) | Weekly cone (new) |
| --- | --- | --- |
| Period | UTC day | **Mon 00:00 → Sun 23:00 UTC** |
| Bars | 24 hourly, all required | **168 hourly, all required** |
| Normalizer | ADR14 over 14 prior complete days | **AWR14** — same formula over 14 prior complete weeks |
| Direction | day close vs day open | **week close vs week open** |
| Conditioning | direction × weekday (24 cells) | **direction only (3 cells)** |
| x-axis | hour 1–24 | **hour-of-week 1–168, labelled Mon…Sun** |

Week anchoring matches `analytics/reference_levels.py` (Monday 00:00 UTC).

### 5.1 Population rule

A week enters the population only if it:

1. has exactly 168 hourly bars,
2. has a positive week open,
3. closes strictly before the current week, and
4. has 14 complete qualifying prior weeks for its AWR window.

AWR14 for week `w` = mean `(high − low)/open` over the 14 complete weeks strictly before
`w`. Normalized path point `i` = `(close_i − week_open) / (week_open × awr14)`.

Direction = `bull` if week close > week open, `bear` if <, else `doji`.

### 5.2 Coverage pre-check — RUN 2026-07-21, PASSED

Counted over `analytics.db`, interior weeks only (first and last partial weeks dropped):

| Symbol | Interior weeks | Exactly 168 bars | Short | Over |
| --- | --- | --- | --- | --- |
| BTCUSDT | 358 (2019-09-02 → 2026-07-20) | **358 (100.0%)** | 0 | 0 |
| ETHUSDT | 346 | **346 (100.0%)** | 0 | 0 |
| SOLUSDT | 304 | **304 (100.0%)** | 0 | 0 |

**168 hourly bars confirmed; the 4h/42-step fallback is not needed and is dropped from
scope.** The all-168 completeness rule costs nothing on the majors.

Resulting population for BTCUSDT: 358 − 14 AWR warmup ≈ **344 usable weeks**, splitting
roughly 172/172 bull/bear. (§2.1's "~390 / ~195" was an estimate; these are the measured
figures and supersede it.)

## 6. Surfaces

### 6.1 Stats — `WeeklyCone.svelte`

A sibling component, not an extension of `PathCone.svelte`: the daily component carries a
weekday selector and a 24-step axis the weekly view does not want.

The two **must** share pure math via `web/ui/src/lib/cone.ts` — band-path construction
and, critically, the ADR/AWR-relative → price conversion that PR #496 established. A
weekly cone whose hover prices disagreed with the daily cone's would be worse than no
weekly cone. Shared functions, independent layouts.

Fold in while touching that math: `fmtPx` renders four decimals below 1000 and the #496
readout surfaces it five times per line (memory `path-cone-followups`).

Load `/frontend-design` before UI work; gate on **both** `make web-build` and
`make web-check` (memory `web-build-is-not-a-type-gate`).

### 6.2 Brief — weekly-state block

```text
Week  bull path · h63/168 (Wed 15:00 UTC · Wed 23:00 MYT) · +0.42×AWR
      p68 of bullish weeks (n=195) · p61 unconditional (n=390)
      low set Tue h31 · high h58 · 71% of bull weeks had set their low by now
```

The axis, day bands, and gridlines stay UTC-anchored — the week is defined by the
Binance weekly candle (Monday 00:00 UTC) and re-anchoring it to MYT would diverge from
that boundary and from `analytics/reference_levels.py`; MYT is surfaced only in this
hour readout, alongside UTC, because that is the operator's working timezone.

Degrades to a health note on failure, never failing the brief.

### 6.3 Brief — monthly context line

Three descriptive numbers, explicitly not a cone:

```text
Month +3.1% · p58 of 90 months · range position 0.64
      July seasonality: median +1.2%, 5/7 green (n=7)
```

Definitions, so the three numbers are unambiguous:

- **Month return** — current month-to-date close vs month open.
- **Percentile of N months** — rank of that MTD return among all *completed* prior
  calendar months' full returns. Comparing a partial month to completed months is a known
  apples-to-oranges read; the label carries the month's elapsed fraction so it is visible.
- **Range position** — `(price − month_low) / (month_high − month_low)` over the current
  month to date, in `[0, 1]`. Undefined (rendered `—`) when high equals low.

Reuses the seasonality already computed by `analytics/strategies/_seasonality.py`. The
`n=7` label is **not optional** — calendar-month seasonality over seven years is seven
observations, and must read as the anecdote it is.

## 7. Testing

Pure-lib + `duckdb.connect(":memory:")`, per repo convention.

- **Completeness** — 167-bar week excluded; duplicate-bar week excluded.
- **Normalization identity** — normalized path at bar 168 equals week return ÷ AWR14
  (closed-form property, not a golden number).
- **Week boundary** — Monday 00:00 UTC anchoring, verified across a year boundary.
- **Warmup** — the first 14 qualifying weeks produce no records.
- **Thin data** — empty combos, never raises (matches `compute_path_cone`'s contract).
- **Determinism** — same `(symbol, week)` in, byte-identical bundle out.
- **Live overlay** — partial week with forming bar; `elapsed_h` within 0–168.
- **Regression goldens unmoved** — nothing here touches the backtest pipeline. If
  `make test-regression` moves, that is a bug in the bundle change, not a behavioral
  change.

Definition of Done per CLAUDE.md: `make lint-py`, `make typecheck`, `make test`,
`make test-regression`, plus `make web-build` **and** `make web-check`.

## 8. Follow-on research — H10 (not part of this build)

The operator's framing goal ("expect a bullish week, so buy dips") requires an inference
the cone cannot supply (§3). Stated falsifiably:

> **H10** — does the partial path observable at hour `h` predict the week's terminal
> direction better than the base rate?

Test: for each hour `h`, compare `P(week closes bull | normalized path at h > unconditional p50)`
against the unconditional bull base rate; then magnitude, not only sign. Route through
`analytics/audit_guard.py` — bootstrap CI clearing a pre-committed bar, **Holm correction
across the family of hours tested** (testing 168 hours and reporting the best is exactly
the multiple-testing trap the guards exist to catch), plus an early/late time split.

H10's verdict — not this spec — decides whether predictive framing reaches the Brief's
language or the F2 card.

Related, separately parked: the operator's LTF-continuation / add-to-winner idea belongs
with **H7** (duration-based trade management) in the exits family, not here.

## 9. Non-goals

- No monthly cone.
- No F2 card wiring (gated on H10).
- No forecast language in UI copy. The Brief block must survive a `/humanizer` pass
  without acquiring implied-prediction phrasing.
- No change to the daily cone's behavior or cached output.
- No new detector. This is a context layer, not a signal — the frozen-detector list in
  memory `todo-master` is untouched.
