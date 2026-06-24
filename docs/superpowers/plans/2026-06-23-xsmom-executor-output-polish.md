# XS executor output polish — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the XS-solo dry-run executor print a book-centric Rich table (one row per active leg, with per-leg leverage) plus a gross/net-leverage summary, so the operator can answer "what leverage, what size, what's about to trade" at a glance.

**Architecture:** Thread the already-fetched `marks`/`positions` onto `ExecutionResult`, then rewrite `tools/xsmom_execute.py::format_result` to render a Rich table over the book legs (merging in each leg's order action) and keep its `-> str` signature by rendering into a captured string. No engine, routing, overlay, or golden changes.

**Tech Stack:** Python 3.11, `rich` (already used in `monitor/live_price.py`), pytest, frozen dataclasses, mypy strict.

Spec: `docs/superpowers/specs/2026-06-23-xsmom-executor-output-polish-design.md`

---

## Task 1: Thread `marks` + `positions` onto `ExecutionResult`

**Files:**

- Modify: `trade/xsmom_executor.py` (the `ExecutionResult` dataclass + the `return` in `run_once`)
- Test: `tests/trade/test_xsmom_executor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/trade/test_xsmom_executor.py` (reuses the existing `_seed`, `_FakeAdapter`, `_limits` helpers in that file):

```python
def test_run_once_threads_marks_and_positions(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(
        equity=10_000.0,
        positions={"AAAUSDT": 2.0},
        marks=dict.fromkeys(syms, 100.0),
    )
    res = run_once(
        conn,
        adapter,
        ForecastConfig(),
        syms,
        _limits(),
        no_trade_band_frac=0.0,
        exchange_leverage=5,
        state_path=tmp_path / "s.json",
        now=pd.Timestamp("2022-02-05", tz="UTC"),
    )
    assert res.marks["AAAUSDT"] == 100.0
    assert res.positions["AAAUSDT"] == 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_xsmom_executor.py::test_run_once_threads_marks_and_positions -v`
Expected: FAIL — `AttributeError: 'ExecutionResult' object has no attribute 'marks'`.

- [ ] **Step 3: Add the fields to the dataclass**

In `trade/xsmom_executor.py`, change the import line:

```python
from dataclasses import dataclass, field
```

and add two trailing fields to `ExecutionResult` (after `mode: str`):

```python
@dataclass(frozen=True)
class ExecutionResult:
    verdict: OverlayVerdict
    plan: OrderPlan
    book: TargetBook
    submitted: list[OrderIntent]
    failed: list[tuple[OrderIntent, str]]
    equity: float
    mode: str
    marks: dict[str, float] = field(default_factory=dict)
    positions: dict[str, float] = field(default_factory=dict)
```

- [ ] **Step 4: Pass them in `run_once`**

In `trade/xsmom_executor.py`, update the final `return ExecutionResult(...)` to include the already-computed locals:

```python
    return ExecutionResult(
        verdict=verdict,
        plan=plan,
        book=book,
        submitted=submitted,
        failed=failed,
        equity=equity,
        mode=adapter.mode,
        marks=marks,
        positions=positions,
    )
```

(`marks` and `positions` are already fetched earlier in `run_once`; this only surfaces them.)

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_xsmom_executor.py -v`
Expected: PASS (new test + all existing executor tests stay green — the new fields are defaulted so nothing else changes).

- [ ] **Step 6: Commit**

```bash
git add trade/xsmom_executor.py tests/trade/test_xsmom_executor.py
git commit -m "feat(trade): surface marks + positions on ExecutionResult"
```

---

## Task 2: `_fmt_price` helper

**Files:**

- Modify: `tools/xsmom_execute.py`
- Test: `tests/trade/test_execute_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/trade/test_execute_cli.py`:

```python
def test_fmt_price_adaptive_precision() -> None:
    from tools.xsmom_execute import _fmt_price

    assert _fmt_price(62140.0) == "62,140"   # >= 1000 -> no decimals, thousands
    assert _fmt_price(148.2) == "148.20"     # >= 1 -> 2 decimals
    assert _fmt_price(0.1234) == "0.12340"   # < 1 -> 5 decimals
    assert _fmt_price(None) == "—"           # absent
    assert _fmt_price(0.0) == "—"            # non-positive
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_execute_cli.py::test_fmt_price_adaptive_precision -v`
Expected: FAIL — `ImportError: cannot import name '_fmt_price'`.

- [ ] **Step 3: Implement the helper**

In `tools/xsmom_execute.py`, add near the top of the module (after the imports, before `check_live_gate`):

```python
def _fmt_price(mark: float | None) -> str:
    if mark is None or mark <= 0:
        return "—"
    if mark >= 1000:
        return f"{mark:,.0f}"
    if mark >= 1:
        return f"{mark:,.2f}"
    return f"{mark:.5f}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_execute_cli.py::test_fmt_price_adaptive_precision -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/xsmom_execute.py tests/trade/test_execute_cli.py
git commit -m "feat(trade): add adaptive _fmt_price helper for executor output"
```

---

## Task 3: Book-centric Rich table in `format_result`

**Files:**

- Modify: `tools/xsmom_execute.py` (rewrite `format_result`; add `_action_label`, `_Row`, `_assemble_rows`, `_side_text`, `_build_table`)
- Test: `tests/trade/test_execute_cli.py` (rewrite the `_result` helper; update + add tests)

- [ ] **Step 1: Rewrite the test `_result` helper and update the existing tests**

The new table is book-centric, so the test fixture must build a book. Replace the existing `_result` helper in `tests/trade/test_execute_cli.py` and update its imports/tests:

```python
from __future__ import annotations

import dataclasses

from analytics.xsmom.live import TargetBook, TargetPosition
from tools.xsmom_execute import check_live_gate, format_result
from trade.overlay import OverlayVerdict
from trade.routing import OrderIntent, OrderPlan
from trade.xsmom_executor import ExecutionResult


def _result(
    allowed: bool,
    intents: list[OrderIntent],
    *,
    book_positions: list[TargetPosition] | None = None,
    skipped: list[OrderIntent] | None = None,
    marks: dict[str, float] | None = None,
    positions: dict[str, float] | None = None,
    aborts: list[str] | None = None,
) -> ExecutionResult:
    legs = book_positions or []
    gross = sum(abs(p.leverage) for p in legs)
    net = sum(p.leverage for p in legs)
    book = TargetBook(
        "2026-06-21", "2026-06-22", 10_000.0, 1.0, len(legs), gross, net, legs
    )
    plan = OrderPlan(intents, skipped or [], gross, net)
    verdict = OverlayVerdict(
        allowed, aborts if aborts is not None else ([] if allowed else ["x"])
    )
    return ExecutionResult(
        verdict,
        plan,
        book,
        intents if allowed else [],
        [],
        10_000.0,
        "dry_run",
        marks or {},
        positions or {},
    )
```

Update the two existing format tests so the symbol is backed by a book leg:

```python
def test_format_result_renders_counts() -> None:
    pos = TargetPosition("AAAUSDT", "long", 0.50, 5000.0, 3.2)
    out = format_result(
        _result(
            True,
            [OrderIntent("AAAUSDT", "BUY", 1.0, False, 100.0, "open")],
            book_positions=[pos],
            marks={"AAAUSDT": 100.0},
        )
    )
    assert "AAAUSDT" in out and "submitted" in out.lower()


def test_format_result_shows_aborts_when_blocked() -> None:
    out = format_result(_result(False, []))
    assert "abort" in out.lower() or "blocked" in out.lower()
```

(`dataclasses` is imported for a later test; leave `check_live_gate` / parser tests in the file unchanged.)

- [ ] **Step 2: Add the new behaviour tests**

Append to `tests/trade/test_execute_cli.py`:

```python
def test_format_result_shows_inband_leg_and_leverage() -> None:
    legs = [
        TargetPosition("AAAUSDT", "long", 0.50, 5000.0, 3.2),
        TargetPosition("BBBUSDT", "short", -0.30, -3000.0, -2.1),
    ]
    intents = [OrderIntent("AAAUSDT", "BUY", 10.0, False, 5000.0, "open")]
    skipped = [OrderIntent("BBBUSDT", "SELL", 0.0, True, 40.0, "skip:band")]
    out = format_result(
        _result(
            True,
            intents,
            book_positions=legs,
            skipped=skipped,
            marks={"AAAUSDT": 100.0, "BBBUSDT": 50.0},
        )
    )
    assert "hold (band)" in out  # in-band leg is shown, not hidden
    assert "+0.50" in out and "-0.30" in out  # signed leverage column


def test_format_result_renders_close_only_row() -> None:
    out = format_result(
        _result(
            True,
            [OrderIntent("ZZZUSDT", "SELL", 5.0, True, -500.0, "close")],
            book_positions=[],
            positions={"ZZZUSDT": 5.0},
            marks={"ZZZUSDT": 100.0},
        )
    )
    assert "ZZZUSDT" in out and "close" in out


def test_format_result_blocked_still_shows_book_table() -> None:
    legs = [TargetPosition("AAAUSDT", "long", 0.50, 5000.0, 3.2)]
    out = format_result(
        _result(
            False,
            [],
            book_positions=legs,
            marks={"AAAUSDT": 100.0},
            aborts=["gross leverage 5.0x > cap 4.5x"],
        )
    )
    assert "AAAUSDT" in out  # table still rendered when blocked
    assert "blocked" in out.lower()  # banner present
    assert "gross leverage" in out  # abort reason shown
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_execute_cli.py -v`
Expected: the new tests FAIL (`hold (band)` / book rows not rendered by the old order-centric `format_result`); `test_format_result_renders_counts` may still pass on the symbol but the new ones drive the rewrite.

- [ ] **Step 4: Rewrite `format_result` and add the helpers**

In `tools/xsmom_execute.py`, add these imports at the top:

```python
import io
import math
import shutil
from dataclasses import dataclass

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from trade.routing import OrderIntent
```

Add the helpers + row assembly (after `_fmt_price`):

```python
def _action_label(reason: str) -> str:
    return "hold (band)" if reason == "skip:band" else reason


@dataclass(frozen=True)
class _Row:
    symbol: str
    side: str
    cur_lev: float
    tgt_lev: float
    notional: float  # target notional_usd
    delta: float
    mark: float | None
    forecast: float | None
    action: str


def _assemble_rows(res: ExecutionResult) -> list[_Row]:
    action_by_sym: dict[str, OrderIntent] = {}
    for o in res.plan.intents:
        action_by_sym[o.symbol] = o
    for o in res.plan.skipped:
        action_by_sym.setdefault(o.symbol, o)

    equity = res.equity or 0.0
    book_syms = {p.symbol for p in res.book.positions}
    rows: list[_Row] = []

    for p in res.book.positions:
        mark = res.marks.get(p.symbol)
        cur_qty = res.positions.get(p.symbol, 0.0)
        if mark and equity:
            cur_lev = cur_qty * mark / equity
        else:
            cur_lev = 0.0
        order = action_by_sym.get(p.symbol)
        rows.append(
            _Row(
                symbol=p.symbol,
                side=p.side,
                cur_lev=cur_lev,
                tgt_lev=p.leverage,
                notional=p.notional_usd,
                delta=order.delta_notional if order else 0.0,
                mark=mark,
                forecast=p.forecast,
                action=_action_label(order.reason) if order else "—",
            )
        )

    for sym, qty in res.positions.items():
        if sym in book_syms or qty == 0.0:
            continue
        mark = res.marks.get(sym)
        if mark and equity:
            cur_lev = qty * mark / equity
        else:
            cur_lev = 0.0
        order = action_by_sym.get(sym)
        rows.append(
            _Row(
                symbol=sym,
                side="long" if qty > 0 else "short",
                cur_lev=cur_lev,
                tgt_lev=0.0,
                notional=0.0,
                delta=order.delta_notional if order else 0.0,
                mark=mark,
                forecast=None,
                action=_action_label(order.reason) if order else "close",
            )
        )

    rows.sort(key=lambda r: max(abs(r.notional), abs(r.cur_lev) * equity), reverse=True)
    return rows


def _side_text(side: str) -> Text:
    style = {"long": "green", "short": "red"}.get(side, "dim")
    return Text(side.upper(), style=style)


def _build_table(rows: list[_Row]) -> Table:
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE_HEAVY)
    table.add_column("SYM")
    table.add_column("SIDE")
    table.add_column("CUR→TGT", justify="right")
    table.add_column("$NOTIONAL", justify="right")
    table.add_column("Δ$", justify="right")
    table.add_column("MARK", justify="right")
    table.add_column("FCAST", justify="right")
    table.add_column("ACTION")
    for r in rows:
        if r.forecast is None or not math.isfinite(r.forecast):
            fcast = "—"
        else:
            fcast = f"{r.forecast:+.1f}"
        table.add_row(
            r.symbol,
            _side_text(r.side),
            f"{r.cur_lev:+.2f}→{r.tgt_lev:+.2f}",
            f"{r.notional:+,.0f}",
            f"{r.delta:+,.0f}",
            _fmt_price(r.mark),
            fcast,
            r.action,
        )
    return table
```

Replace the entire body of `format_result` with:

```python
def format_result(res: ExecutionResult) -> str:
    book = res.book
    gross_notional = book.gross_leverage * book.capital
    summary = (
        f"GOV {book.governor:.2f} · GROSS {book.gross_leverage:.2f}× · "
        f"NET {book.net_leverage:+.2f}× · {book.active_count} legs · "
        f"gross ${gross_notional:,.0f}"
    )

    width = max(shutil.get_terminal_size((140, 24)).columns, 140)
    buf = io.StringIO()
    console = Console(file=buf, width=width, force_terminal=True, markup=False)

    if res.verdict.allowed:
        console.print(
            f"XS execute · {res.mode} · hold {book.next_period_date} · "
            f"equity ${res.equity:,.2f}"
        )
    else:
        console.print(
            Text(
                f"⛔ XS execute · {res.mode} · hold {book.next_period_date} · "
                "BLOCKED by overlay",
                style="bold red",
            )
        )
    console.print(summary)
    if not res.verdict.allowed:
        console.print("aborts:")
        for a in res.verdict.aborts:
            console.print(f"  · {a}")

    console.print(_build_table(_assemble_rows(res)))

    console.print(
        f"submitted {len(res.submitted)} · skipped {len(res.plan.skipped)} · "
        f"failed {len(res.failed)}"
    )
    for intent, err in res.failed:
        console.print(f"  FAILED {intent.symbol} {intent.side} {intent.qty}: {err}")

    return buf.getvalue()
```

- [ ] **Step 5: Run the full executor-CLI test file**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_execute_cli.py -v`
Expected: PASS (all old + new tests).

- [ ] **Step 6: Lint + typecheck the touched module**

Run: `make lint-py && make typecheck`
Expected: both clean. (If mypy flags the `cur_lev` branch, confirm the `if mark and equity:` statement form is used — not a conditional expression — so `mark` narrows from `float | None` to `float`.)

- [ ] **Step 7: Eyeball the real output (optional sanity)**

Run: `make buibui-universe-sync && make buibui-xsmom-execute`
Expected: a Rich box-table with the 8 columns + the `GOV / GROSS / NET / legs / gross $` summary line, sorted biggest-notional first. (Dry-run; submits nothing.)

- [ ] **Step 8: Commit**

```bash
git add tools/xsmom_execute.py tests/trade/test_execute_cli.py
git commit -m "feat(trade): book-centric Rich table for XS executor dry-run output"
```

---

## Task 4: Docs — column legend + output versions

**Files:**

- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the README section**

Find the executor section: `grep -n "buibui-xsmom-execute" README.md`. Insert this block immediately after that section's existing prose (create a new `###` subsection):

```markdown
### XS executor dry-run output

`make buibui-xsmom-execute` (dry-run default) prints the target book as a Rich
table — one row per active leg (not just the legs that trade this cycle), sorted
by |notional| descending:

| Column | Meaning |
| --- | --- |
| SYM | Instrument |
| SIDE | LONG (green) / SHORT (red) |
| CUR→TGT | Current leverage → target leverage (signed, governor-scaled) |
| $NOTIONAL | Target dollar exposure (leverage × equity) |
| Δ$ | Dollar move this cycle (the order, if any) |
| MARK | Latest mark price |
| FCAST | Demeaned cross-sectional forecast (relative-strength signal) |
| ACTION | open / rebalance / close / hold (band) / skip:&lt;why&gt; |

The header summarises the book: `GOV` (vol governor), `GROSS` / `NET` leverage,
leg count, total gross notional. Leverage is vol-targeted and vol-parity — **not
1× per leg**; `--exchange-leverage` is only the Binance margin setting, separate
from the book's gross.

Three output versions:

- **dry-run plan** (default): the advisory plan; nothing is submitted.
- **⛔ BLOCKED by overlay**: a risk guardrail tripped — the book table still
  renders (so you see what was blocked) beneath the abort reasons; nothing
  submits.
- **testnet submit** (`--mode testnet`): same layout; the footer's `submitted` /
  `failed` counts reflect real orders placed on testnet.
```

- [ ] **Step 2: Update the CLAUDE.md `tools/xsmom_execute.py` line**

Find it: `grep -n "xsmom_execute.py" CLAUDE.md`. Append this clause to the end of the existing `tools/xsmom_execute.py` description sentence (inside the `trade/` package bullet):

```text
 Output is a book-centric Rich table (one row per active leg incl. in-band holds, sorted by |notional| desc; columns SYM/SIDE/CUR→TGT-leverage/$NOTIONAL/Δ$/MARK/FCAST/ACTION) under a GOV/GROSS/NET-leverage summary header; the overlay-blocked case still renders the table beneath the abort banner.
```

- [ ] **Step 3: Lint markdown**

Run: `make lint-md`
Expected: clean. (Table delimiter rows must be spaced `| --- |`; every fenced block needs a language — `text` for the CLAUDE.md snippet.)

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs(trade): document XS executor table columns + output versions"
```

---

## Task 5: Full Definition-of-Done verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full gate**

Run:

```bash
make lint-py && make typecheck && make test && make test-regression
```

Expected: lint-py ✓, typecheck ✓, full pytest green (+5 new tests vs the prior count), regression goldens **UNMOVED** (this is a formatting-only change — if any golden moves, stop and investigate, do not regenerate).

- [ ] **Step 2: Confirm and report**

State each result plainly. If all green, the branch is ready for `/post-branch` + PR. If anything failed, fix before claiming done.

---

## Self-review notes

- **Spec coverage:** marks/positions threading (Task 1) ✓; `_fmt_price` (Task 2) ✓; book-centric rows + columns + sort + Rich render + summary + blocked-table (Task 3) ✓; docs/output-versions (Task 4) ✓; DoD incl. goldens-unmoved (Task 5) ✓. No-$ADV is a non-goal — correctly absent.
- **Type consistency:** `_Row` fields used identically in `_assemble_rows` and `_build_table`; `ExecutionResult.marks`/`.positions` (Task 1) consumed by `_assemble_rows` (Task 3); `_fmt_price` (Task 2) reused in `_build_table` (Task 3); `_action_label` defined and used in Task 3.
- **No placeholders:** every code step is complete and runnable.
