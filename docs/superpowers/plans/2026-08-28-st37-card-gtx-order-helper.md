# ST37 Card GTX Order Helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An interactive CLI picklist that places unexpired TRADE cards as post-only (GTX)
limit entry orders on Binance USDT-M futures, hedge-mode-aware, XS-collision-guarded, with
every placement and terminal state instrumented in `docs/plans/card-orders.jsonl`.

**Architecture:** Pure logic in a new `card/orders.py` (scan → selection → guards →
placement rows), exchange I/O through the existing `BinanceFuturesAdapter` (one delta:
optional `position_side`), thin CLI in a new `cli/card_orders.py` registering sibling
commands `card-place` / `card-orders` (the `card` command keeps its positional SYMBOL).

**Tech Stack:** Python 3.11, dataclasses, `python-binance` via injected `MagicMock` in
tests, pytest + unittest.mock, tomllib for `config/universe.toml`.

**Spec:** `docs/superpowers/specs/2026-08-28-st37-card-gtx-order-helper-design.md`

## Global Constraints

- **Branch:** implementation goes on a NEW branch off latest `main`:
  `feat/st37-card-gtx-order-helper` (the spec+plan docs branch is a separate PR).
- mypy strict: every function fully annotated, `-> None` on tests.
- `make lint-py` and `make typecheck` must pass after every task (seconds, foreground).
- Tests make NO network calls — every exchange client is a `MagicMock` passed in directly.
- While iterating run only the targeted test files; the full suite runs ONCE per branch as
  `make preflight` at `/post-branch` Step 7. Never `make test` then `make preflight`.
- `make test-regression` branch: NOT required — `card/`, `trade/`, `cli/` are outside the
  regression paths filter (which covers `analytics/**`, configs, fixtures). State this in
  the PR body.
- Conventional commits (`feat:` / `test:` / `docs:`); never edit the Python tree while a
  suite run is in flight.
- The exchange rejection code for a crossing GTX order is expected to be `-5022`
  ("could not be executed as maker"). **Verify against the installed `python-binance`
  before relying on it** (Task 5 carries the verification step).

---

### Task 1: `OrderIntent.position_side` + hedge-shaped `submit()`

The operator's account is hedge-mode for its entire history (ST109). A dual-side account
rejects an order with no `positionSide`, and rejects `reduceOnly` as a parameter. The XS
executor stays one-way and passes nothing — its wire shape must be byte-identical.

**Files:**

- Modify: `trade/routing.py:29-36` (`OrderIntent`)
- Modify: `trade/binance_futures.py:137-158` (`submit`)
- Test: `tests/trade/test_binance_futures.py`

**Interfaces:**

- Produces: `OrderIntent(..., position_side: str | None = None)` — `"LONG" | "SHORT"` or
  `None`; `submit()` sends `positionSide` and omits `reduceOnly` when it is set.

- [ ] **Step 1: Write the failing tests**

```python
def test_submit_limit_hedge_mode_sends_position_side_and_omits_reduce_only() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent(
        "AAAUSDT", "BUY", 2.0, False, 200.0, "card", "LIMIT", position_side="LONG"
    )
    adapter.submit(intent, price=99.98)
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["positionSide"] == "LONG"
    assert "reduceOnly" not in kwargs  # hedge mode rejects the parameter
    assert kwargs["timeInForce"] == "GTX"


def test_submit_one_way_shape_is_unchanged_when_position_side_absent() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent("AAAUSDT", "SELL", 2.0, False, -200.0, "open", "LIMIT")
    adapter.submit(intent, price=99.98)
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["reduceOnly"] is False
    assert "positionSide" not in kwargs
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/trade/test_binance_futures.py -q`
Expected: FAIL — `OrderIntent.__init__() got an unexpected keyword argument 'position_side'`

- [ ] **Step 3: Implement**

In `trade/routing.py`, add the field LAST so existing positional construction keeps working:

```python
@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str  # "BUY" | "SELL"
    qty: float
    reduce_only: bool
    delta_notional: float
    reason: str  # "open" | "rebalance" | "close" | "skip:<why>" | "card"
    order_type: str = "MARKET"  # "LIMIT" (post-only) | "MARKET"
    position_side: str | None = None  # "LONG" | "SHORT" on a hedge-mode account
```

In `trade/binance_futures.py::submit`, replace the params construction:

```python
        params: dict[str, Any] = {
            "symbol": intent.symbol,
            "side": intent.side,
            "type": intent.order_type,
            "quantity": intent.qty,
        }
        if intent.position_side is None:
            params["reduceOnly"] = intent.reduce_only
        else:
            # Hedge-mode: positionSide carries the direction and the account
            # REJECTS reduceOnly as a parameter (the side implies it).
            params["positionSide"] = intent.position_side
```

(keep the existing LIMIT price/GTX block after it, and the dry_run dict above it —
add `"positionSide": intent.position_side` to the dry_run return dict too.)

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/trade/test_binance_futures.py tests/trade/test_xsmom_executor.py -q`
Expected: ALL PASS (the executor tests pin the one-way shape stayed byte-identical)

- [ ] **Step 5: Commit**

```bash
git add trade/routing.py trade/binance_futures.py tests/trade/test_binance_futures.py
git commit -m "feat: teach OrderIntent and submit() the hedge-mode positionSide shape"
```

---

### Task 2: candidate scan — `card/orders.py`

**Files:**

- Create: `card/orders.py`
- Test: `tests/test_card_orders.py` (create)

**Interfaces:**

- Consumes: `card.card._parse_iso_ms(text: str) -> int | None` (same-package import, the
  repo's established pattern — `tools/decay_review.py` imports `_sharpe` the same way).
- Produces: `CardCandidate` dataclass and
  `scan_candidates(card_rows, order_rows, now_ms) -> list[CardCandidate]`;
  `read_jsonl(path: Path) -> list[dict[str, Any]]`;
  constants `DEFAULT_ORDERS_PATH`, `XS_LIVE_MARKER`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for card/orders.py — scan, selection, guards, placement, refresh."""

from __future__ import annotations

from typing import Any

from card.orders import CardCandidate, scan_candidates

NOW_MS = 1_756_000_000_000


def _trade_row(symbol: str = "BTCUSDT", gen_ms: int = NOW_MS - 60_000, **kw: Any) -> dict[str, Any]:
    card = {
        "direction": kw.get("direction", "long"),
        "entry": kw.get("entry", 100.0),
        "sl": kw.get("sl", 98.0),
        "valid_until_utc": kw.get("valid_until_utc", "2099-01-01T00:00:00Z"),
    }
    return {
        "verdict": kw.get("verdict", "TRADE"),
        "symbol": symbol,
        "generated_at_ms": gen_ms,
        "state_digest": "abc123",
        "size_units": kw.get("size_units", 0.5),
        "risk_usd": kw.get("risk_usd", 1.0),
        "risk_frac": kw.get("risk_frac", 0.0025),
        "card": card,
    }


def test_scan_keeps_only_unexpired_trade_cards() -> None:
    rows = [
        _trade_row(),
        _trade_row(verdict="NO_TRADE"),
        _trade_row(verdict="VETOED"),
        _trade_row(valid_until_utc="2020-01-01T00:00:00Z"),  # expired
        _trade_row(valid_until_utc=None),  # unparseable -> skipped
    ]
    out = scan_candidates(rows, [], NOW_MS)
    assert len(out) == 1
    assert out[0].symbol == "BTCUSDT"
    assert out[0].direction == "long"


def test_scan_anti_joins_already_placed_cards() -> None:
    row = _trade_row()
    placed = [{
        "kind": "placement",
        "symbol": "BTCUSDT",
        "card_generated_at_ms": row["generated_at_ms"],
    }]
    assert scan_candidates([row], placed, NOW_MS) == []
    # a DIFFERENT card on the same symbol still presents
    other = _trade_row(gen_ms=row["generated_at_ms"] + 1)
    assert len(scan_candidates([other], placed, NOW_MS)) == 1


def test_scan_skips_rows_missing_price_or_size() -> None:
    assert scan_candidates([_trade_row(size_units=None)], [], NOW_MS) == []
    assert scan_candidates([_trade_row(entry=None)], [], NOW_MS) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_card_orders.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'card.orders'`

- [ ] **Step 3: Implement `card/orders.py`**

```python
"""Post-card GTX order helper: scan, guards, placement, instrumentation.

Spec: docs/superpowers/specs/2026-08-28-st37-card-gtx-order-helper-design.md
Places picked TRADE cards as post-only (GTX) limit entries and records every
placement + terminal state in an append-only jsonl ledger, joined back to the
card by (symbol, generated_at_ms).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from card.card import _parse_iso_ms

DEFAULT_ORDERS_PATH = "docs/plans/card-orders.jsonl"
# The XS executor writes execution_state_{mode}.json per mode; a live-mode run
# leaves this marker. Testnet orders live on a different venue and cannot
# collide with mainnet card orders, so only "live" gates.
XS_LIVE_MARKER = Path("docs/plans/xsmom_targets/execution_state_live.json")


@dataclass(frozen=True)
class CardCandidate:
    symbol: str
    direction: str  # "long" | "short"
    entry: float
    sl: float
    qty: float
    risk_usd: float | None
    risk_frac: float | None
    valid_until_ms: int
    generated_at_ms: int
    state_digest: str


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a jsonl file, tolerating a torn tail line (append-only ledgers)."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def scan_candidates(
    card_rows: list[dict[str, Any]],
    order_rows: list[dict[str, Any]],
    now_ms: int,
) -> list[CardCandidate]:
    """Unexpired TRADE cards not already placed.

    Anti-join on (symbol, generated_at_ms): a gtx_rejected placement row also
    suppresses re-presentation on purpose — a rejection means price is already
    through the level, so the setup is stale by definition.
    """
    placed = {
        (r.get("symbol"), r.get("card_generated_at_ms"))
        for r in order_rows
        if r.get("kind") == "placement"
    }
    out: list[CardCandidate] = []
    for row in card_rows:
        if row.get("verdict") != "TRADE":
            continue
        card = row.get("card") or {}
        expiry = _parse_iso_ms(str(card.get("valid_until_utc") or ""))
        if expiry is None or expiry <= now_ms:
            continue
        if (row.get("symbol"), row.get("generated_at_ms")) in placed:
            continue
        direction = card.get("direction")
        entry = card.get("entry")
        sl = card.get("sl")
        qty = row.get("size_units")
        if direction not in ("long", "short") or not entry or not sl or not qty:
            continue
        risk_usd = row.get("risk_usd")
        risk_frac = row.get("risk_frac")
        out.append(
            CardCandidate(
                symbol=str(row["symbol"]),
                direction=str(direction),
                entry=float(entry),
                sl=float(sl),
                qty=float(qty),
                risk_usd=float(risk_usd) if risk_usd is not None else None,
                risk_frac=float(risk_frac) if risk_frac is not None else None,
                valid_until_ms=expiry,
                generated_at_ms=int(row["generated_at_ms"]),
                state_digest=str(row.get("state_digest") or ""),
            )
        )
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_card_orders.py -q` — Expected: PASS
Then: `make lint-py && make typecheck` — Expected: clean

- [ ] **Step 5: Commit**

```bash
git add card/orders.py tests/test_card_orders.py
git commit -m "feat: scan unexpired unplaced TRADE cards into placement candidates"
```

---

### Task 3: selection parsing + aggregate risk

**Files:**

- Modify: `card/orders.py`
- Test: `tests/test_card_orders.py`

**Interfaces:**

- Produces: `parse_selection(text: str, n: int) -> list[int] | None` — `""` → `[]` (none,
  the default), 1-based tokens → 0-based unique indices, `None` on any invalid token;
  `AggregateRisk` dataclass and
  `aggregate_risk(selected: list[CardCandidate], equity: float | None) -> AggregateRisk`.

- [ ] **Step 1: Write the failing tests**

```python
from card.orders import AggregateRisk, aggregate_risk, parse_selection


def test_parse_selection_empty_means_none_selected() -> None:
    assert parse_selection("", 5) == []
    assert parse_selection("   ", 5) == []


def test_parse_selection_accepts_spaces_and_commas_dedups() -> None:
    assert parse_selection("1, 3 3", 5) == [0, 2]


def test_parse_selection_rejects_out_of_range_and_garbage() -> None:
    assert parse_selection("0", 5) is None
    assert parse_selection("6", 5) is None
    assert parse_selection("all", 5) is None


def test_aggregate_risk_stacks_same_symbol_and_side() -> None:
    # the 08-17e shape: six 0.25% choices that are three bets doubled
    cands = [
        _cand("BTCUSDT", "short"), _cand("BTCUSDT", "short"),
        _cand("ETHUSDT", "long"), _cand("ETHUSDT", "long"),
        _cand("SOLUSDT", "short"), _cand("SOLUSDT", "short"),
    ]
    agg = aggregate_risk(cands, equity=1000.0)
    assert agg.total_risk_usd == 15.0  # 6 x 2.5
    assert agg.total_risk_frac == 0.015
    assert ("BTCUSDT", "short", 5.0) in agg.by_bet
    assert len(agg.by_bet) == 3  # three bets, not six


def test_aggregate_risk_without_equity_suppresses_fraction() -> None:
    agg = aggregate_risk([_cand("BTCUSDT", "long")], equity=None)
    assert agg.total_risk_frac is None
```

with the helper (add near `_trade_row`):

```python
def _cand(symbol: str, direction: str, risk_usd: float = 2.5) -> CardCandidate:
    return CardCandidate(
        symbol=symbol, direction=direction, entry=100.0, sl=98.0, qty=0.5,
        risk_usd=risk_usd, risk_frac=None, valid_until_ms=NOW_MS + 3_600_000,
        generated_at_ms=NOW_MS - 60_000, state_digest="abc123",
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_card_orders.py -q`
Expected: FAIL — `ImportError: cannot import name 'parse_selection'`

- [ ] **Step 3: Implement in `card/orders.py`**

```python
@dataclass(frozen=True)
class AggregateRisk:
    total_risk_usd: float
    total_risk_frac: float | None  # None when equity is unknown
    by_bet: list[tuple[str, str, float]]  # (symbol, direction, summed risk_usd)


def parse_selection(text: str, n: int) -> list[int] | None:
    """'' -> [] (default none, never pre-selected); invalid input -> None."""
    text = text.strip()
    if not text:
        return []
    picks: list[int] = []
    for tok in text.replace(",", " ").split():
        if not tok.isdigit() or not (1 <= int(tok) <= n):
            return None
        idx = int(tok) - 1
        if idx not in picks:
            picks.append(idx)
    return picks


def aggregate_risk(
    selected: list[CardCandidate], equity: float | None
) -> AggregateRisk:
    """Total + per-(symbol, direction) stacking — the view a flat list hides."""
    total = sum(c.risk_usd or 0.0 for c in selected)
    bets: dict[tuple[str, str], float] = {}
    for c in selected:
        key = (c.symbol, c.direction)
        bets[key] = bets.get(key, 0.0) + (c.risk_usd or 0.0)
    frac = (total / equity) if equity and equity > 0.0 else None
    return AggregateRisk(
        total_risk_usd=total,
        total_risk_frac=frac,
        by_bet=[(s, d, r) for (s, d), r in sorted(bets.items())],
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_card_orders.py -q` — Expected: PASS
Then: `make lint-py && make typecheck` — Expected: clean

- [ ] **Step 5: Commit**

```bash
git add card/orders.py tests/test_card_orders.py
git commit -m "feat: selection parser and aggregate-risk stacking view for the picklist"
```

---

### Task 4: placement guards

**Files:**

- Modify: `card/orders.py`
- Test: `tests/test_card_orders.py`

**Interfaces:**

- Consumes: `portfolio.sizing.round_down_to_step(qty, step) -> float`,
  `portfolio.sizing.round_to_tick(price, tick, side) -> float`,
  `trade.routing.ExchangeFilters` (fields: `symbol, qty_step, min_qty, min_notional,
  price_tick`).
- Produces: `PlacementDecision` dataclass and
  `check_placement(cand, filt, *, managed, xs_live_marker, now_ms) -> PlacementDecision`.

- [ ] **Step 1: Write the failing tests**

```python
from trade.routing import ExchangeFilters

from card.orders import check_placement

_FILT = ExchangeFilters(
    symbol="BTCUSDT", qty_step=0.001, min_qty=0.001,
    min_notional=100.0, price_tick=0.1,
)


def test_xs_guard_four_quadrants() -> None:
    c = _cand("BTCUSDT", "long")
    # managed AND live marker -> VETO
    d = check_placement(c, _FILT, managed=True, xs_live_marker=True, now_ms=NOW_MS)
    assert any("XS" in v for v in d.vetoes)
    # managed, no marker -> WARN only (heard before XS go-live)
    d = check_placement(c, _FILT, managed=True, xs_live_marker=False, now_ms=NOW_MS)
    assert d.vetoes == [] and any("XS managed set" in w for w in d.warnings)
    # unmanaged: marker state is irrelevant either way
    for marker in (True, False):
        d = check_placement(c, _FILT, managed=False, xs_live_marker=marker, now_ms=NOW_MS)
        assert d.vetoes == []
        assert not any("XS" in w for w in d.warnings)


def test_expiry_rechecked_at_placement_instant() -> None:
    c = _cand("BTCUSDT", "long")
    d = check_placement(
        c, _FILT, managed=False, xs_live_marker=False,
        now_ms=c.valid_until_ms + 1,  # expired while the operator thought
    )
    assert any("expired" in v for v in d.vetoes)


def test_rounding_restates_risk_and_warns_when_moved() -> None:
    c = CardCandidate(
        symbol="BTCUSDT", direction="long", entry=100.05, sl=98.0, qty=0.5015,
        risk_usd=1.0, risk_frac=None, valid_until_ms=NOW_MS + 3_600_000,
        generated_at_ms=NOW_MS, state_digest="d",
    )
    d = check_placement(c, _FILT, managed=False, xs_live_marker=False, now_ms=NOW_MS)
    assert d.qty == 0.501            # floored to LOT_SIZE step
    assert d.price == 100.0          # long=BUY floors to tick, never crosses up
    assert d.risk_usd == round(0.501 * (100.0 - 98.0), 8)  # restated from ROUNDED
    assert d.warnings                # says the numbers moved


def test_sub_lot_quantity_vetoes() -> None:
    c = _cand("BTCUSDT", "long")
    tiny = CardCandidate(**{**c.__dict__, "qty": 0.0004})
    d = check_placement(tiny, _FILT, managed=False, xs_live_marker=False, now_ms=NOW_MS)
    assert any("sub-lot" in v for v in d.vetoes)


def test_missing_filters_warns_and_keeps_raw_numbers() -> None:
    c = _cand("BTCUSDT", "long")
    d = check_placement(c, None, managed=False, xs_live_marker=False, now_ms=NOW_MS)
    assert d.vetoes == []
    assert d.qty == c.qty and d.price == c.entry
    assert any("filters unavailable" in w for w in d.warnings)
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_card_orders.py -q`
Expected: FAIL — `ImportError: cannot import name 'check_placement'`

- [ ] **Step 3: Implement in `card/orders.py`**

```python
from portfolio.sizing import round_down_to_step, round_to_tick
from trade.routing import ExchangeFilters


@dataclass(frozen=True)
class PlacementDecision:
    candidate: CardCandidate
    qty: float
    price: float
    risk_usd: float | None
    vetoes: list[str]
    warnings: list[str]


def check_placement(
    cand: CardCandidate,
    filt: ExchangeFilters | None,
    *,
    managed: bool,
    xs_live_marker: bool,
    now_ms: int,
) -> PlacementDecision:
    """Every hard rule in code, veto-style, each naming its numbers."""
    vetoes: list[str] = []
    warnings: list[str] = []

    # (a) expiry at the PLACEMENT instant — the card veto only ever compared
    # against generated_at_ms, and a card can expire while the operator thinks.
    if cand.valid_until_ms <= now_ms:
        vetoes.append(
            f"card expired at placement ({(now_ms - cand.valid_until_ms) / 1000:.0f}s past valid_until)"
        )

    # (b) XS collision: cancel_open_orders is symbol-WIDE, so a live XS run
    # cancels card orders on any managed symbol. Veto on managed AND live
    # marker; always warn on managed so the hazard is heard before go-live.
    if managed and xs_live_marker:
        vetoes.append(
            f"{cand.symbol} is in the XS managed set and a live XS execution "
            "state exists - a live XS run cancels ALL open orders on this symbol"
        )
    elif managed:
        warnings.append(
            f"{cand.symbol} is in the XS managed set; a future live XS run "
            "would cancel this order"
        )

    # (d) exchange rounding, restated risk (the card's own sizing rule applied
    # at placement time)
    qty, price, risk_usd = cand.qty, cand.entry, cand.risk_usd
    if filt is None:
        warnings.append("exchange filters unavailable - placing unrounded numbers")
    else:
        side = "BUY" if cand.direction == "long" else "SELL"
        qty = round_down_to_step(cand.qty, filt.qty_step)
        price = round_to_tick(cand.entry, filt.price_tick, side)
        if qty <= 0.0 or qty < filt.min_qty:
            vetoes.append(
                f"sub-lot after rounding: {cand.qty} -> {qty} against step {filt.qty_step}"
            )
        else:
            risk_usd = round(qty * abs(price - cand.sl), 8)
            if qty != cand.qty or price != cand.entry:
                warnings.append(
                    f"rounded qty {cand.qty} -> {qty}, price {cand.entry} -> {price}; "
                    f"risk restated to {risk_usd}"
                )
    return PlacementDecision(
        candidate=cand, qty=qty, price=price, risk_usd=risk_usd,
        vetoes=vetoes, warnings=warnings,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_card_orders.py -q` — Expected: PASS
Then: `make lint-py && make typecheck` — Expected: clean

- [ ] **Step 5: Commit**

```bash
git add card/orders.py tests/test_card_orders.py
git commit -m "feat: veto-style placement guards - expiry, XS collision, exchange rounding"
```

---

### Task 5: placement orchestration + ledger rows

**Files:**

- Modify: `card/orders.py`
- Test: `tests/test_card_orders.py`

**Interfaces:**

- Consumes: `BinanceFuturesAdapter.submit(intent, price)` (Task 1 shape),
  `card.ledger._append_line(path: Path, obj: dict[str, object]) -> None`,
  `trade.binance_futures.APIError` (has `.code`).
- Produces:
  `place_orders(adapter, decisions, *, ledger_path, dual_side, marks, books, positions, equity, now_ms) -> list[dict[str, Any]]`
  — returns the rows it appended (empty list in dry_run; nothing is written then).

- [ ] **Step 1: Verify the GTX rejection shape (no test yet)**

Run: `poetry run python -c "from binance.error import ClientError" 2>/dev/null; poetry run python -c "import binance, inspect; print(binance.__version__ if hasattr(binance,'__version__') else 'n/a')"`
and read how `trade/binance_futures.py` imports `APIError` (top of file). The plan assumes
the post-only rejection surfaces as an `APIError` with `code == -5022`. If the installed
library raises a different exception type or code for "Order could not be executed as
maker", adjust the constant `_POST_ONLY_REJECT` and the `except` clause below to match,
and record what you found in the commit body.

- [ ] **Step 2: Write the failing tests**

```python
from pathlib import Path
from unittest.mock import MagicMock

from trade.binance_futures import BinanceFuturesAdapter

from card.orders import PlacementDecision, place_orders, read_jsonl


def _decision(symbol: str = "BTCUSDT", direction: str = "long") -> PlacementDecision:
    return check_placement(
        _cand(symbol, direction), _FILT,
        managed=False, xs_live_marker=False, now_ms=NOW_MS,
    )


def test_place_orders_writes_placement_row_with_instrumentation(tmp_path: Path) -> None:
    client = MagicMock()
    client.futures_create_order.return_value = {"orderId": 42}
    adapter = BinanceFuturesAdapter(client, mode="live")
    ledger = tmp_path / "card-orders.jsonl"
    rows = place_orders(
        adapter, [_decision()], ledger_path=ledger, dual_side=True,
        marks={"BTCUSDT": 100.2}, books={"BTCUSDT": (100.1, 100.3)},
        positions={"BTCUSDT": 0.25}, equity=550.0, now_ms=NOW_MS,
    )
    assert len(rows) == 1
    row = read_jsonl(ledger)[0]
    assert row["kind"] == "placement" and row["order_id"] == 42
    assert row["card_generated_at_ms"] == NOW_MS - 60_000
    assert row["mark_at_placement"] == 100.2
    assert row["bid_at_placement"] == 100.1 and row["ask_at_placement"] == 100.3
    assert row["position_at_placement"] == 0.25
    assert row["equity_at_placement"] == 550.0
    assert row["position_mode"] == "hedge"
    # hedge-mode wire shape came from Task 1
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["positionSide"] == "LONG" and "reduceOnly" not in kwargs


def test_gtx_rejection_is_recorded_not_raised(tmp_path: Path) -> None:
    from trade.binance_futures import APIError

    err = APIError("Post Only order will be rejected")
    err.code = -5022
    client = MagicMock()
    client.futures_create_order.side_effect = err
    adapter = BinanceFuturesAdapter(client, mode="live")
    ledger = tmp_path / "card-orders.jsonl"
    rows = place_orders(
        adapter, [_decision()], ledger_path=ledger, dual_side=False,
        marks={}, books={}, positions={}, equity=None, now_ms=NOW_MS,
    )
    row = rows[0]
    assert row["order_id"] is None
    assert row["terminal_reason"] == "gtx_rejected"
    # and the rejected card no longer re-presents: same anti-join key
    assert row["kind"] == "placement" and row["symbol"] == "BTCUSDT"


def test_vetoed_decisions_are_skipped_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    vetoed = check_placement(
        _cand("BTCUSDT", "long"), _FILT,
        managed=True, xs_live_marker=True, now_ms=NOW_MS,
    )
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="live")
    ledger = tmp_path / "card-orders.jsonl"
    assert place_orders(
        adapter, [vetoed], ledger_path=ledger, dual_side=False,
        marks={}, books={}, positions={}, equity=None, now_ms=NOW_MS,
    ) == []
    client.futures_create_order.assert_not_called()

    dry = BinanceFuturesAdapter(MagicMock(), mode="dry_run")
    assert place_orders(
        dry, [_decision()], ledger_path=ledger, dual_side=False,
        marks={}, books={}, positions={}, equity=None, now_ms=NOW_MS,
    ) == []
    assert not ledger.exists()  # a dry run must not pollute the ledger
```

- [ ] **Step 3: Run to verify failure**

Run: `poetry run pytest tests/test_card_orders.py -q`
Expected: FAIL — `ImportError: cannot import name 'place_orders'`

- [ ] **Step 4: Implement in `card/orders.py`**

```python
from card.ledger import _append_line
from trade.binance_futures import APIError, BinanceFuturesAdapter
from trade.routing import OrderIntent

# Binance rejects a GTX order that would cross as an error rather than resting
# it: "Due to the order could not be executed as maker, the Post Only order
# will be rejected." Verified against the installed python-binance in Task 5
# Step 1 - adjust here if the library surfaces it differently.
_POST_ONLY_REJECT = -5022


def place_orders(
    adapter: BinanceFuturesAdapter,
    decisions: list[PlacementDecision],
    *,
    ledger_path: Path,
    dual_side: bool,
    marks: dict[str, float],
    books: dict[str, tuple[float, float]],
    positions: dict[str, float],
    equity: float | None,
    now_ms: int,
) -> list[dict[str, Any]]:
    """Submit each un-vetoed decision as a GTX limit; append placement rows.

    A GTX rejection is INFORMATION, not an error: price is already through the
    structural level, so the setup is stale rather than the order broken. It
    is recorded with order_id None + terminal_reason "gtx_rejected", which
    also suppresses re-presentation via the scan's anti-join.
    Dry-run placements return [] and write nothing.
    """
    written: list[dict[str, Any]] = []
    for d in decisions:
        if d.vetoes:
            continue
        cand = d.candidate
        side = "BUY" if cand.direction == "long" else "SELL"
        position_side = (
            ("LONG" if cand.direction == "long" else "SHORT") if dual_side else None
        )
        intent = OrderIntent(
            cand.symbol, side, d.qty, False, 0.0, "card", "LIMIT",
            position_side=position_side,
        )
        order_id: int | None = None
        terminal_reason: str | None = None
        try:
            resp = adapter.submit(intent, price=d.price)
        except APIError as exc:
            if getattr(exc, "code", None) != _POST_ONLY_REJECT:
                raise
            terminal_reason = "gtx_rejected"
        else:
            if resp.get("dryRun"):
                continue  # never pollute the ledger from a dry run
            order_id = int(resp["orderId"])
        bid, ask = books.get(cand.symbol, (None, None))
        row: dict[str, Any] = {
            "kind": "placement",
            "order_id": order_id,
            "symbol": cand.symbol,
            "direction": cand.direction,
            "card_generated_at_ms": cand.generated_at_ms,
            "state_digest": cand.state_digest,
            "placed_at_ms": now_ms,
            "limit_price": d.price,
            "qty": d.qty,
            "risk_usd": d.risk_usd,
            "mark_at_placement": marks.get(cand.symbol),
            "bid_at_placement": bid,
            "ask_at_placement": ask,
            "position_at_placement": positions.get(cand.symbol, 0.0),
            "equity_at_placement": equity,
            "position_mode": "hedge" if dual_side else "one_way",
            "warnings": d.warnings,
        }
        if terminal_reason is not None:
            row["terminal_reason"] = terminal_reason
            row["terminal_at_ms"] = now_ms
        _append_line(ledger_path, row)
        written.append(row)
    return written
```

Note: `books.get(cand.symbol, (None, None))` needs the local
`bid, ask = books.get(...)` unpack typed as `tuple[float | None, float | None]` — if mypy
strict complains about the default, write it as:

```python
        top = books.get(cand.symbol)
        bid = top[0] if top else None
        ask = top[1] if top else None
```

- [ ] **Step 5: Run to verify pass**

Run: `poetry run pytest tests/test_card_orders.py -q` — Expected: PASS
Then: `make lint-py && make typecheck` — Expected: clean

- [ ] **Step 6: Commit**

```bash
git add card/orders.py tests/test_card_orders.py
git commit -m "feat: place picked cards as GTX limits with full placement instrumentation"
```

---

### Task 6: terminal-state refresh

**Files:**

- Modify: `card/orders.py`
- Test: `tests/test_card_orders.py`

**Interfaces:**

- Consumes: `client.futures_get_order(symbol=..., orderId=...)` returning at least
  `{"status", "avgPrice", "executedQty", "updateTime"}`.
- Produces:
  `refresh_orders(client: Any, ledger_path: Path, *, marks: dict[str, float], now_ms: int) -> list[dict[str, Any]]`
  — appends one terminal row per newly-closed order, returns them; a still-working order
  (NEW / PARTIALLY_FILLED) writes nothing.

- [ ] **Step 1: Write the failing tests**

```python
from card.orders import refresh_orders


def _ledger_with_placement(tmp_path: Path, order_id: int = 42) -> Path:
    ledger = tmp_path / "card-orders.jsonl"
    client = MagicMock()
    client.futures_create_order.return_value = {"orderId": order_id}
    adapter = BinanceFuturesAdapter(client, mode="live")
    place_orders(
        adapter, [_decision()], ledger_path=ledger, dual_side=False,
        marks={}, books={}, positions={}, equity=None, now_ms=NOW_MS,
    )
    return ledger


def test_refresh_writes_terminal_row_for_filled_order(tmp_path: Path) -> None:
    ledger = _ledger_with_placement(tmp_path)
    client = MagicMock()
    client.futures_get_order.return_value = {
        "status": "FILLED", "avgPrice": "99.9", "executedQty": "0.5",
        "updateTime": NOW_MS + 60_000,
    }
    rows = refresh_orders(client, ledger, marks={"BTCUSDT": 100.5}, now_ms=NOW_MS + 90_000)
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "terminal" and r["order_id"] == 42
    assert r["reason"] == "filled" and r["avg_price"] == 99.9
    assert r["terminal_at_ms"] == NOW_MS + 60_000
    assert r["mark_at_terminal"] == 100.5
    client.futures_get_order.assert_called_once_with(symbol="BTCUSDT", orderId=42)


def test_refresh_skips_working_and_already_terminal_orders(tmp_path: Path) -> None:
    ledger = _ledger_with_placement(tmp_path)
    client = MagicMock()
    client.futures_get_order.return_value = {"status": "NEW"}
    assert refresh_orders(client, ledger, marks={}, now_ms=NOW_MS) == []
    # now close it, then a second refresh must not re-poll it
    client.futures_get_order.return_value = {
        "status": "CANCELED", "avgPrice": "0", "executedQty": "0",
        "updateTime": NOW_MS + 1,
    }
    assert len(refresh_orders(client, ledger, marks={}, now_ms=NOW_MS)) == 1
    client.futures_get_order.reset_mock()
    assert refresh_orders(client, ledger, marks={}, now_ms=NOW_MS) == []
    client.futures_get_order.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_card_orders.py -q`
Expected: FAIL — `ImportError: cannot import name 'refresh_orders'`

- [ ] **Step 3: Implement in `card/orders.py`**

```python
_STATUS_REASON = {"FILLED": "filled", "CANCELED": "cancelled", "EXPIRED": "expired"}


def refresh_orders(
    client: Any,
    ledger_path: Path,
    *,
    marks: dict[str, float],
    now_ms: int,
) -> list[dict[str, Any]]:
    """Poll each open placement once; append a terminal row when it closed.

    Age-at-terminal and price-drift-at-cancel are derivable by joining the two
    row kinds on order_id - the whole of the max_order_age /
    hanging_orders_cancel_pct / order_refresh_tolerance claims (ST33).
    """
    rows = read_jsonl(ledger_path)
    terminal_ids = {r.get("order_id") for r in rows if r.get("kind") == "terminal"}
    written: list[dict[str, Any]] = []
    for p in rows:
        if p.get("kind") != "placement" or not p.get("order_id"):
            continue
        if p["order_id"] in terminal_ids:
            continue
        order = client.futures_get_order(symbol=p["symbol"], orderId=p["order_id"])
        reason = _STATUS_REASON.get(str(order.get("status")))
        if reason is None:
            continue  # NEW / PARTIALLY_FILLED: still working
        row: dict[str, Any] = {
            "kind": "terminal",
            "order_id": p["order_id"],
            "symbol": p["symbol"],
            "terminal_at_ms": int(order.get("updateTime") or now_ms),
            "status": str(order["status"]),
            "reason": reason,
            "avg_price": float(order.get("avgPrice") or 0.0),
            "executed_qty": float(order.get("executedQty") or 0.0),
            "mark_at_terminal": marks.get(str(p["symbol"])),
        }
        _append_line(ledger_path, row)
        written.append(row)
    return written
```

- [ ] **Step 4: Run to verify pass**

Run: `poetry run pytest tests/test_card_orders.py -q` — Expected: PASS
Then: `make lint-py && make typecheck` — Expected: clean

- [ ] **Step 5: Commit**

```bash
git add card/orders.py tests/test_card_orders.py
git commit -m "feat: poll placed card orders into terminal ledger rows"
```

---

### Task 7: CLI — `card-place` / `card-orders`, Make targets

**Files:**

- Create: `cli/card_orders.py`
- Modify: `cli/main.py` (mirror the existing `card.add_card_subparser(subparsers)` line)
- Modify: `Makefile` (two targets beside `buibui-card`; add both names to `.PHONY`)
- Test: `tests/test_card_orders.py` (picklist flow with injected `input_fn`)

**Interfaces:**

- Consumes: everything Tasks 2–6 produced; `utils.binance_client.create_client()`
  (mainnet client, the `tools/xsmom_execute.py` pattern);
  `card.config.CardConfig().cards_path`; `tomllib` for `config/universe.toml`.
- Produces: `add_card_orders_subparsers(subparsers) -> None` registering BOTH commands;
  `run_place(args) -> None`, `run_orders(args) -> None`;
  `pick_interactive(candidates, equity, *, input_fn, print_fn) -> list[CardCandidate]`.

- [ ] **Step 1: Write the failing tests for the interactive flow**

```python
from card.orders import pick_interactive


def test_pick_interactive_empty_input_selects_none() -> None:
    lines = iter([""])
    out: list[str] = []
    picked = pick_interactive(
        [_cand("BTCUSDT", "long")], equity=1000.0,
        input_fn=lambda _prompt: next(lines), print_fn=out.append,
    )
    assert picked == []
    assert any("ONE DRAW" in s for s in out)  # the hazard line is in the header


def test_pick_interactive_confirm_gate_and_aggregate_echo() -> None:
    lines = iter(["1 2", "y"])
    out: list[str] = []
    cands = [_cand("BTCUSDT", "short"), _cand("BTCUSDT", "short")]
    picked = pick_interactive(
        cands, equity=1000.0,
        input_fn=lambda _prompt: next(lines), print_fn=out.append,
    )
    assert len(picked) == 2
    assert any("BTCUSDT short" in s and "5.0" in s for s in out)  # stacked bet


def test_pick_interactive_n_aborts() -> None:
    lines = iter(["1", "n"])
    picked = pick_interactive(
        [_cand("BTCUSDT", "long")], equity=None,
        input_fn=lambda _prompt: next(lines), print_fn=lambda _s: None,
    )
    assert picked == []


def test_pick_interactive_reprompts_on_garbage() -> None:
    lines = iter(["banana", "1", "y"])
    picked = pick_interactive(
        [_cand("BTCUSDT", "long")], equity=None,
        input_fn=lambda _prompt: next(lines), print_fn=lambda _s: None,
    )
    assert len(picked) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_card_orders.py -q`
Expected: FAIL — `ImportError: cannot import name 'pick_interactive'`

- [ ] **Step 3: Implement `pick_interactive` in `card/orders.py`**

```python
from collections.abc import Callable
from datetime import UTC, datetime

_ONE_DRAW_LINE = (
    "A card verdict is ONE DRAW - identical inputs have returned opposite "
    "directions. Nothing is pre-selected; empty input places nothing."
)


def pick_interactive(
    candidates: list[CardCandidate],
    equity: float | None,
    *,
    input_fn: Callable[[str], str],
    print_fn: Callable[[str], None],
) -> list[CardCandidate]:
    """Numbered table -> selection -> aggregate echo -> y/N confirm."""
    print_fn(_ONE_DRAW_LINE)
    print_fn(f"{'#':>2}  {'symbol':<10} {'dir':<5} {'entry':>12} {'sl':>12} "
             f"{'qty':>10} {'risk_usd':>9}  expires")
    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    for i, c in enumerate(candidates, 1):
        mins = (c.valid_until_ms - now_ms) / 60_000
        risk = f"{c.risk_usd:.2f}" if c.risk_usd is not None else "?"
        print_fn(f"{i:>2}  {c.symbol:<10} {c.direction:<5} {c.entry:>12} "
                 f"{c.sl:>12} {c.qty:>10} {risk:>9}  {mins:.0f}m")
    while True:
        picks = parse_selection(input_fn("place which? (numbers, empty = none): "),
                                len(candidates))
        if picks is not None:
            break
        print_fn("unrecognised - numbers from the table, space or comma separated")
    if not picks:
        return []
    selected = [candidates[i] for i in picks]
    agg = aggregate_risk(selected, equity)
    frac = f" = {agg.total_risk_frac * 100:.2f}% of equity" if agg.total_risk_frac else ""
    print_fn(f"AGGREGATE: {len(selected)} orders, total risk ${agg.total_risk_usd:.2f}{frac}")
    for symbol, direction, risk_usd in agg.by_bet:
        print_fn(f"  {symbol} {direction}: ${risk_usd:.2f}")
    if input_fn("confirm placement? [y/N]: ").strip().lower() != "y":
        return []
    return selected
```

- [ ] **Step 4: Run to verify pass, then write the CLI**

Run: `poetry run pytest tests/test_card_orders.py -q` — Expected: PASS

Create `cli/card_orders.py`:

```python
"""CLI for the post-card GTX order helper: `card-place` and `card-orders`.

Sibling commands rather than `card place`: `buibui card` owns a positional
SYMBOL, so a nested subcommand would parse as a symbol. The existing card
invocation stays byte-identical.
"""

from __future__ import annotations

import argparse
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from card.config import CardConfig
from card.orders import (
    DEFAULT_ORDERS_PATH,
    XS_LIVE_MARKER,
    check_placement,
    pick_interactive,
    place_orders,
    read_jsonl,
    refresh_orders,
    scan_candidates,
)
from trade.binance_futures import BinanceFuturesAdapter


def _universe_symbols(path: Path = Path("config/universe.toml")) -> set[str]:
    with path.open("rb") as f:
        return set(tomllib.load(f).get("symbols", []))


def _now_ms() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def run_place(args: argparse.Namespace) -> None:
    from utils.binance_client import create_client

    now_ms = _now_ms()
    candidates = scan_candidates(
        read_jsonl(Path(args.cards_path)),
        read_jsonl(Path(args.ledger)),
        now_ms,
    )
    if not candidates:
        print("no unexpired unplaced TRADE cards")
        return
    client = create_client()
    adapter = BinanceFuturesAdapter(client, mode="dry_run" if args.dry_run else "live")
    try:
        equity: float | None = adapter.get_equity()
    except Exception:
        equity = None
        print("! equity unavailable - % figures suppressed")
    selected = pick_interactive(
        candidates, equity, input_fn=input, print_fn=print
    )
    if not selected:
        print("nothing placed")
        return
    symbols = sorted({c.symbol for c in selected})
    filters = adapter.get_filters(symbols)
    managed = _universe_symbols()
    dual_side = bool(client.futures_get_position_mode().get("dualSidePosition"))
    decisions = [
        check_placement(
            c, filters.get(c.symbol),
            managed=c.symbol in managed,
            xs_live_marker=XS_LIVE_MARKER.exists(),
            now_ms=_now_ms(),  # re-read: the operator may have thought a while
        )
        for c in selected
    ]
    for d in decisions:
        for v in d.vetoes:
            print(f"VETO {d.candidate.symbol}: {v}")
        for w in d.warnings:
            print(f"warn {d.candidate.symbol}: {w}")
    rows = place_orders(
        adapter, decisions,
        ledger_path=Path(args.ledger),
        dual_side=dual_side,
        marks=adapter.get_marks(symbols),
        books=adapter.get_book_tops(symbols),
        positions=adapter.get_positions(),
        equity=equity,
        now_ms=_now_ms(),
    )
    for row in rows:
        if row.get("terminal_reason") == "gtx_rejected":
            print(f"{row['symbol']}: GTX rejected - price already through the "
                  "level, the setup is stale (recorded, not an error)")
        else:
            print(f"{row['symbol']}: placed order {row['order_id']} "
                  f"@ {row['limit_price']} x {row['qty']}")
    if args.dry_run:
        print("dry run - nothing submitted, nothing recorded")


def run_orders(args: argparse.Namespace) -> None:
    ledger = Path(args.ledger)
    if args.refresh:
        from utils.binance_client import create_client

        client = create_client()
        rows = read_jsonl(ledger)
        symbols = sorted({
            str(r["symbol"]) for r in rows if r.get("kind") == "placement"
        })
        adapter = BinanceFuturesAdapter(client, mode="dry_run")
        written = refresh_orders(
            client, ledger, marks=adapter.get_marks(symbols), now_ms=_now_ms()
        )
        print(f"{len(written)} order(s) reached a terminal state")
    rows = read_jsonl(ledger)
    terminal = {r["order_id"]: r for r in rows if r.get("kind") == "terminal"}
    for p in rows:
        if p.get("kind") != "placement":
            continue
        t = terminal.get(p.get("order_id"))
        state = (
            p.get("terminal_reason")
            or (t["reason"] if t else "working")
        )
        print(f"{p['symbol']:<10} {p.get('direction','?'):<5} "
              f"{p['limit_price']:>12} x {p['qty']:<8} {state}")


def add_card_orders_subparsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    place = subparsers.add_parser(
        "card-place",
        help="interactive GTX picklist over unexpired TRADE cards",
    )
    place.add_argument("--cards-path", default=CardConfig().cards_path)
    place.add_argument("--ledger", default=DEFAULT_ORDERS_PATH)
    place.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="walk the whole flow, submit nothing, record nothing",
    )
    place.set_defaults(func=run_place)

    orders = subparsers.add_parser(
        "card-orders",
        help="list card order placements; --refresh polls terminal states",
    )
    orders.add_argument("--ledger", default=DEFAULT_ORDERS_PATH)
    orders.add_argument("--refresh", action="store_true")
    orders.set_defaults(func=run_orders)
```

In `cli/main.py`, beside the existing `card` import and registration:

```python
from cli import card_orders
...
    card_orders.add_card_orders_subparsers(subparsers)
```

In the `Makefile`, beside `buibui-card` (recipe lines below are shown with spaces —
use REAL TABS in the Makefile — and add both target names to the `.PHONY`
list at the top):

```make
.PHONY: buibui-card-place
buibui-card-place:  ## interactive GTX picklist over unexpired TRADE cards (DRY=1 previews)
    @poetry run python buibui.py card-place $(if $(DRY),--dry-run,)

.PHONY: buibui-card-orders
buibui-card-orders:  ## list card order placements (REFRESH=1 polls terminal states)
    @poetry run python buibui.py card-orders $(if $(REFRESH),--refresh,)
```

- [ ] **Step 5: Smoke the CLI wiring (no network needed for --help)**

Run: `poetry run python buibui.py card-place --help && poetry run python buibui.py card-orders --help && poetry run python buibui.py card --help >/dev/null && echo OK`
Expected: both help texts print, existing `card` still parses, `OK`

- [ ] **Step 6: Run everything touched**

Run: `poetry run pytest tests/test_card_orders.py tests/trade/ -q && make lint-py && make typecheck`
Expected: PASS / clean

- [ ] **Step 7: Commit**

```bash
git add card/orders.py cli/card_orders.py cli/main.py Makefile tests/test_card_orders.py
git commit -m "feat: card-place and card-orders CLI with interactive GTX picklist"
```

---

### Task 8: docs flip — the advisory invariant changes deliberately

**Files:**

- Modify: `.claude/skills/card/SKILL.md` — the common-mistakes entry "treating a TRADE
  card as an order" becomes the pointer: `card-place` is the ONE deliberate exception,
  used only through its picklist; add a post-batch step offering
  `make buibui-card-place`.
- Modify: `AGENTS.md` `### buibui card SYMBOL` section — add one bullet naming
  `card-place` / `card-orders`, the entry-only scope, the XS-collision guard, and the
  one-draw hazard; keep it to ~5 lines (the spec carries the detail).
- Modify: `cli/card.py:243` — the `card` parser help string
  `"AI trade card for one symbol (advisory; routes no orders)"` becomes
  `"AI trade card for one symbol (advisory; card-place places picked TRADE cards)"`.
- Modify: `README.md` — add the two commands to the CLI list.

**Interfaces:** none — prose only, but it MUST land in this same branch: the SoT row's
own condition for changing the invariant is that skill + docs move with the code.

- [ ] **Step 1: Make the four edits above**

- [ ] **Step 2: Verify**

Run: `make lint-md && make sanity-checks`
Expected: lint clean; sanity sweep green (read the banner, not the exit code)

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/card/SKILL.md AGENTS.md cli/card.py README.md
git commit -m "docs: card-place is the one deliberate exception to advisory-only cards"
```

---

### Task 9: final verification

- [ ] **Step 1: Targeted suite over everything the branch touched**

Run: `poetry run pytest tests/test_card_orders.py tests/trade/ tests/test_card_card.py tests/test_card_ledger.py -q`
Expected: ALL PASS

- [ ] **Step 2: Foreground gates**

Run: `make lint-py && make typecheck && make lint-md`
Expected: clean

- [ ] **Step 3: Hand off to `/post-branch`**

Invoke `/post-branch`. Its Step 7 runs `make preflight` (the ONE full-suite run for the
branch — do not run `make test` separately) and the pre-flip gates. Regression branch to
state in the PR body: diff touches `card/`, `cli/`, `trade/`, `Makefile`, docs — all
outside the regression paths filter, so `make test-regression` is not required.
