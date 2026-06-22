# XS-solo Order Routing + Risk Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the read-only XS-solo `TargetBook` into an overlay-gated market-order plan executed through a Binance Futures adapter that runs dry-run by default and can submit on testnet.

**Architecture:** Approach A — pure `trade/routing.py` (target → order plan) and `trade/overlay.py` (risk gate) with no I/O, a thin injectable `trade/binance_futures.py` adapter, a `trade/xsmom_executor.py` orchestrator that persists a small state file, and a `tools/xsmom_execute.py` CLI. All execution code lives under `trade/` so `analytics/xsmom/` stays pure/read-only.

**Tech Stack:** Python 3.11+, `python-binance` `Client`, `duckdb`, `pandas`, pytest + `unittest.mock`. Poetry. ruff + mypy strict.

---

## Background for the implementer

- The XS-solo sleeve is the validated deploy core (universe Sharpe +1.375). Slice 1 (PR #451) shipped `analytics/xsmom/live.py` (`TargetBook`, `TargetPosition`, `build_target_book`) and `analytics/xsmom/replay.py::replay_targets(conn, cfg, capital, symbols) -> TargetBook`. **Do not modify those.**
- `TargetBook` fields: `as_of_date: str`, `next_period_date: str`, `capital: float`, `governor: float`, `active_count: int`, `gross_leverage: float`, `net_leverage: float`, `positions: list[TargetPosition]`. `TargetPosition` fields: `symbol: str`, `side: str`, `leverage: float`, `notional_usd: float` (signed), `forecast: float`.
- The spec is `docs/superpowers/specs/2026-06-21-p3-xsmom-order-routing-overlay-design.md`. Read it for the locked decisions.
- `python-binance` `Client` futures methods used: `futures_position_information()`, `futures_account()`, `futures_exchange_info()`, `futures_mark_price()`, `futures_create_order(...)`, `futures_change_leverage(...)`, `futures_change_margin_type(...)`, `futures_get_position_mode()`. The `binance.exceptions.APIError` carries `.code` (int).
- Tests seed an in-memory DuckDB exactly like `tests/xsmom/test_targets_replay.py::_seed` (reuse that pattern verbatim for the executor test).
- **Commit discipline (memory gotcha):** never pipe `git commit` output (a pipe masks the exit code). Run `git commit -m "..."` then verify with `git log --oneline -1`. Pre-commit runs ruff + markdownlint; if it reformats, `git add -u` and re-commit.

## File structure

```text
trade/routing.py          CREATE  pure: ExchangeFilters, OrderIntent, OrderPlan, build_order_plan
trade/overlay.py          CREATE  pure: RiskLimits, AccountState, OverlayVerdict, evaluate_overlay
trade/binance_futures.py  CREATE  I/O adapter over python-binance Client (injectable)
trade/xsmom_executor.py   CREATE  ExecutionResult, load_state/save_state, run_once orchestrator
tools/xsmom_execute.py    CREATE  CLI + live-gate + output rendering
tests/trade/test_routing.py          CREATE
tests/trade/test_overlay.py          CREATE
tests/trade/test_binance_futures.py  CREATE
tests/trade/test_xsmom_executor.py   CREATE
tests/trade/test_execute_cli.py      CREATE
Makefile                  MODIFY  add buibui-xsmom-execute target
CLAUDE.md                 MODIFY  document the trade/ execution layer
README.md                 MODIFY  document the new CLI/Make target
```

`trade/__init__.py` already exists (empty). Do not add `__init__.py` under `tests/trade/` (mirrors `tests/xsmom/`).

---

### Task 1: `trade/routing.py` — pure order-plan builder

**Files:**

- Create: `trade/routing.py`
- Test: `tests/trade/test_routing.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/trade/test_routing.py
from __future__ import annotations

from analytics.xsmom.live import TargetBook, TargetPosition
from trade.routing import ExchangeFilters, build_order_plan


def _filters(sym: str, step: float = 0.001, min_qty: float = 0.001,
             min_notional: float = 5.0) -> ExchangeFilters:
    return ExchangeFilters(symbol=sym, qty_step=step, min_qty=min_qty,
                           min_notional=min_notional)


def _book(positions: list[TargetPosition], capital: float = 10_000.0) -> TargetBook:
    gross = sum(abs(p.leverage) for p in positions)
    net = sum(p.leverage for p in positions)
    return TargetBook(
        as_of_date="2026-06-21", next_period_date="2026-06-22", capital=capital,
        governor=1.0, active_count=len(positions), gross_leverage=gross,
        net_leverage=net, positions=positions,
    )


def _pos(sym: str, lev: float, capital: float = 10_000.0) -> TargetPosition:
    return TargetPosition(symbol=sym, side="long" if lev > 0 else "short",
                          leverage=lev, notional_usd=lev * capital, forecast=0.0)


def test_open_from_flat_rounds_qty_down_to_step() -> None:
    # target notional = 0.1*10000 = $1000 at mark 100 -> 10.0 units; step 0.001
    book = _book([_pos("AAAUSDT", 0.1)])
    plan = build_order_plan(
        book, current_positions={}, marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.005, capital=10_000.0,
    )
    assert len(plan.intents) == 1
    o = plan.intents[0]
    assert o.symbol == "AAAUSDT" and o.side == "BUY"
    assert o.qty == 10.0 and o.reduce_only is False
    assert o.reason == "open"


def test_below_band_is_skipped() -> None:
    # delta notional $20 < band 0.005*10000 = $50
    book = _book([_pos("AAAUSDT", 0.1)])
    plan = build_order_plan(
        book, current_positions={"AAAUSDT": 9.8}, marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.005, capital=10_000.0,
    )
    assert plan.intents == []
    assert any(s.reason == "skip:band" for s in plan.skipped)


def test_below_min_notional_is_skipped() -> None:
    book = _book([_pos("AAAUSDT", 0.0006)])  # $6 notional, min_notional 10
    plan = build_order_plan(
        book, current_positions={}, marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT", min_notional=10.0)},
        no_trade_band_frac=0.0, capital=10_000.0,
    )
    assert plan.intents == []
    assert any(s.reason == "skip:min_notional" for s in plan.skipped)


def test_trim_same_side_is_reduce_only() -> None:
    # target long 5 units, currently long 10 -> SELL 5, reduce_only
    book = _book([_pos("AAAUSDT", 0.05)])  # $500 -> 5 units @ 100
    plan = build_order_plan(
        book, current_positions={"AAAUSDT": 10.0}, marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.0, capital=10_000.0,
    )
    o = plan.intents[0]
    assert o.side == "SELL" and o.qty == 5.0 and o.reduce_only is True


def test_close_absent_symbol_full_reduce_only() -> None:
    # held long 3 units, not in target -> SELL 3 reduce_only, bypasses band
    book = _book([_pos("AAAUSDT", 0.1)])
    plan = build_order_plan(
        book,
        current_positions={"AAAUSDT": 10.0, "ZZZUSDT": 3.0},
        marks={"AAAUSDT": 100.0, "ZZZUSDT": 50.0},
        filters={"AAAUSDT": _filters("AAAUSDT"), "ZZZUSDT": _filters("ZZZUSDT")},
        no_trade_band_frac=0.99, capital=10_000.0,   # huge band; close must still fire
    )
    closes = [o for o in plan.intents if o.symbol == "ZZZUSDT"]
    assert len(closes) == 1
    assert closes[0].side == "SELL" and closes[0].qty == 3.0
    assert closes[0].reduce_only is True and closes[0].reason == "close"


def test_flip_long_to_short_is_not_reduce_only() -> None:
    # currently long 10, target short 5 -> SELL 15, NOT reduce_only (must flip)
    book = _book([_pos("AAAUSDT", -0.05)])  # -$500 -> -5 units @ 100
    plan = build_order_plan(
        book, current_positions={"AAAUSDT": 10.0}, marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.0, capital=10_000.0,
    )
    o = plan.intents[0]
    assert o.side == "SELL" and o.qty == 15.0 and o.reduce_only is False


def test_missing_mark_is_skipped() -> None:
    book = _book([_pos("AAAUSDT", 0.1)])
    plan = build_order_plan(
        book, current_positions={}, marks={},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.0, capital=10_000.0,
    )
    assert plan.intents == []
    assert any(s.reason == "skip:no_mark" for s in plan.skipped)


def test_leverage_aggregates_come_from_book() -> None:
    book = _book([_pos("AAAUSDT", 0.1), _pos("BBBUSDT", -0.2)])
    plan = build_order_plan(
        book, current_positions={},
        marks={"AAAUSDT": 100.0, "BBBUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT"), "BBBUSDT": _filters("BBBUSDT")},
        no_trade_band_frac=0.0, capital=10_000.0,
    )
    assert plan.target_gross_leverage == 0.3
    assert abs(plan.target_net_leverage - (-0.1)) < 1e-12
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_routing.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'trade.routing'`.

- [ ] **Step 3: Write `trade/routing.py`**

```python
"""Pure order-plan construction for the XS-solo executor.

Converts a `TargetBook` plus current exchange positions, mark prices, and
per-symbol exchange filters into a market-order plan. No I/O, no Binance
imports — fully unit-testable. Reduce-only is set on trims/closes (never on
intended flips); a no-trade band and the exchange min-notional/min-qty filters
suppress negligible or unsubmittable orders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from analytics.xsmom.live import TargetBook


@dataclass(frozen=True)
class ExchangeFilters:
    symbol: str
    qty_step: float
    min_qty: float
    min_notional: float


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str  # "BUY" | "SELL"
    qty: float
    reduce_only: bool
    delta_notional: float
    reason: str  # "open" | "rebalance" | "close" | "skip:<why>"


@dataclass(frozen=True)
class OrderPlan:
    intents: list[OrderIntent]
    skipped: list[OrderIntent]
    target_gross_leverage: float
    target_net_leverage: float


def _round_down_to_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    return math.floor(abs(qty) / step) * step


def _signed_target_qty(notional: float, mark: float, step: float) -> float:
    mag = _round_down_to_step(abs(notional) / mark, step)
    return math.copysign(mag, notional)


def build_order_plan(
    book: TargetBook,
    current_positions: dict[str, float],
    marks: dict[str, float],
    filters: dict[str, ExchangeFilters],
    *,
    no_trade_band_frac: float,
    capital: float,
) -> OrderPlan:
    """Per-symbol market orders to move current positions to the target book."""
    band = no_trade_band_frac * capital
    target_notional = {p.symbol: p.notional_usd for p in book.positions}
    symbols = set(target_notional) | set(current_positions)

    intents: list[OrderIntent] = []
    skipped: list[OrderIntent] = []

    for sym in sorted(symbols):
        current = float(current_positions.get(sym, 0.0))
        is_close = sym not in target_notional
        notional = target_notional.get(sym, 0.0)
        mark = marks.get(sym)
        filt = filters.get(sym)

        if mark is None or mark <= 0:
            skipped.append(OrderIntent(sym, "SELL" if current > 0 else "BUY",
                                       0.0, False, 0.0, "skip:no_mark"))
            continue
        if filt is None:
            skipped.append(OrderIntent(sym, "SELL" if current > 0 else "BUY",
                                       0.0, False, 0.0, "skip:no_filters"))
            continue

        target_qty = 0.0 if is_close else _signed_target_qty(notional, mark, filt.qty_step)
        delta_qty = _round_down_to_step(target_qty - current, filt.qty_step)
        delta_qty = math.copysign(delta_qty, target_qty - current)
        delta_notional = delta_qty * mark
        side = "BUY" if delta_qty > 0 else "SELL"
        order_qty = abs(delta_qty)

        same_side_trim = (
            current != 0.0
            and target_qty != 0.0
            and (target_qty > 0) == (current > 0)
            and abs(target_qty) < abs(current)
        )
        reduce_only = is_close or same_side_trim
        reason = "close" if is_close else ("rebalance" if current != 0.0 else "open")

        if order_qty == 0.0:
            skipped.append(OrderIntent(sym, side, 0.0, reduce_only,
                                       delta_notional, "skip:noop"))
            continue
        if order_qty < filt.min_qty:
            skipped.append(OrderIntent(sym, side, order_qty, reduce_only,
                                       delta_notional, "skip:min_qty"))
            continue
        if abs(delta_notional) < filt.min_notional:
            skipped.append(OrderIntent(sym, side, order_qty, reduce_only,
                                       delta_notional, "skip:min_notional"))
            continue
        if not is_close and abs(delta_notional) < band:
            skipped.append(OrderIntent(sym, side, order_qty, reduce_only,
                                       delta_notional, "skip:band"))
            continue

        intents.append(OrderIntent(sym, side, order_qty, reduce_only,
                                   delta_notional, reason))

    return OrderPlan(
        intents=intents,
        skipped=skipped,
        target_gross_leverage=book.gross_leverage,
        target_net_leverage=book.net_leverage,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_routing.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
poetry run ruff format trade/routing.py tests/trade/test_routing.py
poetry run ruff check --fix trade/routing.py tests/trade/test_routing.py
poetry run mypy trade/routing.py
git add trade/routing.py tests/trade/test_routing.py
git commit -m "feat(trade): pure order-plan builder for the XS executor"
git log --oneline -1
```

---

### Task 2: `trade/overlay.py` — pure risk overlay

**Files:**

- Create: `trade/overlay.py`
- Test: `tests/trade/test_overlay.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/trade/test_overlay.py
from __future__ import annotations

from analytics.xsmom.live import TargetBook, TargetPosition
from trade.overlay import AccountState, RiskLimits, evaluate_overlay
from trade.routing import OrderIntent, OrderPlan


def _limits(**kw: float) -> RiskLimits:
    base = dict(max_gross_leverage=3.0, max_position_notional_frac=0.5,
                max_drawdown_frac=0.25, max_run_turnover_frac=1.0,
                max_data_staleness_hours=36.0)
    base.update(kw)
    return RiskLimits(**base)  # type: ignore[arg-type]


def _book(positions: list[TargetPosition], gross: float = 1.0,
          net: float = 0.0, capital: float = 10_000.0) -> TargetBook:
    return TargetBook(as_of_date="2026-06-21", next_period_date="2026-06-22",
                      capital=capital, governor=1.0, active_count=len(positions),
                      gross_leverage=gross, net_leverage=net, positions=positions)


def _plan(intents: list[OrderIntent], gross: float = 1.0, net: float = 0.0) -> OrderPlan:
    return OrderPlan(intents=intents, skipped=[],
                     target_gross_leverage=gross, target_net_leverage=net)


def _intent(notional: float) -> OrderIntent:
    return OrderIntent("AAAUSDT", "BUY", 1.0, False, notional, "open")


def test_all_pass_is_allowed() -> None:
    v = evaluate_overlay(_plan([_intent(100.0)]), _book([]),
                         AccountState(10_000.0, 10_000.0, False),
                         _limits(), data_age_hours=1.0)
    assert v.allowed is True and v.aborts == []


def test_kill_switch_aborts() -> None:
    v = evaluate_overlay(_plan([]), _book([]),
                         AccountState(10_000.0, 10_000.0, True),
                         _limits(), data_age_hours=1.0)
    assert v.allowed is False and any("kill" in a.lower() for a in v.aborts)


def test_drawdown_aborts() -> None:
    # equity 7000 < peak 10000 * (1-0.25) = 7500
    v = evaluate_overlay(_plan([]), _book([]),
                         AccountState(7_000.0, 10_000.0, False),
                         _limits(), data_age_hours=1.0)
    assert v.allowed is False and any("drawdown" in a.lower() for a in v.aborts)


def test_gross_leverage_cap_aborts() -> None:
    v = evaluate_overlay(_plan([], gross=3.5), _book([], gross=3.5),
                         AccountState(10_000.0, 10_000.0, False),
                         _limits(), data_age_hours=1.0)
    assert v.allowed is False and any("gross" in a.lower() for a in v.aborts)


def test_per_instrument_notional_cap_aborts() -> None:
    # leg notional 6000 > 0.5 * 10000 = 5000
    book = _book([TargetPosition("AAAUSDT", "long", 0.6, 6_000.0, 0.0)])
    v = evaluate_overlay(_plan([]), book,
                         AccountState(10_000.0, 10_000.0, False),
                         _limits(), data_age_hours=1.0)
    assert v.allowed is False and any("notional" in a.lower() for a in v.aborts)


def test_run_turnover_guard_aborts() -> None:
    # total |delta_notional| = 12000 > 1.0 * 10000
    v = evaluate_overlay(_plan([_intent(7_000.0), _intent(-5_000.0)]), _book([]),
                         AccountState(10_000.0, 10_000.0, False),
                         _limits(), data_age_hours=1.0)
    assert v.allowed is False and any("turnover" in a.lower() for a in v.aborts)


def test_staleness_aborts() -> None:
    v = evaluate_overlay(_plan([]), _book([]),
                         AccountState(10_000.0, 10_000.0, False),
                         _limits(), data_age_hours=48.0)
    assert v.allowed is False and any("stale" in a.lower() for a in v.aborts)


def test_multiple_breaches_collected() -> None:
    v = evaluate_overlay(_plan([], gross=5.0), _book([]),
                         AccountState(1_000.0, 10_000.0, True),
                         _limits(), data_age_hours=99.0)
    assert v.allowed is False and len(v.aborts) >= 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_overlay.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'trade.overlay'`.

- [ ] **Step 3: Write `trade/overlay.py`**

```python
"""Pure fail-closed risk overlay for the XS-solo executor.

Each guardrail is an independent check; any breach blocks the entire order plan
(not per-order). No I/O — every check is a one-line unit test. The capital
basis for the fractional caps is the book's own `capital` (live equity).
"""

from __future__ import annotations

from dataclasses import dataclass

from analytics.xsmom.live import TargetBook
from trade.routing import OrderPlan


@dataclass(frozen=True)
class RiskLimits:
    max_gross_leverage: float
    max_position_notional_frac: float
    max_drawdown_frac: float
    max_run_turnover_frac: float
    max_data_staleness_hours: float


@dataclass(frozen=True)
class AccountState:
    equity: float
    peak_equity: float
    kill_switch: bool


@dataclass(frozen=True)
class OverlayVerdict:
    allowed: bool
    aborts: list[str]


def evaluate_overlay(
    plan: OrderPlan,
    book: TargetBook,
    account: AccountState,
    limits: RiskLimits,
    data_age_hours: float,
) -> OverlayVerdict:
    aborts: list[str] = []

    if account.kill_switch:
        aborts.append("kill-switch engaged")

    floor = account.peak_equity * (1.0 - limits.max_drawdown_frac)
    if account.equity < floor:
        aborts.append(
            f"drawdown halt: equity {account.equity:.2f} < floor {floor:.2f} "
            f"(peak {account.peak_equity:.2f})"
        )

    if plan.target_gross_leverage > limits.max_gross_leverage:
        aborts.append(
            f"gross leverage {plan.target_gross_leverage:.2f} > "
            f"cap {limits.max_gross_leverage:.2f}"
        )

    notional_cap = limits.max_position_notional_frac * book.capital
    for p in book.positions:
        if abs(p.notional_usd) > notional_cap:
            aborts.append(
                f"per-instrument notional {p.symbol} {abs(p.notional_usd):.2f} "
                f"> cap {notional_cap:.2f}"
            )

    turnover = sum(abs(o.delta_notional) for o in plan.intents)
    turnover_cap = limits.max_run_turnover_frac * book.capital
    if turnover > turnover_cap:
        aborts.append(
            f"run turnover {turnover:.2f} > cap {turnover_cap:.2f}"
        )

    if data_age_hours > limits.max_data_staleness_hours:
        aborts.append(
            f"stale data: {data_age_hours:.1f}h > "
            f"{limits.max_data_staleness_hours:.1f}h"
        )

    return OverlayVerdict(allowed=not aborts, aborts=aborts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_overlay.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
poetry run ruff format trade/overlay.py tests/trade/test_overlay.py
poetry run ruff check --fix trade/overlay.py tests/trade/test_overlay.py
poetry run mypy trade/overlay.py
git add trade/overlay.py tests/trade/test_overlay.py
git commit -m "feat(trade): pure fail-closed risk overlay for the XS executor"
git log --oneline -1
```

---

### Task 3: `trade/binance_futures.py` — injectable I/O adapter

**Files:**

- Create: `trade/binance_futures.py`
- Test: `tests/trade/test_binance_futures.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/trade/test_binance_futures.py
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from trade.binance_futures import BinanceFuturesAdapter
from trade.routing import OrderIntent


class _APIError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"code {code}")
        self.code = code


def _intent() -> OrderIntent:
    return OrderIntent("AAAUSDT", "BUY", 2.0, False, 200.0, "open")


def test_get_positions_parses_signed_amt() -> None:
    client = MagicMock()
    client.futures_position_information.return_value = [
        {"symbol": "AAAUSDT", "positionAmt": "1.5"},
        {"symbol": "BBBUSDT", "positionAmt": "-2.0"},
        {"symbol": "CCCUSDT", "positionAmt": "0"},
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    pos = adapter.get_positions()
    assert pos == {"AAAUSDT": 1.5, "BBBUSDT": -2.0}  # zero dropped


def test_get_equity_uses_total_margin_balance() -> None:
    client = MagicMock()
    client.futures_account.return_value = {"totalMarginBalance": "10250.5"}
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_equity() == 10250.5


def test_get_filters_extracts_lot_and_notional() -> None:
    client = MagicMock()
    client.futures_exchange_info.return_value = {
        "symbols": [
            {"symbol": "AAAUSDT", "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ]},
        ]
    }
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    f = adapter.get_filters(["AAAUSDT"])["AAAUSDT"]
    assert f.qty_step == 0.001 and f.min_qty == 0.001 and f.min_notional == 5.0


def test_get_marks_parses_prices() -> None:
    client = MagicMock()
    client.futures_mark_price.return_value = [
        {"symbol": "AAAUSDT", "markPrice": "100.0"},
        {"symbol": "BBBUSDT", "markPrice": "50.0"},
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_marks(["AAAUSDT"]) == {"AAAUSDT": 100.0}


def test_submit_market_is_noop_in_dry_run() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    res = adapter.submit_market(_intent())
    assert res["dryRun"] is True
    client.futures_create_order.assert_not_called()


def test_submit_market_calls_create_order_on_testnet() -> None:
    client = MagicMock()
    client.futures_create_order.return_value = {"orderId": 1}
    adapter = BinanceFuturesAdapter(client, mode="testnet")
    adapter.submit_market(_intent())
    client.futures_create_order.assert_called_once_with(
        symbol="AAAUSDT", side="BUY", type="MARKET", quantity=2.0, reduceOnly=False,
    )


def test_ensure_account_config_raises_on_hedge_mode() -> None:
    client = MagicMock()
    client.futures_get_position_mode.return_value = {"dualSidePosition": True}
    adapter = BinanceFuturesAdapter(client, mode="testnet")
    with pytest.raises(RuntimeError, match="hedge"):
        adapter.ensure_account_config(["AAAUSDT"], leverage=5)


def test_ensure_account_config_swallows_4046(monkeypatch: Any) -> None:
    import trade.binance_futures as mod
    monkeypatch.setattr(mod, "APIError", _APIError, raising=False)
    client = MagicMock()
    client.futures_get_position_mode.return_value = {"dualSidePosition": False}
    client.futures_change_margin_type.side_effect = _APIError(-4046)
    adapter = BinanceFuturesAdapter(client, mode="testnet")
    adapter.ensure_account_config(["AAAUSDT"], leverage=5)  # must not raise
    client.futures_change_leverage.assert_called_once_with(symbol="AAAUSDT", leverage=5)


def test_ensure_account_config_noop_in_dry_run() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    adapter.ensure_account_config(["AAAUSDT"], leverage=5)
    client.futures_get_position_mode.assert_not_called()
    client.futures_change_leverage.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_binance_futures.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'trade.binance_futures'`.

- [ ] **Step 3: Write `trade/binance_futures.py`**

```python
"""Binance USDT-M Futures I/O adapter for the XS-solo executor.

Thin, injectable wrapper over a `python-binance` Client. Read methods always
hit the API; write methods (`ensure_account_config`, `submit_market`) are
no-op-and-log when `mode == "dry_run"`. The client is constructed by the CLL
(mainnet for dry_run/live, testnet client for testnet) and injected here, so
this class is unit-testable with a MagicMock.
"""

from __future__ import annotations

from typing import Any

from trade.routing import ExchangeFilters, OrderIntent

try:  # pragma: no cover - import shape depends on python-binance version
    from binance.exceptions import APIError
except Exception:  # pragma: no cover

    class APIError(Exception):  # type: ignore[no-redef]
        code = 0


_MARGIN_TYPE_UNCHANGED = -4046


class BinanceFuturesAdapter:
    def __init__(self, client: Any, mode: str) -> None:
        if mode not in ("dry_run", "testnet", "live"):
            raise ValueError(f"unknown mode {mode!r}")
        self.client = client
        self.mode = mode

    # ----- reads -----
    def get_positions(self) -> dict[str, float]:
        rows = self.client.futures_position_information()
        out: dict[str, float] = {}
        for r in rows:
            amt = float(r["positionAmt"])
            if amt != 0.0:
                out[r["symbol"]] = amt
        return out

    def get_equity(self) -> float:
        return float(self.client.futures_account()["totalMarginBalance"])

    def get_filters(self, symbols: list[str]) -> dict[str, ExchangeFilters]:
        info = self.client.futures_exchange_info()
        wanted = set(symbols)
        out: dict[str, ExchangeFilters] = {}
        for s in info["symbols"]:
            if s["symbol"] not in wanted:
                continue
            step = min_qty = min_notional = 0.0
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                    min_qty = float(f["minQty"])
                elif f["filterType"] == "MIN_NOTIONAL":
                    min_notional = float(f["notional"])
            out[s["symbol"]] = ExchangeFilters(
                symbol=s["symbol"], qty_step=step, min_qty=min_qty,
                min_notional=min_notional,
            )
        return out

    def get_marks(self, symbols: list[str]) -> dict[str, float]:
        rows = self.client.futures_mark_price()
        wanted = set(symbols)
        return {
            r["symbol"]: float(r["markPrice"])
            for r in rows
            if r["symbol"] in wanted
        }

    # ----- writes -----
    def ensure_account_config(self, symbols: list[str], *, leverage: int) -> None:
        if self.mode == "dry_run":
            return
        if self.client.futures_get_position_mode().get("dualSidePosition"):
            raise RuntimeError(
                "account is in hedge mode; XS executor requires one-way mode"
            )
        for sym in symbols:
            try:
                self.client.futures_change_margin_type(symbol=sym, marginType="CROSSED")
            except APIError as exc:  # already CROSSED is fine
                if getattr(exc, "code", None) != _MARGIN_TYPE_UNCHANGED:
                    raise
            self.client.futures_change_leverage(symbol=sym, leverage=leverage)

    def submit_market(self, intent: OrderIntent) -> dict[str, Any]:
        if self.mode == "dry_run":
            return {"dryRun": True, "symbol": intent.symbol, "side": intent.side,
                    "qty": intent.qty, "reduceOnly": intent.reduce_only}
        return self.client.futures_create_order(  # type: ignore[no-any-return]
            symbol=intent.symbol, side=intent.side, type="MARKET",
            quantity=intent.qty, reduceOnly=intent.reduce_only,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_binance_futures.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
poetry run ruff format trade/binance_futures.py tests/trade/test_binance_futures.py
poetry run ruff check --fix trade/binance_futures.py tests/trade/test_binance_futures.py
poetry run mypy trade/binance_futures.py
git add trade/binance_futures.py tests/trade/test_binance_futures.py
git commit -m "feat(trade): injectable Binance Futures adapter (dry-run/testnet/live)"
git log --oneline -1
```

---

### Task 4: `trade/xsmom_executor.py` — orchestrator + state

**Files:**

- Create: `trade/xsmom_executor.py`
- Test: `tests/trade/test_xsmom_executor.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/trade/test_xsmom_executor.py
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from trade.overlay import RiskLimits
from trade.routing import OrderIntent
from trade.xsmom_executor import load_state, run_once, save_state

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, n: int = 400) -> list[str]:
    rng = np.random.default_rng(3)
    start = 1_609_459_200_000
    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    for i, sym in enumerate(syms):
        steps = rng.normal(0.0005 * (i - 1), 0.02, n)
        close = 100.0 * np.exp(np.cumsum(steps))
        rows = pd.DataFrame({
            "symbol": sym, "timeframe": "1d",
            "open_time": [start + k * _DAY for k in range(n)],
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1000.0, "taker_buy_volume": 500.0,
        })
        upsert_ohlcv(conn, rows)
    return syms


class _FakeAdapter:
    def __init__(self, equity: float, positions: dict[str, float],
                 marks: dict[str, float], mode: str = "dry_run") -> None:
        self._equity = equity
        self._positions = positions
        self._marks = marks
        self.mode = mode
        self.submitted: list[OrderIntent] = []
        self.config_calls = 0
        self.fail_symbol: str | None = None

    def get_equity(self) -> float:
        return self._equity

    def get_positions(self) -> dict[str, float]:
        return dict(self._positions)

    def get_marks(self, symbols: list[str]) -> dict[str, float]:
        return {s: self._marks.get(s, 100.0) for s in symbols}

    def get_filters(self, symbols: list[str]):  # type: ignore[no-untyped-def]
        from trade.routing import ExchangeFilters
        return {s: ExchangeFilters(s, 0.001, 0.001, 5.0) for s in symbols}

    def ensure_account_config(self, symbols: list[str], *, leverage: int) -> None:
        self.config_calls += 1

    def submit_market(self, intent: OrderIntent) -> dict[str, object]:
        if self.fail_symbol in (intent.symbol, "*"):  # "*" fails every order
            raise RuntimeError("rejected")
        self.submitted.append(intent)
        return {"ok": True}


def _limits(**kw: float) -> RiskLimits:
    base = dict(max_gross_leverage=10.0, max_position_notional_frac=1.0,
                max_drawdown_frac=0.5, max_run_turnover_frac=10.0,
                max_data_staleness_hours=1e9)
    base.update(kw)
    return RiskLimits(**base)  # type: ignore[arg-type]


def test_load_state_defaults_when_absent(tmp_path: Path) -> None:
    st = load_state(tmp_path / "nope.json")
    assert st["peak_equity"] == 0.0 and st["kill_switch"] is False


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    save_state(p, {"peak_equity": 5.0, "kill_switch": True, "last_run": {}})
    st = load_state(p)
    assert st["peak_equity"] == 5.0 and st["kill_switch"] is True


def test_run_once_happy_path_submits(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=10_000.0, positions={}, marks={})
    state_path = tmp_path / "execution_state_dry_run.json"
    res = run_once(
        conn, adapter, ForecastConfig(), syms, _limits(),
        no_trade_band_frac=0.0, exchange_leverage=5, state_path=state_path,
        now=pd.Timestamp("2022-02-05", tz="UTC"),
    )
    assert res.verdict.allowed is True
    assert len(res.submitted) >= 1
    assert adapter.config_calls == 1
    assert state_path.exists()
    assert load_state(state_path)["peak_equity"] == 10_000.0


def test_run_once_overlay_breach_submits_nothing(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=10_000.0, positions={}, marks={})
    res = run_once(
        conn, adapter, ForecastConfig(), syms,
        _limits(max_data_staleness_hours=0.0),  # force staleness breach
        no_trade_band_frac=0.0, exchange_leverage=5,
        state_path=tmp_path / "s.json",
        now=pd.Timestamp("2022-12-31", tz="UTC"),  # well after the last seeded bar
    )
    assert res.verdict.allowed is False
    assert res.submitted == [] and adapter.config_calls == 0


def test_run_once_isolates_per_order_failure(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=10_000.0, positions={}, marks={})
    adapter.fail_symbol = "*"  # every submit raises
    res = run_once(
        conn, adapter, ForecastConfig(), syms, _limits(),
        no_trade_band_frac=0.0, exchange_leverage=5,
        state_path=tmp_path / "s.json",
        now=pd.Timestamp("2022-02-05", tz="UTC"),
    )
    assert len(res.plan.intents) >= 1  # there is something to submit
    assert res.submitted == []  # every order failed
    assert len(res.failed) == len(res.plan.intents)  # all captured, no crash


def test_peak_equity_is_monotonic(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    save_state(p, {"peak_equity": 12_000.0, "kill_switch": False, "last_run": {}})
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=10_000.0, positions={}, marks={})
    run_once(conn, adapter, ForecastConfig(), syms, _limits(),
             no_trade_band_frac=0.0, exchange_leverage=5, state_path=p,
             now=pd.Timestamp("2022-02-05", tz="UTC"))
    assert load_state(p)["peak_equity"] == 12_000.0  # not lowered to 10k
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_xsmom_executor.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'trade.xsmom_executor'`.

- [ ] **Step 3: Write `trade/xsmom_executor.py`**

```python
"""Orchestrator for the XS-solo executor: state -> book -> plan -> overlay -> submit.

The only stateful module: reads/writes a small gitignored JSON state file
(peak equity high-water mark + kill-switch + last-run summary). Sizes the target
book off live account equity, runs the fail-closed overlay before any write, and
isolates per-order submission failures. Reads the analytics DB read-only via
`replay_targets`; never writes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.live import TargetBook
from analytics.xsmom.replay import replay_targets
from trade.overlay import AccountState, OverlayVerdict, RiskLimits, evaluate_overlay
from trade.routing import ExchangeFilters, OrderIntent, OrderPlan, build_order_plan


class _Adapter(Protocol):
    mode: str

    def get_equity(self) -> float: ...
    def get_positions(self) -> dict[str, float]: ...
    def get_marks(self, symbols: list[str]) -> dict[str, float]: ...
    def get_filters(self, symbols: list[str]) -> dict[str, ExchangeFilters]: ...
    def ensure_account_config(self, symbols: list[str], *, leverage: int) -> None: ...
    def submit_market(self, intent: OrderIntent) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ExecutionResult:
    verdict: OverlayVerdict
    plan: OrderPlan
    book: TargetBook
    submitted: list[OrderIntent]
    failed: list[tuple[OrderIntent, str]]
    equity: float
    mode: str


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"peak_equity": 0.0, "kill_switch": False, "last_run": {}}
    data: dict[str, Any] = json.loads(path.read_text())
    data.setdefault("peak_equity", 0.0)
    data.setdefault("kill_switch", False)
    data.setdefault("last_run", {})
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _data_age_hours(as_of_date: str, now: pd.Timestamp) -> float:
    # The 1d bar labelled `as_of_date` closes at the end of that UTC day.
    close = pd.Timestamp(as_of_date, tz="UTC") + pd.Timedelta(days=1)
    return float((now - close).total_seconds() / 3600.0)


def run_once(
    conn: Any,
    adapter: _Adapter,
    cfg: ForecastConfig,
    symbols: list[str],
    limits: RiskLimits,
    *,
    no_trade_band_frac: float,
    exchange_leverage: int,
    state_path: Path,
    now: pd.Timestamp | None = None,
) -> ExecutionResult:
    now = now if now is not None else pd.Timestamp(datetime.now(timezone.utc))
    state = load_state(state_path)
    prior_peak = float(state["peak_equity"])
    kill = bool(state["kill_switch"])

    equity = adapter.get_equity()
    book = replay_targets(conn, cfg, equity, symbols=symbols)
    data_age = _data_age_hours(book.as_of_date, now)

    positions = adapter.get_positions()
    marks = adapter.get_marks(symbols)
    filters = adapter.get_filters(symbols)
    plan = build_order_plan(
        book, positions, marks, filters,
        no_trade_band_frac=no_trade_band_frac, capital=equity,
    )

    account = AccountState(equity=equity, peak_equity=prior_peak, kill_switch=kill)
    verdict = evaluate_overlay(plan, book, account, limits, data_age)

    submitted: list[OrderIntent] = []
    failed: list[tuple[OrderIntent, str]] = []

    if verdict.allowed:
        adapter.ensure_account_config(symbols, leverage=exchange_leverage)
        for intent in plan.intents:
            try:
                adapter.submit_market(intent)
                submitted.append(intent)
            except Exception as exc:  # per-order isolation
                failed.append((intent, str(exc)))

    new_peak = max(prior_peak, equity)
    state["peak_equity"] = new_peak
    state["last_run"] = {
        "ts": now.isoformat(),
        "next_period_date": book.next_period_date,
        "mode": adapter.mode,
        "submitted": len(submitted),
        "skipped": len(plan.skipped),
        "failed": len(failed),
        "aborts": verdict.aborts,
    }
    save_state(state_path, state)

    return ExecutionResult(
        verdict=verdict, plan=plan, book=book, submitted=submitted,
        failed=failed, equity=equity, mode=adapter.mode,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_xsmom_executor.py -q`
Expected: PASS (6 passed). If the happy-path test submits 0 orders, widen `_seed` `n` or confirm `replay_targets` returns ≥1 active position (it does at n=400 in the slice-1 test).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
poetry run ruff format trade/xsmom_executor.py tests/trade/test_xsmom_executor.py
poetry run ruff check --fix trade/xsmom_executor.py tests/trade/test_xsmom_executor.py
poetry run mypy trade/xsmom_executor.py
git add trade/xsmom_executor.py tests/trade/test_xsmom_executor.py
git commit -m "feat(trade): XS executor orchestrator + state persistence"
git log --oneline -1
```

---

### Task 5: `tools/xsmom_execute.py` — CLI driver

**Files:**

- Create: `tools/xsmom_execute.py`
- Test: `tests/trade/test_execute_cli.py`

- [ ] **Step 1: Write the failing tests** (pure helpers only — no network)

```python
# tests/trade/test_execute_cli.py
from __future__ import annotations

from analytics.xsmom.live import TargetBook
from trade.overlay import OverlayVerdict
from trade.routing import OrderIntent, OrderPlan
from trade.xsmom_executor import ExecutionResult
from tools.xsmom_execute import check_live_gate, format_result


def _result(allowed: bool, intents: list[OrderIntent]) -> ExecutionResult:
    book = TargetBook("2026-06-21", "2026-06-22", 10_000.0, 1.0,
                      len(intents), 1.0, 0.0, [])
    plan = OrderPlan(intents, [], 1.0, 0.0)
    return ExecutionResult(OverlayVerdict(allowed, [] if allowed else ["x"]),
                           plan, book, intents if allowed else [], [],
                           10_000.0, "dry_run")


def test_live_gate_blocks_without_flag_and_env() -> None:
    assert check_live_gate("live", i_understand_live=False, allow_live_env=None) is not None


def test_live_gate_blocks_with_only_flag() -> None:
    assert check_live_gate("live", i_understand_live=True, allow_live_env=None) is not None


def test_live_gate_opens_with_flag_and_env() -> None:
    assert check_live_gate("live", i_understand_live=True, allow_live_env="1") is None


def test_non_live_modes_never_gated() -> None:
    assert check_live_gate("dry_run", i_understand_live=False, allow_live_env=None) is None
    assert check_live_gate("testnet", i_understand_live=False, allow_live_env=None) is None


def test_format_result_renders_counts() -> None:
    out = format_result(_result(True, [OrderIntent("AAAUSDT", "BUY", 1.0, False, 100.0, "open")]))
    assert "AAAUSDT" in out and "submitted" in out.lower()


def test_format_result_shows_aborts_when_blocked() -> None:
    out = format_result(_result(False, []))
    assert "abort" in out.lower() or "blocked" in out.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_execute_cli.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.xsmom_execute'`.

- [ ] **Step 3: Write `tools/xsmom_execute.py`**

```python
"""XS-solo order-routing executor CLI (P3 sub-project #3 slice 2 + #4).

Builds the overlay-gated order plan from the local read-only `analytics.db` plus
live Binance account state and either prints it (dry-run, the default) or submits
it on testnet. Mainnet (`--mode live`) is double-gated by `--i-understand-live`
AND `BINANCE_ALLOW_LIVE=1`. `--kill` / `--resume` toggle the kill-switch.

Run `buibui analytics sync --universe` first to refresh the 1d bars.

Usage::

    PYTHONPATH=. poetry run python tools/xsmom_execute.py            # dry-run
    PYTHONPATH=. poetry run python tools/xsmom_execute.py --mode testnet
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import duckdb

from analytics.forecast.config import ForecastConfig
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from trade.binance_futures import BinanceFuturesAdapter
from trade.overlay import RiskLimits
from trade.xsmom_executor import (
    ExecutionResult,
    load_state,
    run_once,
    save_state,
)

_DEFAULT_STATE_DIR = Path("docs/plans/xsmom_targets")


def check_live_gate(
    mode: str, *, i_understand_live: bool, allow_live_env: str | None
) -> str | None:
    """Return an error message if live mode is requested but not double-gated."""
    if mode != "live":
        return None
    if not i_understand_live:
        return "live mode requires --i-understand-live"
    if allow_live_env != "1":
        return "live mode requires BINANCE_ALLOW_LIVE=1 in the environment"
    return None


def format_result(res: ExecutionResult) -> str:
    lines = [
        f"XS execute [{res.mode}] — hold {res.book.next_period_date}   "
        f"equity ${res.equity:,.2f}   governor {res.book.governor:.2f}",
    ]
    if not res.verdict.allowed:
        lines.append("ORDER PLAN BLOCKED by overlay:")
        lines.extend(f"  abort: {a}" for a in res.verdict.aborts)
        return "\n".join(lines)
    lines.append(f"{'SYM':<12}{'SIDE':<6}{'QTY':>12}{'RED':>5}{'Δ$NOTIONAL':>14}  reason")
    for o in res.plan.intents:
        lines.append(
            f"{o.symbol:<12}{o.side:<6}{o.qty:>12.4f}"
            f"{('Y' if o.reduce_only else 'N'):>5}{o.delta_notional:>+14,.0f}  {o.reason}"
        )
    lines.append(
        f"submitted={len(res.submitted)}  skipped={len(res.plan.skipped)}  "
        f"failed={len(res.failed)}"
    )
    for intent, err in res.failed:
        lines.append(f"  FAILED {intent.symbol} {intent.side} {intent.qty}: {err}")
    return "\n".join(lines)


def _build_client(mode: str):  # type: ignore[no-untyped-def]
    from utils.binance_client import create_client

    if mode == "testnet":
        from binance.client import Client

        key = os.environ["BINANCE_TESTNET_API_KEY"]
        secret = os.environ["BINANCE_TESTNET_API_SECRET"]
        return Client(key, secret, testnet=True)
    return create_client()  # mainnet (reads only in dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--symbols", type=str, default=None)
    parser.add_argument("--mode", choices=("dry_run", "testnet", "live"),
                        default="dry_run")
    parser.add_argument("--no-trade-band", type=float, default=0.005)
    parser.add_argument("--exchange-leverage", type=int, default=5)
    parser.add_argument("--max-gross-leverage", type=float, default=3.0)
    parser.add_argument("--max-position-notional-frac", type=float, default=0.5)
    parser.add_argument("--max-drawdown-frac", type=float, default=0.25)
    parser.add_argument("--max-run-turnover-frac", type=float, default=1.0)
    parser.add_argument("--max-data-staleness-hours", type=float, default=36.0)
    parser.add_argument("--i-understand-live", action="store_true")
    parser.add_argument("--kill", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--state-dir", type=Path, default=_DEFAULT_STATE_DIR)
    args = parser.parse_args()

    state_path = args.state_dir / f"execution_state_{args.mode}.json"

    if args.kill or args.resume:
        state = load_state(state_path)
        state["kill_switch"] = bool(args.kill)
        save_state(state_path, state)
        print(f"kill_switch = {state['kill_switch']} ({state_path})")
        return

    gate_err = check_live_gate(
        args.mode, i_understand_live=args.i_understand_live,
        allow_live_env=os.environ.get("BINANCE_ALLOW_LIVE"),
    )
    if gate_err:
        raise SystemExit(gate_err)

    cfg = ForecastConfig.from_toml(args.config) if args.config else ForecastConfig()
    symbols = args.symbols.split(",") if args.symbols else load_universe()
    limits = RiskLimits(
        max_gross_leverage=args.max_gross_leverage,
        max_position_notional_frac=args.max_position_notional_frac,
        max_drawdown_frac=args.max_drawdown_frac,
        max_run_turnover_frac=args.max_run_turnover_frac,
        max_data_staleness_hours=args.max_data_staleness_hours,
    )

    adapter = BinanceFuturesAdapter(_build_client(args.mode), mode=args.mode)
    with duckdb.connect(str(args.db), read_only=True) as conn:
        res = run_once(
            conn, adapter, cfg, symbols, limits,
            no_trade_band_frac=args.no_trade_band,
            exchange_leverage=args.exchange_leverage, state_path=state_path,
        )
    print(format_result(res))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/trade/test_execute_cli.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
poetry run ruff format tools/xsmom_execute.py tests/trade/test_execute_cli.py
poetry run ruff check --fix tools/xsmom_execute.py tests/trade/test_execute_cli.py
poetry run mypy tools/xsmom_execute.py
git add tools/xsmom_execute.py tests/trade/test_execute_cli.py
git commit -m "feat(trade): xsmom_execute CLI with live double-gate + result rendering"
git log --oneline -1
```

---

### Task 6: Makefile target, docs, gitignore, and full DoD gate

**Files:**

- Modify: `Makefile`
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Verify: `.gitignore` already covers the state file (it lives under `docs/plans/`, which slice 1 already ignores for snapshots — confirm).

- [ ] **Step 1: Add the Makefile target**

Find the existing `buibui-xsmom-targets` target and add directly below it. **The recipe
line must be indented with a single literal TAB (not the spaces shown here) — Make
requires it:**

```make
.PHONY: buibui-xsmom-execute
buibui-xsmom-execute:  ## XS-solo order-routing executor (dry-run by default; MODE=testnet to submit)
    PYTHONPATH=. poetry run python tools/xsmom_execute.py $(if $(MODE),--mode $(MODE),)
```

(Match the surrounding target's variable-passthrough style; if `buibui-xsmom-targets` uses a different override convention, mirror it exactly.)

- [ ] **Step 2: Confirm the state file is gitignored**

Run: `git check-ignore docs/plans/xsmom_targets/execution_state_dry_run.json`
Expected: the path is printed (ignored). If nothing prints, add `docs/plans/` (or the existing snapshot glob) coverage to `.gitignore` to match how slice-1 snapshots are ignored, then re-run.

- [ ] **Step 3: Document the `trade/` execution layer in CLAUDE.md**

Under the Project Structure section, replace the stale `trade/open_trades.py` bullet with a `trade/` package description covering: `routing.py` (pure order-plan), `overlay.py` (pure fail-closed risk gate), `binance_futures.py` (injectable adapter, dry-run/testnet/live), `xsmom_executor.py` (orchestrator + state), and that it consumes `analytics/xsmom/live.py::TargetBook` while keeping `analytics/xsmom/` pure. Note `tools/xsmom_execute.py` + `make buibui-xsmom-execute`, dry-run default, testnet-validated, mainnet double-gated.

- [ ] **Step 4: Document the CLI in README.md**

Add a short subsection near the other `tools/` / `make buibui-*` entries describing `make buibui-xsmom-execute` (and the `--mode testnet` validation step + the live double-gate). Keep it parallel to the `buibui-xsmom-targets` entry.

- [ ] **Step 5: Run the full DoD gate**

```bash
make lint-py
make typecheck
make test
make test-regression
make lint-md
```

Expected: lint-py ✓; typecheck ✓ (mypy strict, count rises by ~5 files); `make test` green with the new `tests/trade/` modules; **`make test-regression` goldens UNMOVED** (this slice is additive — no engine/golden change); lint-md ✓. State each result plainly; if any step is red, stop and fix before committing.

- [ ] **Step 6: Commit the docs + Makefile**

```bash
git add Makefile CLAUDE.md README.md .gitignore
git commit -m "docs(trade): document the XS execution layer + buibui-xsmom-execute target"
git log --oneline -1
```

---

## Self-review notes

- **Spec coverage:** routing (Task 1), overlay all six checks (Task 2), adapter incl. one-way/cross/hedge-abort/-4046 (Task 3), orchestrator + run flow + state + per-order isolation + drawdown peak (Task 4), modes + live double-gate + CLI + rendering (Task 5), Make/docs/gitignore/DoD (Task 6). Capital-basis = live equity is wired in Task 4 (`replay_targets(conn, cfg, equity, ...)`). Safety invariants 1–6 map to: dry-run default (Task 5 arg default), overlay-before-write (Task 4 ordering), live double-gate (Task 5), per-order isolation (Task 4), read-only DB (`duckdb.connect(..., read_only=True)` in Task 5), reduce-only on trims/closes (Task 1).
- **Type consistency:** `OrderIntent`/`OrderPlan`/`ExchangeFilters` (Task 1) are imported unchanged by Tasks 2/3/4/5; `RiskLimits`/`AccountState`/`OverlayVerdict`/`evaluate_overlay` (Task 2) used unchanged by Tasks 4/5; `BinanceFuturesAdapter(client, mode=...)` (Task 3) matches the `_Adapter` Protocol + CLI construction (Tasks 4/5); `run_once(...)` keyword args match between Task 4 definition and Task 5 call; `ExecutionResult` fields match between Task 4 and the Task 5 formatter.
- **No placeholders:** every code step is complete and runnable; no TBD/TODO.
- **Testnet caveat for the executor:** `_build_client` reads `BINANCE_TESTNET_API_KEY/SECRET` from the env; the testnet end-to-end run is a manual validation step the operator performs after merge (not part of the offline test suite). Document this in the PR test plan.
