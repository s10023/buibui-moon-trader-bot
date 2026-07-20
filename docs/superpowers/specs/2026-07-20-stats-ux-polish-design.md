# Stats UX polish — live-outcomes honesty + path-cone readout

Date: 2026-07-20
Status: approved (design), pending implementation plan
Scope: `analytics/stats/live_outcomes.py`, `web/api/models/live_outcomes.py`,
`web/api/routers/live_outcomes.py`, `web/ui/src/api.ts`,
`web/ui/src/components/LiveOutcomes.svelte`,
`web/ui/src/components/PathCone.svelte`

## Why

A manual review of the Stats page on 2026-07-20 (immediately after PR #494
merged) produced three misreadings in a row, each traced to presentation
rather than data:

1. **BTCUSDT 1h `bos` short displayed "100% win / +1.328R".** The cell is
   n=12 → 3 win, 0 loss, **9 expired**. `win_rate` is
   `wins / (wins + losses)`, so expired rows leave the denominator, and no
   table column reveals them. Ledger-wide 1399 of 3364 resolved rows are
   expired (41.6%), so the distortion is systemic rather than a single odd
   cell.
2. **ETHUSDT `ema` appeared in "By strategy" but not in
   "By strategy · tf · direction".** At `n≥10` the strategy roll-up passes
   (n=18) while every one of its five cells (9 / 5 / 2 / 1 / 1) is filtered
   out, because `min_n` is applied independently per grouping. Correct
   behaviour, unreadable presentation.
3. **The path cone's right-most x-axis tick renders as `08:0`.** `x(24)`
   evaluates to `W − PR` = 750; the tick is `text-anchor="middle"` and the
   label is ≈27 units wide at `font-size: 10px`, so its centred half needs
   764 of the 760-unit viewBox.

A fourth item is an operator request rather than a defect: hovering the cone
should read out the percentile bands **as prices**, not as ADR multiples.

This spec covers all four. It is presentation-layer only — no detector, no
engine, no schema, no gate change. `make test-regression` goldens must stay
unmoved; any movement means something outside scope was touched.

## Non-goals

- **No change to how any statistic is computed.** `win_rate` keeps its
  `wins / (wins + losses)` definition; `avg_r` keeps averaging `outcome_r`
  over all resolved rows including expired. The fix is disclosure, not
  redefinition.
- **No live→ratings feedback loop.** The related finding — that
  `signal_alert_outcomes` never feeds stars, the Telegram confidence line, or
  the live `min_avg_r` gate — is a separate design question tracked in memory
  `golden-signal-feedback-loop`. It is deliberately out of scope here.
- **No keyboard interaction for the cone.** The chart is `role="img"` today
  and stays non-interactive for assistive tech.

## Part 1 — live-outcomes tables

### 1.1 Backend: expired counts on the strategy roll-up

The per-cell payload already carries `wins` / `losses` / `expired`; the
per-strategy payload does not. Four additive edits:

| Surface | Change |
| --- | --- |
| `LiveOutcomeStrategyRow` (dataclass) | add `wins: int`, `losses: int`, `expired: int` after `n`, mirroring `LiveOutcomeCell`'s field order |
| `by_strategy` SQL | add the three `COUNT(*) FILTER (WHERE outcome = …)` aggregates already used by the cells query |
| `LiveOutcomeStrategyModel` (Pydantic) | add the same three fields |
| `LiveOutcomeStrategyRow` (TS, `api.ts`) | add the same three fields |

The `WHERE`, `HAVING`, and `ORDER BY` clauses are untouched, so the row *set*
is unchanged and only the rows get wider. `compute_live_outcomes` constructs
the dataclass with keyword arguments, so field position is free. No other
module reads this dataclass.

### 1.2 An `exp` column on both tables

Inserted between `n` and `win`, right-aligned, and sortable via the existing
`toggleSort` / `sortRows` machinery (the value is a plain integer and is never
null, so the nulls-last path is not exercised).

Grid tracks:

| Rule | Before | After |
| --- | --- | --- |
| `.lo-row` | `1fr 34px 46px 56px 64px` | `1fr 34px 34px 46px 56px 64px` |
| `.lo-cell-row` | `1fr 34px 34px 30px 44px 56px 60px` | `1fr 34px 34px 30px 34px 44px 56px 60px` |

The By-strategy table is the tight one — it occupies the `0.85fr` side of
`.lo-cols` — so its fit must be screenshot-verified in both layouts: the
two-column desktop grid and the single-column stack below the existing
`max-width: 760px` breakpoint.

### 1.3 Per-table threshold labels

Each block title carries the threshold in the form that actually applies to
it:

```text
By strategy                    By strategy · tf · direction
(n≥10 total)                   (n≥10 per cell)
```

Rendered muted, from the response's own `min_n`, and shown at every value
including `n≥1` — the goal is to teach the semantics, not to warn about one
setting.

### 1.4 Footnote

One muted line beneath the tables:

```text
win% = wins/(wins+losses); expired excluded. avg R is net of costs and
includes expired.
```

The second sentence is load-bearing: it explains how a cell can read 100% win
and still carry an unremarkable avg R.

### 1.5 Empty-cells state

When `by_strategy` returns rows but `cells` is empty — the ETHUSDT `ema` case
at `n≥10` — the right-hand table currently renders a bare header. It gains one
inline muted row:

```text
no cell clears n≥10 — lower min n to see the breakdown
```

## Part 2 — path cone

### 2.1 Axis clip

`PR` changes from `10` to `30`. `x(24)` becomes 730; a centred label needing
≈14 units either side reaches 744 against a 760-unit viewBox, leaving 16 units
of headroom. The plot area narrows by 20 units, which also gives the today-dot
at `x(24)` clearance from the frame.

Rejected alternative: `text-anchor="end"` on the final tick only. It detaches
the label from its own gridline and leaves the dot touching the edge.

### 2.2 Hover: guide line + pinned readout strip

A transparent `<rect>` over the plot area captures `pointermove` and
`pointerleave`. Cursor position converts to a step through the SVG's
`getBoundingClientRect()`, which makes it correct at any rendered width
despite `viewBox` scaling:

```text
vbX  = (clientX − rect.left) / rect.width × W
step = clamp(round((vbX − PL) / (W − PL − PR) × 24), 0, 24)
```

`hoverStep` is `$state<number | null>`, cleared on `pointerleave`. Bands are
indexed 0…23 for steps 1…24, so a hovered step reads `bands[step − 1]`; step 0
is the day's open, where every value is 0 by construction.

A vertical guide line draws at the hovered step from `PT` to `H − PB`.

The readout strip sits between the SVG and the existing `.cone-footer` and
shows, for the hovered hour: the MYT label (reusing the ticks' own
`(s + 8) % 24` mapping), the elapsed `+Nh`, then p90 / p75 / p50 / p25 / p10,
plus today's own value at that hour when the overlay exists.

### 2.3 Prices, and the fallback when there is no overlay

The strip calls the **existing** `px()` helper as `px(v, 1)` with the signed
band value. Reusing it rather than adding a second conversion guarantees the
strip cannot drift from the pivot prices already rendered in the footer.

`px()` returns `null` when `todayPath` is absent, because the conversion needs
`today_open` and `adr14_today`. In that case the strip renders ADR multiples
(`+0.21×`) instead of prices, using the existing `fmtAdr`. Labels are
otherwise identical.

The strip is always present with a fixed `min-height`, showing a muted
"hover for hourly percentiles" at rest, so hovering never changes the card's
height.

### 2.4 Error handling

The `{:else}` branch renders no SVG when `!hasData`, so there is no hover
surface to guard and no new failure mode. Every existing `combo` / `hasData`
guard is unchanged.

## Testing

| Layer | Check |
| --- | --- |
| `analytics/stats/live_outcomes.py` | `by_strategy` rows carry correct `wins`/`losses`/`expired`; an expired-only strategy yields `win_rate is None` with `expired == n`; the row set and every `n` are unchanged from the pre-change query |
| API boundary | response-model parity test asserting the three new fields survive Pydantic serialisation — the M3 lesson, where `extra="ignore"` silently dropped a field at exactly this seam |
| UI | `make web-build` **and** `make web-check` (the former compiles without type-checking — memory `web-build-is-not-a-type-gate`) |
| Visual | screenshots at desktop and below the 760px breakpoint: no clipped final tick, `exp` column fits the `0.85fr` table, strip does not reflow the card on hover |
| Regression | `make test-regression` 3/3 goldens unmoved — no engine change, so movement means out-of-scope drift |

## Delivery

One branch, three sonnet SDD tasks, opus whole-branch review:

| Task | Content |
| --- | --- |
| T1 | backend counts: dataclass + SQL → Pydantic model → router mapping → TS type, with tests |
| T2 | live-outcomes tables: `exp` column, grid tracks, threshold labels, footnote, empty-cells row |
| T3 | cone: `PR` fix, hover capture, guide line, readout strip |

T2 depends on T1's TS type landing first. T3 is independent of both.
