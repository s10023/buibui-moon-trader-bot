# M5 Stats Rework — Live Alert Outcomes UX (design)

**Date:** 2026-07-19 · **Status:** user-approved design (brainstorm session) ·
**Milestone:** Brief-v2 M5, slice 2 · **Sibling:** slice 1 shipped as the Daily
Path Cone (`2026-07-18-m5-price-distribution-cone-design.md`, PR #493)

## 1. Context and goal

The Live Alert Outcomes card is the system's ground-truth surface: the real
result of every Telegram alert the daemon fired, scored from the
`signal_alert_outcomes` ledger. It is the evidence an operator uses to confirm
or kill a strategy cell. Today it renders a fixed roll-up strip plus two static
tables (by strategy; by strategy × tf × direction) with period and min-n
toggles.

Three gaps, all named in the M5 roadmap:

- **No symbol dimension.** The ledger carries `symbol`, but
  `compute_live_outcomes` aggregates it away. The operator cannot ask "how does
  my alert stream perform on SOL specifically" without dropping to SQL.
- **No sorting.** Row order is fixed by the server (`by_strategy` by avg-R
  desc; cells by strategy/tf/direction). Finding the worst-N cell by win rate,
  or the largest-n cell, means reading the whole table.
- **Open alerts are counted but invisible.** The roll-up shows an "open" count
  (15 at time of writing) and nothing else. There is no way to see what is
  live right now, let alone whether it is in the money.

**Goal:** make the card answer three questions it currently cannot — *which
coin*, *ranked by what*, and *what is live right now* — without weakening the
ground-truth guarantees of the numbers already on it.

**Framing:** descriptive operator tool. No change to detection, alerting,
scoring, or any backtest path. Advisory surface only.

## 2. Scope decisions (brainstorm ledger, all user-approved 2026-07-19)

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | Per-ticker drill-down is a **symbol filter chip row**, not a third table and not expandable rows | Answers "how does the whole stream do on this coin"; simplest API surface; composes with sorting |
| D2 | **Everything follows the chip** — roll-up tiles, open list, and both tables all re-slice | No mismatched numbers on screen; the ALL chip restores the global view |
| D3 | Open positions get a **live-marked panel** (mark price, unrealized R, distance to SL/TP), degrading to static columns on price failure | Answers "am I in the money"; one mark-price call covers all symbols |
| D4 | Wiring is **split by cadence** (approach C): tables stay a pure DB read, open list gets its own route | Ground-truth tables never inherit a network dependency; open list polls independently |
| D5 | Sorting is **client-side**, click-to-sort on every column, default = current server order | Payload is already in the browser; no API work, no refetch |
| D6 | Unrealized R is **gross of costs, explicitly labelled** | Resolved `outcome_r` is net (P0b PR-3). Netting an open position means accruing funding to *now* — real work for a number that moves as it is read. Label rather than mislead. |
| D7 | The card is **extracted** from `Stats.svelte` into `LiveOutcomes.svelte` | Stats.svelte is 1797 lines; this rework roughly doubles the card's logic; PathCone set the precedent |

### 2.1 Ledger reality that shaped the design

At time of writing the ledger holds exactly **three symbols** — BTCUSDT (1,210
alerts, 7 open), ETHUSDT (1,128, 3 open), SOLUSDT (1,036, 5 open). Two
consequences:

- The chip row needs **no overflow menu**. Four chips (ALL + 3) fit inline. The
  implementation must still derive chips from data and wrap gracefully, because
  the set grows with `coins.json`, but no dropdown affordance is in scope.
- Per-symbol slices carry n ≈ 1,000, so D2's slicing does **not** fragment the
  tables into underpowered noise. This is what makes D1 worth building now.

## 3. Component architecture

```text
analytics/stats/live_outcomes.py   (pure, DB-only, no network)
  compute_live_outcomes(conn, days, min_n, *, symbol=None) -> LiveOutcomesResult
  open_positions(conn, *, symbol=None)  -> list[OpenPosition]      NEW
  mark_open_positions(rows, marks)      -> list[MarkedOpenPosition] NEW (pure)

web/api/routers/live_outcomes.py
  GET /api/live-outcomes?days&min_n&symbol      (extended — still DB-only)
  GET /api/live-outcomes/open?symbol            NEW (DB + one price call)

web/ui/src/components/LiveOutcomes.svelte       NEW (extracted + extended)
web/ui/src/pages/Stats.svelte                   (card markup + lo-* CSS removed)
```

The split at `mark_open_positions` is deliberate: all arithmetic that could be
wrong lives in a pure function taking a plain `dict[str, float]`, so it is unit
testable without a client, a network, or a DB.

## 4. Data layer

### 4.1 Symbol filter

`compute_live_outcomes` gains a keyword-only `symbol: str | None = None`.
When set, `symbol = ?` joins the WHERE clause of **all three** queries — the
roll-up, the cells, and the per-strategy roll-up (D2).

This is a deliberate departure from the current docstring contract ("the
roll-up is always all-time so `open_no_tp` stays a true integrity gauge"). The
gauge semantics survive: under a symbol filter it reports that symbol's
integrity, and `ALL` (the default, `symbol=None`) is byte-identical to today's
behaviour. The docstring must be updated to say so.

The `days` window continues to apply only to the tables, not the roll-up.
Symbol and period are independent axes.

### 4.2 Chip list

`LiveOutcomesResult` gains `symbols: list[LiveOutcomeSymbolRow]` where each row
is `(symbol: str, n: int)`.

Computed **globally and all-time** — unaffected by both `symbol` and `days`.
Chips must not disappear when one is selected, and must not churn when the
period toggle changes. Ordered by `n` desc, then symbol asc for stability.

### 4.3 Open positions (ledger side)

```python
@dataclass
class OpenPosition:
    signal_id: str
    symbol: str
    strategy: str
    tf: str
    direction: str
    fired_at_ms: int
    entry_price: float | None
    sl_price: float | None
    tp_price: float | None
```

`open_positions(conn, *, symbol=None)` selects `WHERE outcome IS NULL`, ordered
by `fired_at_ms` desc. Pure DB read — no price, no network, no clock.

### 4.4 Marking (pure)

```python
@dataclass
class MarkedOpenPosition:
    position: OpenPosition
    mark: float | None
    unrealized_r: float | None   # GROSS of costs (D6)
    dist_sl_pct: float | None
    dist_tp_pct: float | None
```

`mark_open_positions(rows, marks: Mapping[str, float]) -> list[MarkedOpenPosition]`

Given `risk = abs(entry - sl)`:

- long: `unrealized_r = (mark - entry) / risk`
- short: `unrealized_r = (entry - mark) / risk`
- `dist_sl_pct = abs(mark - sl) / mark`, `dist_tp_pct = abs(mark - tp) / mark`

Positive `unrealized_r` always means in profit, both directions.

Every derived field independently degrades to `None` rather than raising:

| Condition | Result |
| --- | --- |
| symbol absent from `marks` | `mark`, `unrealized_r`, both distances = `None` |
| `entry_price` or `sl_price` is NULL | `unrealized_r` = `None` |
| `risk == 0` (entry == sl) | `unrealized_r` = `None` — division-by-zero guard |
| `tp_price` is NULL | `dist_tp_pct` = `None` |
| `mark == 0` | both distances = `None` |

The function never raises and never consults a clock, so its tests are exact.

## 5. API

### 5.1 `GET /api/live-outcomes` (extended)

Adds an optional `symbol` query param, passed through to
`compute_live_outcomes`. Response gains `symbols: list[LiveOutcomeSymbolModel]`.

Unknown or misspelled symbol returns a **zero roll-up with empty tables**, not
404 — consistent with the existing "empty ledger is a valid state" contract.
SQL is parameterized, so no validation beyond the existing type coercion is
required.

Remains a pure DuckDB read: no client dependency, works offline and on the
OKX path.

### 5.2 `GET /api/live-outcomes/open` (new)

```json
{
  "symbol": "BTCUSDT",
  "marks_ok": true,
  "marked_at_ms": 1784452800000,
  "positions": [
    {
      "signal_id": "...", "symbol": "BTCUSDT", "strategy": "order_block",
      "tf": "1h", "direction": "long", "fired_at_ms": 1784437440000,
      "entry_price": 64120.0, "sl_price": 63400.0, "tp_price": 65560.0,
      "mark": 64590.0, "unrealized_r": 0.653,
      "dist_sl_pct": 0.0184, "dist_tp_pct": 0.0150
    }
  ]
}
```

Handler sequence: `open_positions(db, symbol=...)` → one
`client.futures_mark_price()` call (returns every symbol in a single request,
as a list of dicts carrying `symbol` and `markPrice`) → fold into
`dict[str, float]`, skipping any entry whose `markPrice` will not parse as a
float → `mark_open_positions(...)`.

**The price call is wrapped in a bare try/except.** Any failure sets
`marks_ok=false` and passes an empty `marks` dict, so every row still returns
with null price columns. The route never 5xxs because Binance is unreachable,
rate-limited, geo-blocked, or slow. `marked_at_ms` is the server clock at the
time of the price call, so the UI can age the marks.

Uses the existing `get_db` and `get_client` dependencies; `get_db` already
raises 503 on the daemon's write lock, which the UI handles (§7).

## 6. UI

### 6.1 Extraction

`LiveOutcomes.svelte` receives the card's existing markup, its `lo-*` CSS
block, `CARD_HELP.liveOutcomes`, and the `rbar` snippet. It owns its own data
fetching (the card is already independent of the page's symbol/days picker).
`Stats.svelte` is left with a single `<LiveOutcomes />` mount.

Load `/frontend-design` before touching CSS. All new styling reuses the
existing dark-minimal terminal vocabulary; no new visual language.

### 6.2 Chip row

Rendered under the roll-up strip: `ALL` plus one chip per `symbols` entry. Each
per-symbol chip shows that symbol's count; the `ALL` chip shows the sum.
Selection refetches `/api/live-outcomes?symbol=`, and — when
the open panel is expanded — `/api/live-outcomes/open?symbol=` too. Chips wrap
on narrow viewports.

### 6.3 Sorting

Both tables get click-to-sort headers. First click sorts descending, second
ascending, and the active column carries a ▾/▴ indicator. Sorting is a pure
client-side derivation over the fetched arrays — no refetch, no server round
trip.

`null` values (win-rate and avg-R can both be null) always sort **last**,
regardless of direction, so an unresolved cell never masquerades as the best or
worst row.

Default order on load and after any refetch is the server's order, unchanged
from today.

### 6.4 Open panel

The "open" roll-up tile becomes a disclosure button (`▸`/`▾`) showing the count.
Expanding fetches `/api/live-outcomes/open` and renders one row per position:
symbol, strategy, tf, direction, age, entry, mark, unrealized R, distance to
SL, distance to TP.

Age is derived from `fired_at_ms`. `Stats.svelte` currently owns a minute-ticking
MYT clock; the extracted component must own its own ticker rather than reach
back into the page, and must clear it on unmount alongside the poll timer.

While expanded it polls every **30 seconds**; collapsing stops the timer, and
the component must clear it on unmount.

The unrealized-R column header is marked gross (D6), and the card help text
gains a sentence contrasting it with the net `outcome_r` in the tables.

When `marks_ok` is false the price-derived columns render `—` under an inline
note that live prices are unavailable; the ledger columns still render.

Zero open positions renders an empty-state line, not a bare table.

## 7. Error handling summary

| Failure | Behaviour |
| --- | --- |
| Table fetch fails | Existing `loError` banner — unchanged |
| Price call fails | 200 with `marks_ok=false`; ledger columns render, price columns `—` plus a note |
| DuckDB 503 (daemon holds write lock) | Keep last good data, mark it stale; the 30s poll must **not** flash an error banner each cycle |
| Ledger empty | Existing "no alerts fired yet" message — unchanged |
| No resolved trades in window | Existing "widen the period" message — unchanged |
| Zero open positions | Empty-state line inside the expanded panel |

The 503 case is not hypothetical: it was hit repeatedly during the 2026-07-19
smoke session while `make buibui-signal-watch` held the lock. A polling panel
that surfaces a red banner on every transient lock would train the operator to
ignore the card.

## 8. Testing

**`tests/test_live_outcomes_stats.py` (extend)**

- symbol filter slices roll-up, cells, and by_strategy together
- `symbol=None` is identical to pre-change output (no-regression guard)
- chip list is global: unchanged under both a symbol filter and a days window
- unknown symbol returns a zero roll-up, does not raise
- `open_positions` returns only `outcome IS NULL`, newest first, and honours
  the symbol filter

**New pure tests for `mark_open_positions`**

- long and short unrealized-R sign and magnitude
- symbol missing from marks → all derived fields `None`
- `entry == sl` → `unrealized_r is None`, no `ZeroDivisionError`
- `tp_price` NULL → `dist_tp_pct is None`, other fields still computed
- empty input → empty output

**`tests/test_web_live_outcomes.py` (extend)**

- `/open` happy path with a stubbed client
- stubbed client **raising** → 200, `marks_ok=false`, positions still present
- `symbol` param plumbed on both routes
- `/api/live-outcomes` response includes the `symbols` list

**Gate:** `make lint-py`, `make typecheck`, `make test`, and `make web-build`.
`make test-regression` goldens must stay unmoved — this touches no backtest
path, so any movement is a bug in the change.

## 9. Out of scope / follow-ups

- **Net unrealized R** (D6's rejected alternative) — revisit only if the
  operator starts trading these alerts directly rather than reading them.
- **Per-(symbol × strategy × tf × direction) expansion** — the rejected D1
  option C. At n ≈ 1,000 per symbol the four-way split is still thin; revisit
  when the ledger has more symbols and depth.
- **Symbol chip overflow menu** — unnecessary at three symbols; needed if
  `coins.json` grows past roughly eight.
- **Sorting the open panel** — deliberately unsorted beyond newest-first; it is
  a monitoring list, not an evidence table.
- **The cone's clipped right-edge axis label** (`08:0`), observed in the same
  smoke session — unrelated to this card, tracked separately.

## 10. Effort estimate

Four tasks, sonnet-executable from this spec:

1. Data layer — symbol filter, chip list, `open_positions`,
   `mark_open_positions` + their tests
2. API layer — extended route, new `/open` route, models + their tests
3. UI extraction — card out of `Stats.svelte` into `LiveOutcomes.svelte`, no
   behaviour change (a clean checkpoint: the page must look identical)
4. UI features — chips, sorting, open panel, help-text update

Task 3 is deliberately a pure move so that any visual regression is isolated
from the feature work in task 4.
