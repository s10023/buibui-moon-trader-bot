# Stats UX Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Stats page's Live Alert Outcomes card impossible to misread
(expired counts visible, thresholds labelled) and let the Daily Path Cone be
hovered to read its percentile bands as prices.

**Architecture:** Three independent slices. T1 widens the per-strategy
roll-up with the outcome counts the per-cell payload already carries —
dataclass → SQL → Pydantic model → router → TypeScript type. T2 renders those
counts plus per-table threshold labels and a footnote in
`LiveOutcomes.svelte`. T3 fixes the cone's clipped axis label and adds a
pointer-driven guide line + readout strip in `PathCone.svelte`. No detector,
engine, schema, or gate change anywhere.

**Tech Stack:** Python 3.11 + DuckDB + FastAPI/Pydantic v2 (backend), Svelte 5
runes + TypeScript + Vite (frontend), pytest (backend tests), svelte-check
(frontend type gate).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-20-stats-ux-polish-design.md`. Read it
  before starting; it holds the reasoning behind every decision here.
- **Every function needs type annotations including the return type** (mypy
  runs in strict mode, `disallow_untyped_defs = true`). Test functions need
  `-> None`.
- Python gate after any `.py` change: `make lint-py` (ruff format + lint),
  `make typecheck` (mypy strict), `make test`. State each result plainly. If a
  step fails or is skipped, say so — never claim green without running it.
- Frontend gate after any change under `web/ui/`: `make web-check` **and**
  `make web-build`. `web-build` compiles **without** type-checking, so it is
  not a type gate on its own — 12 svelte-check errors have built green before.
- `make test-regression` must stay 3/3 goldens unmoved. There is no engine
  change in this plan, so any golden movement means something out of scope was
  touched — stop and report rather than regenerating.
- **No statistic is redefined.** `win_rate` keeps its `wins / (wins + losses)`
  meaning; `avg_r` keeps averaging `outcome_r` across all resolved rows
  including expired. This plan adds disclosure only.
- UI style: dark, minimal, matching the surrounding card. Timestamps and hour
  labels are MYT (UTC+8, no DST).
- There is **no frontend unit-test runner** in this repo (`web/ui/package.json`
  has only dev/build/preview). T2 and T3 are verified by svelte-check, a
  production build, and screenshots — not by unit tests. Do not add a test
  runner as part of this work.
- Run tests in the **foreground**. Do not background a `make test` run and
  report before it finishes.

---

### Task 1: Outcome counts on the per-strategy roll-up

**Files:**

- Modify: `analytics/stats/live_outcomes.py:57-65` (dataclass),
  `analytics/stats/live_outcomes.py:218-243` (SQL + construction)
- Modify: `web/api/models/live_outcomes.py:28-32`
- Modify: `web/api/routers/live_outcomes.py:69-76`
- Modify: `web/ui/src/api.ts:598-603`
- Test: `tests/test_live_outcomes_stats.py`, `tests/test_web_live_outcomes.py`

**Interfaces:**

- Consumes: nothing from earlier tasks (this is the first task).
- Produces: `LiveOutcomeStrategyRow` and its TypeScript twin
  `LiveOutcomeStrategyRow` both gain `wins: int/number`, `losses: int/number`,
  `expired: int/number`, positioned after `n` and before `win_rate`. Task 2
  reads `s.expired` off the TypeScript interface.

- [ ] **Step 1: Write the failing tests**

Append both tests to `tests/test_live_outcomes_stats.py`. The existing
`_insert` helper defaults to `strategy="bos"`, `tf="1h"`,
`direction="short"`, `symbol="BTCUSDT"`, so only the varying fields are passed.

```python
def test_by_strategy_carries_outcome_counts() -> None:
    conn = _conn()
    _insert(conn, "w1", strategy="bos", outcome="win", outcome_r=1.5)
    _insert(conn, "l1", strategy="bos", outcome="loss", outcome_r=-1.0)
    _insert(conn, "e1", strategy="bos", outcome="expired", outcome_r=0.2)
    _insert(conn, "e2", strategy="bos", outcome="expired", outcome_r=-0.1)

    res = compute_live_outcomes(conn, days=0, min_n=1)
    row = res.by_strategy[0]
    assert row.strategy == "bos"
    assert row.n == 4
    assert row.wins == 1
    assert row.losses == 1
    assert row.expired == 2
    # win% still excludes expired: 1 / (1 + 1)
    assert row.win_rate == 0.5
    # avg_r still spans every resolved row: (1.5 - 1.0 + 0.2 - 0.1) / 4
    assert row.avg_r is not None
    assert abs(row.avg_r - 0.15) < 1e-9


def test_by_strategy_expired_only_has_null_win_rate() -> None:
    conn = _conn()
    _insert(conn, "e1", strategy="fvg", outcome="expired", outcome_r=0.0)
    _insert(conn, "e2", strategy="fvg", outcome="expired", outcome_r=0.0)

    res = compute_live_outcomes(conn, days=0, min_n=1)
    row = res.by_strategy[0]
    assert row.n == 2
    assert row.wins == 0
    assert row.losses == 0
    assert row.expired == 2
    assert row.win_rate is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/test_live_outcomes_stats.py -k "carries_outcome_counts or expired_only" -v
```

Expected: FAIL with `AttributeError: 'LiveOutcomeStrategyRow' object has no
attribute 'wins'`.

- [ ] **Step 3: Widen the dataclass**

Replace the `LiveOutcomeStrategyRow` definition in
`analytics/stats/live_outcomes.py`:

```python
@dataclass
class LiveOutcomeStrategyRow:
    """Per-strategy resolved-trade roll-up across TFs and directions."""

    strategy: str
    n: int
    wins: int
    losses: int
    expired: int
    win_rate: float | None
    avg_r: float | None
```

- [ ] **Step 4: Add the counts to the SQL and the construction**

Replace the `strat_rows` query and the `by_strategy` comprehension. `WHERE`,
`HAVING`, and `ORDER BY` are untouched, so the row set does not change — only
the rows get wider.

```python
    strat_rows = conn.execute(
        f"""
        SELECT
          strategy,
          COUNT(*) AS n,
          COUNT(*) FILTER (WHERE outcome='win')     AS wins,
          COUNT(*) FILTER (WHERE outcome='loss')    AS losses,
          COUNT(*) FILTER (WHERE outcome='expired') AS expired,
          AVG(CASE WHEN outcome='win'  THEN 1.0
                   WHEN outcome='loss' THEN 0.0 END) AS win_rate,
          AVG(outcome_r)                            AS avg_r
        FROM signal_alert_outcomes
        {where}
        GROUP BY strategy
        HAVING COUNT(*) >= ?
        ORDER BY avg_r DESC NULLS LAST, strategy
        """,
        (*params, min_n),
    ).fetchall()

    by_strategy = [
        LiveOutcomeStrategyRow(
            strategy=str(s),
            n=int(n),
            wins=int(wins),
            losses=int(losses),
            expired=int(expired),
            win_rate=None if win_rate is None else float(win_rate),
            avg_r=None if avg_r is None else float(avg_r),
        )
        for (s, n, wins, losses, expired, win_rate, avg_r) in strat_rows
    ]
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
poetry run pytest tests/test_live_outcomes_stats.py -v
```

Expected: PASS, including the pre-existing tests (`min_n` filtering and
`avg_r DESC` ordering must be unchanged).

- [ ] **Step 6: Write the failing API parity test**

Append to `tests/test_web_live_outcomes.py`. The module's `_seed_conn` inserts
one `bos` win (`outcome_r` 1.5), one `bos` loss (−1.0), and one unresolved
`ema` row, so the resolved `bos` roll-up is n=2 / 1 win / 1 loss / 0 expired.

```python
def test_by_strategy_exposes_outcome_counts() -> None:
    """The new counts must survive the Pydantic boundary.

    A response model missing these fields drops them silently rather than
    erroring — the M3 failure mode where ``extra="ignore"`` swallowed a whole
    block at exactly this seam.
    """
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes?days=0&min_n=1")
        assert resp.status_code == 200
        row = resp.json()["by_strategy"][0]
        assert row["strategy"] == "bos"
        assert row["n"] == 2
        assert row["wins"] == 1
        assert row["losses"] == 1
        assert row["expired"] == 0
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)
```

- [ ] **Step 7: Run it to verify it fails**

```bash
poetry run pytest tests/test_web_live_outcomes.py::test_by_strategy_exposes_outcome_counts -v
```

Expected: FAIL with `KeyError: 'wins'` — the serialised row lacks the field.

- [ ] **Step 8: Widen the Pydantic model**

In `web/api/models/live_outcomes.py`:

```python
class LiveOutcomeStrategyModel(BaseModel):
    strategy: str
    n: int
    wins: int
    losses: int
    expired: int
    win_rate: float | None
    avg_r: float | None
```

- [ ] **Step 9: Map the fields in the router**

In `web/api/routers/live_outcomes.py`, replace the `by_strategy` comprehension
body:

```python
            LiveOutcomeStrategyModel(
                strategy=s.strategy,
                n=s.n,
                wins=s.wins,
                losses=s.losses,
                expired=s.expired,
                win_rate=s.win_rate,
                avg_r=s.avg_r,
            )
```

- [ ] **Step 10: Run the API test to verify it passes**

```bash
poetry run pytest tests/test_web_live_outcomes.py -v
```

Expected: PASS, all tests in the file.

- [ ] **Step 11: Widen the TypeScript interface**

In `web/ui/src/api.ts`, replace the `LiveOutcomeStrategyRow` interface. Field
order mirrors `LiveOutcomeCell` directly above it:

```typescript
export interface LiveOutcomeStrategyRow {
  strategy: string;
  n: number;
  wins: number;
  losses: number;
  expired: number;
  win_rate: number | null;
  avg_r: number | null;
}
```

- [ ] **Step 12: Run the full gate**

```bash
make lint-py && make typecheck && make test && make web-check
```

Expected: ruff clean, mypy strict clean, full suite green with 3 new tests,
svelte-check 0 errors. Report each result explicitly.

- [ ] **Step 13: Commit**

```bash
git add analytics/stats/live_outcomes.py web/api/models/live_outcomes.py \
  web/api/routers/live_outcomes.py web/ui/src/api.ts \
  tests/test_live_outcomes_stats.py tests/test_web_live_outcomes.py
git commit -m "feat(stats): expose win/loss/expired counts on the live-outcomes strategy roll-up"
```

---

### Task 2: Live-outcomes tables — exp column, thresholds, footnote

**Files:**

- Modify: `web/ui/src/components/LiveOutcomes.svelte` (markup ~289-359, CSS
  ~511-528)

**Interfaces:**

- Consumes: `LiveOutcomeStrategyRow.expired` and `LiveOutcomeCell.expired`
  from Task 1's TypeScript interface. `LiveOutcomeCell` already carried
  `expired` before this plan; only the strategy row is new.
- Produces: nothing consumed by Task 3 (the two tasks are independent).

Existing machinery to reuse rather than reinvent: `toggleSort(current, key)`
and `sortRows(rows, sort)` are generic over a `string` key, so the new column
needs no type-union edit. `liveOutcomes.min_n` is already on the response.
`.lo-note` and `.lo-msg` are existing muted-text classes.

- [ ] **Step 1: Add the exp header + cell to the By-strategy table**

In the "By strategy" block, insert this button between the existing `n` and
`win` header buttons:

```svelte
            <button class="lo-th num" onclick={() => (stratSort = toggleSort(stratSort, "expired"))}>
              exp{stratSort?.key === "expired" ? (stratSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
```

And insert this span between the `{s.n}` and `win_rate` spans in the row body:

```svelte
              <span class="num muted">{s.expired}</span>
```

- [ ] **Step 2: Add the exp header + cell to the cells table**

In the "By strategy · tf · direction" block, insert between the `n` and `win`
header buttons:

```svelte
            <button class="lo-th num" onclick={() => (cellSort = toggleSort(cellSort, "expired"))}>
              exp{cellSort?.key === "expired" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
```

And between the `{c.n}` and `win_rate` spans in the row body:

```svelte
              <span class="num muted">{c.expired}</span>
```

- [ ] **Step 3: Widen both grid track lists**

In the `<style>` block, replace the two rules. Each gains one `34px` track in
the position matching where the span was inserted.

```css
  .lo-row {
    display: grid;
    grid-template-columns: 1fr 34px 34px 46px 56px 64px;
```

```css
  .lo-cell-row {
    grid-template-columns: 1fr 34px 34px 30px 34px 44px 56px 60px;
  }
```

Keep every other declaration inside those rules exactly as it is — only the
`grid-template-columns` line changes.

- [ ] **Step 4: Label each table with its own threshold**

Replace the two `.lo-block-title` divs:

```svelte
        <div class="lo-block-title">
          By strategy <span class="lo-title-note">(n≥{liveOutcomes.min_n} total)</span>
        </div>
```

```svelte
        <div class="lo-block-title">
          By strategy · tf · direction
          <span class="lo-title-note">(n≥{liveOutcomes.min_n} per cell)</span>
        </div>
```

Add the supporting class to the `<style>` block, immediately after the
existing `.lo-block-title` rule so the cascade order is base-then-modifier
(placing an override before its base rule is how the `.lo-open-row` columns
silently collided in PR #494):

```css
  .lo-title-note {
    font-size: 9px;
    font-weight: 400;
    letter-spacing: 0.02em;
    text-transform: none;
    color: #777;
  }
```

`text-transform: none` and the matching `9px` are both deliberate: the parent
`.lo-block-title` is `font-size: 9px` with `text-transform: uppercase`, so
without these the note would render larger than the title it annotates and
read "(N≥10 TOTAL)".

- [ ] **Step 5: Add the empty-cells row**

Immediately after the `{#each sortedCells as c}…{/each}` loop and still inside
the cells `.lo-table` div:

```svelte
          {#if sortedCells.length === 0}
            <div class="lo-msg muted">
              no cell clears n≥{liveOutcomes.min_n} — lower min n to see the breakdown
            </div>
          {/if}
```

- [ ] **Step 6: Add the semantics footnote**

Immediately after the closing `</div>` of `.lo-cols`, still inside the
`{:else}` branch:

```svelte
    <div class="lo-note muted">
      win% = wins/(wins+losses); expired excluded. avg R is net of costs and includes expired.
    </div>
```

- [ ] **Step 7: Type-check and build**

```bash
make web-check && make web-build
```

Expected: svelte-check 0 errors, 0 warnings; Vite build succeeds. Both are
required — `web-build` alone does not type-check.

- [ ] **Step 8: Verify visually at both layouts**

Start the API and UI, open the Stats page, and screenshot the Live Alert
Outcomes card:

```bash
make buibui-web    # separate shell; then load the Stats page
```

Confirm all five, and paste the screenshots into your report:

1. Both tables show an `exp` column between `n` and `win`.
2. The By-strategy table (the narrower `0.85fr` side) is not clipped or
   overlapping — this is the tightest fit in the card.
3. Below 760px viewport width the tables stack to one column and still fit.
4. Clicking `exp` sorts by it, descending first, and clicking again reverses.
5. With the `n≥10` chip active on a symbol whose cells are all thin (ETHUSDT
   is the known case), the right table shows the "no cell clears n≥10" row
   rather than a bare header.

- [ ] **Step 9: Commit**

```bash
git add web/ui/src/components/LiveOutcomes.svelte
git commit -m "feat(ui): expired column, per-table thresholds, and win% footnote on live outcomes"
```

---

### Task 3: Path cone — axis clip fix and hover readout

**Files:**

- Modify: `web/ui/src/components/PathCone.svelte` (geometry ~40-46, script
  ~96-110, markup ~140-196, CSS ~267+)

**Interfaces:**

- Consumes: nothing from Tasks 1 and 2 — this task is independent and may be
  implemented in parallel.
- Produces: nothing consumed downstream.

Existing helpers to reuse rather than duplicate: `px(mag, side)` converts an
ADR-relative magnitude to a price and returns `null` when `todayPath` is
absent; `fmtPx(p)` formats a price or `—`; `fmtAdr(v)` formats a signed ADR
multiple. `combo.bands[i]` is a 5-element row ordered `[p10, p25, p50, p75,
p90]` for elapsed step `i + 1`.

- [ ] **Step 1: Fix the clipped final axis label**

Change the right-padding constant only:

```typescript
  const PR = 30;
```

Why 30: `x(24)` is `W - PR`, so the final tick moves from 750 to 730. The
label "08:00" is ≈27 viewBox units wide at `font-size: 10px` and is
`text-anchor="middle"`, so it needs ≈14 units to its right — 744 against a
760-unit viewBox, leaving 16 units spare. At `PR = 10` it needed 764 and got
clipped to "08:0".

- [ ] **Step 2: Add hover state and the pointer handler**

Add to the `<script>` block, after the `highInNow` derived:

```typescript
  let hoverStep = $state<number | null>(null);

  // Pointer x → elapsed step. Reading the SVG's client rect keeps this correct
  // at any rendered width, since the chart scales through its viewBox.
  function onPointerMove(ev: PointerEvent): void {
    const svg = (ev.currentTarget as SVGGraphicsElement).ownerSVGElement;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    if (rect.width === 0) return;
    const vbX = ((ev.clientX - rect.left) / rect.width) * W;
    const raw = Math.round(((vbX - PL) / (W - PL - PR)) * 24);
    hoverStep = Math.max(0, Math.min(24, raw));
  }
```

- [ ] **Step 3: Derive the hovered row and its formatters**

Add directly below the handler:

```typescript
  // Bands are indexed 0…23 for steps 1…24; step 0 is the open, where every
  // percentile is 0 by construction.
  const hoverRow = $derived.by(() => {
    if (hoverStep === null || !combo || combo.n === 0) return null;
    const step = hoverStep;
    const vals = step === 0 ? [0, 0, 0, 0, 0] : combo.bands[step - 1];
    // A short bands array would leave vals undefined and crash on vals[4].
    if (!vals) return null;
    const today =
      todayPath && step >= 1 && step <= todayPath.points.length
        ? todayPath.points[step - 1]
        : null;
    return { step, vals, today };
  });

  // Price when the live overlay gives us today_open + adr14; the cone's native
  // ADR multiple otherwise.
  const fmtVal = (v: number): string => {
    const p = px(v, 1);
    return p === null ? fmtAdr(v) + "×" : fmtPx(p);
  };
  const hourLabel = (step: number): string =>
    String((step + 8) % 24).padStart(2, "0") + ":00";
```

- [ ] **Step 4: Draw the guide line**

Inside the `<svg>`, immediately after the `{#if todayD && todayPath}` block
and before the `{#each ticks as t}` loop:

```svelte
      {#if hoverStep !== null}
        <line
          x1={x(hoverStep)}
          y1={PT}
          x2={x(hoverStep)}
          y2={H - PB}
          class="cone-guide"
        />
      {/if}
```

- [ ] **Step 5: Add the transparent capture rect**

As the **last** child of the `<svg>`, after the y-axis label `<text>`
elements. SVG paint order means later elements sit on top, so placing it last
guarantees it receives the pointer events:

```svelte
      <rect
        x={PL}
        y={PT}
        width={W - PL - PR}
        height={H - PT - PB}
        fill="transparent"
        onpointermove={onPointerMove}
        onpointerleave={() => (hoverStep = null)}
      />
```

- [ ] **Step 6: Add the readout strip**

Between the closing `</svg>` and the opening `<div class="cone-footer">`. This
position puts the strip inside the existing `{#if hasData && combo}` branch,
so when there is no data the SVG, the capture rect, and the strip are all
absent together — there is no hover surface to guard and no new failure mode
(spec §2.4):

```svelte
    <div class="cone-readout">
      {#if hoverRow}
        <span class="cone-readout-hour"
          >{hourLabel(hoverRow.step)} (+{hoverRow.step}h)</span
        >
        <span
          >p90 {fmtVal(hoverRow.vals[4])} · p75 {fmtVal(hoverRow.vals[3])} · p50
          {fmtVal(hoverRow.vals[2])} · p25 {fmtVal(hoverRow.vals[1])} · p10
          {fmtVal(hoverRow.vals[0])}</span
        >
        {#if hoverRow.today !== null}
          <span class="cone-readout-today"
            >today {fmtVal(hoverRow.today)} ({fmtAdr(hoverRow.today)}×)</span
          >
        {/if}
      {:else}
        <span class="cone-muted">hover the chart for hourly percentiles</span>
      {/if}
    </div>
```

- [ ] **Step 7: Add the styles**

Append to the `<style>` block. `min-height` is load-bearing: without it the
card's height jumps as the strip fills and empties on hover.

```css
  .cone-guide {
    stroke: #666;
    stroke-width: 1;
    stroke-dasharray: 3 3;
  }
  .cone-readout {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.3rem 1rem;
    min-height: 2.4em;
    padding-top: 4px;
    font-size: 0.78rem;
    color: #bbb;
  }
  .cone-readout-hour {
    color: #e5e7eb;
    font-variant-numeric: tabular-nums;
  }
  .cone-readout-today {
    color: #60a5fa;
  }
```

- [ ] **Step 8: Type-check and build**

```bash
make web-check && make web-build
```

Expected: svelte-check 0 errors, 0 warnings; Vite build succeeds.

- [ ] **Step 9: Verify visually**

Load the Stats page and screenshot the cone. Confirm all six, pasting
screenshots into your report:

1. The right-most x-axis label reads "08:00" in full, not "08:0".
2. Hovering draws a dashed vertical guide at the hovered hour.
3. The strip shows the hour, `+Nh`, and five percentile **prices** for a
   symbol that has a live overlay.
4. The strip shows ADR multiples (e.g. `+0.21×`) instead of prices for a
   symbol with no overlay — pick a symbol whose 1h data is stale, which the
   PR #494 smoke already identified as a reachable state.
5. Moving the pointer off the chart clears the guide and restores the muted
   hint, and the card's height never changes.
6. p50 at the last hour is consistent with the existing pivots line in the
   footer — both go through `px()`, so a mismatch means the wrong band index.

- [ ] **Step 10: Commit**

```bash
git add web/ui/src/components/PathCone.svelte
git commit -m "feat(ui): path-cone hover readout with prices; fix clipped final axis tick"
```

---

## Final gate (after all three tasks)

- [ ] **Run the full gate and report each line**

```bash
make lint-py && make typecheck && make test && make test-regression && make lint-md && make web-check && make web-build
```

Expected: ruff clean · mypy strict clean · full suite green (3 tests added in
T1) · **regression 3/3 goldens unmoved** · markdownlint 0 issues ·
svelte-check 0 errors · Vite build succeeds.

If `test-regression` moves, do **not** run `make regression-update`. Nothing
in this plan touches the engine, so movement means an out-of-scope edit — stop
and report it.

- [ ] **Docs sweep**

This work changes the Live Alert Outcomes card's columns and the cone's
interactivity, so check `.claude/context/web.md` for the live-outcomes card
description and `PathCone` notes and update them if they now disagree with the
UI. Use the `/post-branch` skill after the PR is opened.
