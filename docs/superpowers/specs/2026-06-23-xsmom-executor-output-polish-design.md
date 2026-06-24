# XS executor output polish — design

- **Date:** 2026-06-23
- **Status:** approved (brainstorm)
- **Scope:** `tools/xsmom_execute.py` + small `trade/xsmom_executor.py` threading + docs. No engine, no goldens.

## Context

The XS-solo dry-run executor (`make buibui-xsmom-execute`) prints a plan that the
operator reviews daily before any live flip. The current `format_result` iterates
`plan.intents` — the *actionable* orders only — so the ~16 book legs that sit
inside the no-trade band are invisible, and there is no per-leg **leverage**
column at all. The operator ran it on 2026-06-23 and could not answer "what
leverage is this — all 1×?" from the output. This change makes the book's sizing
and leverage legible at a glance.

Observed live shape (2026-06-23, equity $2,302.80, governor 0.50): gross ≈ 2.86×,
net ≈ −1.04×, 24 active legs. Leverage is **vol-targeted** (20% vol × governor)
and **vol-parity** per instrument — not 1× per leg. The `--exchange-leverage` 5×
is only the Binance margin setting, a separate concept from the book's gross.

## Goals

- One scannable Rich table that answers "what leverage, what size, what's about
  to trade" per leg, covering the **whole book** (all active legs), not just the
  legs that need an order this cycle.
- A summary line giving governor, gross/net leverage, leg count, total gross
  notional — the direct answer to "all 1×?" (no: gross ~2.86×, net ~−1×).
- The overlay-blocked case still shows the book table (what got blocked), not
  just abort strings.
- A docs block explaining each column and each output *version*.

## Non-goals

- No `$ADV` / volume column. The capacity audit
  (`docs/audits/2026-06-20-p3-xsmom-capacity.md`) proved the edge is GREEN at the
  operator's ~$10k scale with >10× margin, so per-leg liquidity is never the
  binding constraint at this AUM; a $ADV column would always read "fine" while
  costing a new DB read + threading + a test. Revisit only if AUM climbs toward
  the ~$1M capacity ceiling. (Recorded here so the decision isn't re-litigated.)
- No change to the order-routing, overlay, or sizing logic. Formatting + a small
  data-threading change only.
- No engine / backtest / golden changes.

## Design

### 1. Thread `marks` + `positions` onto `ExecutionResult`

`run_once` already fetches `marks` and `positions` from the adapter but discards
them. Add two trailing fields to the frozen `ExecutionResult` dataclass:

```python
marks: dict[str, float] = field(default_factory=dict)
positions: dict[str, float] = field(default_factory=dict)
```

Trailing + defaulted so the existing positional construction in the test helper
and the kwargs construction in `run_once` both stay valid. `run_once` passes
`marks=marks, positions=positions`.

### 2. Book-centric rows (the core fix)

The table iterates the **book legs**, not the order intents:

- Row set = `book.positions` (all active legs) ∪ close-only symbols (in
  `positions` but not in the book).
- Per row, look up the order action by symbol: `plan.intents` first, else
  `plan.skipped`, else `—`. Every book symbol with a mark+filter appears in
  exactly one of intents/skipped (`build_order_plan` guarantees this), so the
  ACTION column is always populated.
- **Sort:** by `max(|target_notional|, |current_notional|)` descending — keeps
  the biggest exposures (opening, holding, or closing) on top.

Per-leg derived values:

- `current_leverage = current_qty * mark / equity` (signed; `0.0` when flat or
  mark missing).
- `target_leverage = TargetPosition.leverage` (signed, governor-scaled — already
  on the book).
- close-only rows: `target_leverage = 0.0`, no forecast (`—`).

### 3. Columns

| Col | Source | Format |
| --- | --- | --- |
| SYM | book/plan symbol | left, ≤12 |
| SIDE | `TargetPosition.side` | `LONG` green / `SHORT` red / `FLAT` dim |
| CUR→TGT | `current_leverage` → `target_leverage` | signed 2dp, e.g. `−0.10→−0.42` |
| $NOTIONAL | `TargetPosition.notional_usd` | signed `$`, thousands, 0dp |
| Δ$ | `OrderIntent.delta_notional` (intent or skip) | signed `$`, thousands, 0dp |
| MARK | `marks.get(sym)` | adaptive precision (see `_fmt_price`); `—` if absent |
| FCAST | `TargetPosition.forecast` | signed 1dp; `—` for close-only |
| ACTION | order `reason` | `open`/`rebalance`/`close`/`hold (band)`/`skip:<why>` |

`_fmt_price(mark)`: ≥1000 → `,.0f`; ≥1 → `,.2f`; <1 → `.5f` (so BTC `62,140`,
SOL `148.20`, DOGE `0.12340`). `hold (band)` is the friendly label for the
`skip:band` reason; other `skip:*` reasons render verbatim.

### 4. Summary lines

Header (always):

```text
XS execute · {mode} · hold {next_period_date} · equity ${equity:,.2f}
GOV {governor:.2f} · GROSS {gross_leverage:.2f}× · NET {net_leverage:+.2f}× · {active_count} legs · gross ${gross_notional:,.0f}
```

`gross_notional = gross_leverage * capital`. Footer (always):

```text
submitted {n} · skipped {n} · failed {n}
```

plus the existing per-failure lines when `failed` is non-empty.

### 5. Output versions

- **dry-run plan** (default, `mode=dry_run`, `verdict.allowed`): header + table +
  footer; `submitted 0` (dry-run submits nothing — the plan is advisory).
- **BLOCKED** (`not verdict.allowed`, any mode): red `⛔ BLOCKED by overlay`
  banner in the header, the abort reasons listed, **then the full book table**
  (ACTION = what *would* have happened), then footer `submitted 0`.
- **testnet submit** (`mode=testnet`, allowed): identical layout; footer reflects
  real `submitted` / `failed` counts from the adapter.

(`mode=live` reuses the same path; not exercised in this change.)

### 6. Rendering

`format_result(res: ExecutionResult) -> str` keeps its signature and return type.
Internally it builds a `rich.table.Table` (box borders + color, mirroring
`monitor/live_price.py`) and renders to a string via a `rich.console.Console`
writing to an `io.StringIO`, with `width = max(terminal_width, 140)` so the
8-column table never truncates symbols (keeps existing substring-asserting tests
green). `main()` continues to `print(format_result(res))`.

## Files touched

- `tools/xsmom_execute.py` — rewrite `format_result`; add `_fmt_price` and a
  small row-assembly helper.
- `trade/xsmom_executor.py` — add `marks`/`positions` fields to `ExecutionResult`;
  pass them in `run_once`.
- `tests/trade/test_execute_cli.py` — extend: leverage column present, book legs
  inside the band still shown, blocked case renders the table, close row.
- `README.md` (+ a CLAUDE.md `trade/` line) — column legend + the three output
  versions + the "not 1× — gross/net leverage" note.

## Testing

- Existing tests stay green (substring asserts, parser defaults).
- New: a book with an in-band leg appears in the table with `hold (band)`; the
  `CUR→TGT` leverage column renders signed values; a blocked verdict still emits
  the table rows + the abort text; a close-only symbol (in positions, not book)
  renders with ACTION `close`.
- DoD: `make lint-py` · `make typecheck` · `make test` · `make test-regression`
  (goldens must stay UNMOVED — this is formatting only).

## Future

- `$ADV` liquidity column + liquidity sort, gated on AUM approaching the capacity
  ceiling (`load_daily_dollar_volumes` already exists in `analytics/xsmom/`).
