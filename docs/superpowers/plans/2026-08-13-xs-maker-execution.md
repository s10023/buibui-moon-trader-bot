# XS Maker Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send the XS sleeve's risk-increasing orders as post-only LIMIT orders resting at
the touch, keeping risk-reducing orders as MARKET.

**Architecture:** The maker/taker choice is made in the pure planner (`trade/routing.py`)
from the `reduce_only` flag it already computes, and travels on `OrderIntent`. The exchange
adapter (`trade/binance_futures.py`) gains the reads that a limit price needs — best
bid/ask, and the `PRICE_FILTER` tick that no code in this repo has ever needed — plus a
cancel path. Unfilled orders are left to self-correct on the next daily rebalance, which
makes cancelling stale orders before each run a correctness requirement, not an
optimisation.

**Tech Stack:** Python 3.11+, Poetry, pytest + unittest.mock, mypy strict, ruff.
`python-binance` client, always injected and mocked in tests.

**Spec:** `docs/superpowers/specs/2026-08-13-xs-maker-execution-design.md` — read it first;
this plan argues from it, and its decision log records why each choice was made and what
observable would reverse it.

## Global Constraints

- **Python 3.11+**, all functions annotated including return types (`-> None` on tests);
  mypy runs strict (`disallow_untyped_defs = true`).
- **Tests never make real network calls.** The adapter takes an injected client; tests pass
  a `MagicMock` directly. Follow the existing style in `tests/trade/test_binance_futures.py`.
- **Post-only is `timeInForce="GTX"`** on Binance USDT-M Futures — the exchange rejects the
  order rather than crossing.
- **BUY prices round DOWN to the tick; SELL prices round UP.** Rounding the wrong way
  crosses the spread and GTX rejects the order outright.
- **New dataclass fields must be defaulted** — `ExchangeFilters` and `OrderIntent` are
  constructed positionally in existing tests, and an undefaulted field breaks them.
- **After every Python change:** `make lint-py` ✓, `make typecheck` ✓, `make test` green.
  State each result plainly.
- **`make test-regression` is NOT required for this plan** — the diff touches `trade/`,
  `portfolio/sizing.py` and `tests/`, none of which are in the regression trigger set
  (`analytics/backtest/`, `analytics/strategies/`, `analytics/signal_config.py`,
  `config/*signal_watch*.toml`, `config/strategy_params.toml`, `tests/fixtures/`,
  `poetry.lock`). Say which branch you took.
- **Do not change `analytics/xsmom/execution.py`.** `fee_pct = 0.0005` stays at the taker
  rate deliberately (spec §5, decision 5).
- Branch `feat/xs-maker-execution` already exists and carries the spec. Commit to it; do
  not open a PR until every task is done (one plan = one PR).

---

### Task 1: Side-aware tick rounding

The price analogue of `round_down_to_step`. It lives beside it so the float-fragility
knowledge stays in one file.

**Files:**

- Modify: `portfolio/sizing.py` (add after `round_down_to_step`, which ends at line 195)
- Test: `tests/test_portfolio_sizing.py`

**Interfaces:**

- Consumes: `_STEP_SNAP_REL_TOL` (1e-9) and `_STEP_SNAP_MAX_TOL` (1e-6), already defined at
  `portfolio/sizing.py:157` and `:165`; `math`, already imported.
- Produces: `round_to_tick(price: float, tick: float, side: str) -> float` — used by Task 7.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_portfolio_sizing.py`:

```python
def test_round_to_tick_enumerated_never_crosses() -> None:
    """BUY must never round up, SELL must never round down — at any tick."""
    for tick in (0.0001, 0.01, 0.1, 1.0, 2.5):
        for mult in range(1, 400):
            exact = mult * tick
            for offset in (0.0, tick * 0.3, tick * 0.7, tick * 0.999):
                price = exact + offset
                buy = round_to_tick(price, tick, "BUY")
                sell = round_to_tick(price, tick, "SELL")
                assert buy <= price + 1e-12, f"BUY crossed: {price} {tick} -> {buy}"
                assert sell >= price - 1e-12, f"SELL crossed: {price} {tick} -> {sell}"


def test_round_to_tick_exact_multiple_is_returned_unchanged() -> None:
    """The case that matters most: an exchange bid/ask is ALREADY a tick multiple.

    A naive floor(price / tick) * tick loses a full tick here — 0.29 / 0.01
    computes as 28.999999999999996. That is the same defect round_down_to_step
    documents, and at the touch it would push the order a tick away from the
    queue position we asked for.
    """
    assert round_to_tick(0.29, 0.01, "BUY") == pytest.approx(0.29)
    assert round_to_tick(0.29, 0.01, "SELL") == pytest.approx(0.29)
    assert round_to_tick(3000.0, 0.01, "BUY") == pytest.approx(3000.0)
    assert round_to_tick(117.3, 0.1, "SELL") == pytest.approx(117.3)


def test_round_to_tick_non_positive_tick_passes_through() -> None:
    assert round_to_tick(123.456, 0.0, "BUY") == 123.456
    assert round_to_tick(123.456, -1.0, "SELL") == 123.456
```

Add `round_to_tick` to the existing `from portfolio.sizing import (...)` line at the top of
the file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_portfolio_sizing.py -k round_to_tick -q`
Expected: FAIL — `ImportError: cannot import name 'round_to_tick'`

- [ ] **Step 3: Implement**

Add to `portfolio/sizing.py` after `round_down_to_step`:

```python
def round_to_tick(price: float, tick: float, side: str) -> float:
    """Round a limit price to the symbol's PRICE_FILTER tick, passively.

    Side-dependent by necessity: a BUY resting above the tick it asked for, or a
    SELL below it, CROSSES the spread — and a post-only (GTX) order that would
    cross is rejected outright by the exchange, so the leg silently does not
    trade. BUY therefore floors, SELL ceils.

    Shares `round_down_to_step`'s snap-before-round for the same reason: the
    exchange's own bid/ask is already an exact tick multiple, and a plain
    `floor(price / tick)` loses a full tick on exactly that input
    (`0.29 / 0.01` -> `28.999999999999996`). A non-positive tick means "unknown
    filter" and passes through unchanged.
    """
    if tick <= 0:
        return price
    quotient = price / tick
    nearest = round(quotient)
    tolerance = min(_STEP_SNAP_REL_TOL * max(1.0, quotient), _STEP_SNAP_MAX_TOL)
    if abs(quotient - nearest) <= tolerance:
        return nearest * tick
    if side == "BUY":
        return math.floor(quotient) * tick
    return math.ceil(quotient) * tick
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_portfolio_sizing.py -k round_to_tick -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Gates and commit**

```bash
make lint-py && make typecheck && make test
git add portfolio/sizing.py tests/test_portfolio_sizing.py
git commit -m "feat(sizing): add side-aware round_to_tick for post-only limit prices"
```

---

### Task 2: Read PRICE_FILTER into ExchangeFilters

**Files:**

- Modify: `trade/routing.py:19-24` (the `ExchangeFilters` dataclass)
- Modify: `trade/binance_futures.py:47-67` (`get_filters`)
- Test: `tests/trade/test_binance_futures.py`

**Interfaces:**

- Produces: `ExchangeFilters.price_tick: float` (defaults to `0.0`), populated by
  `get_filters`. Consumed by Task 7.

- [ ] **Step 1: Write the failing test**

Add to `tests/trade/test_binance_futures.py`:

```python
def test_get_filters_extracts_price_tick() -> None:
    client = MagicMock()
    client.futures_exchange_info.return_value = {
        "symbols": [
            {
                "symbol": "AAAUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                ],
            },
        ]
    }
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_filters(["AAAUSDT"])["AAAUSDT"].price_tick == 0.01


def test_get_filters_missing_price_filter_leaves_tick_zero() -> None:
    """A zero tick means 'unknown filter' and round_to_tick passes through."""
    client = MagicMock()
    client.futures_exchange_info.return_value = {
        "symbols": [
            {
                "symbol": "AAAUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                ],
            },
        ]
    }
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_filters(["AAAUSDT"])["AAAUSDT"].price_tick == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/trade/test_binance_futures.py -k price_tick -q`
Expected: FAIL — `TypeError: ExchangeFilters.__init__() got an unexpected keyword argument`
or `AttributeError: 'ExchangeFilters' object has no attribute 'price_tick'`

- [ ] **Step 3: Implement**

In `trade/routing.py`, add the field last so existing positional constructions keep working:

```python
@dataclass(frozen=True)
class ExchangeFilters:
    symbol: str
    qty_step: float
    min_qty: float
    min_notional: float
    price_tick: float = 0.0  # PRICE_FILTER tickSize; 0.0 == unknown
```

In `trade/binance_futures.py::get_filters`, extend the filter loop and the construction:

```python
            step = min_qty = min_notional = price_tick = 0.0
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                    min_qty = float(f["minQty"])
                elif f["filterType"] == "MIN_NOTIONAL":
                    min_notional = float(f["notional"])
                elif f["filterType"] == "PRICE_FILTER":
                    price_tick = float(f["tickSize"])
            out[s["symbol"]] = ExchangeFilters(
                symbol=s["symbol"],
                qty_step=step,
                min_qty=min_qty,
                min_notional=min_notional,
                price_tick=price_tick,
            )
```

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/trade/ -q`
Expected: PASS — the whole trade suite, confirming the defaulted field broke no existing
positional construction.

- [ ] **Step 5: Gates and commit**

```bash
make lint-py && make typecheck && make test
git add trade/routing.py trade/binance_futures.py tests/trade/test_binance_futures.py
git commit -m "feat(trade): read PRICE_FILTER tickSize into ExchangeFilters"
```

---

### Task 3: Order type on the plan

The maker/taker policy, decided in the pure planner so it is testable with no client.

**Files:**

- Modify: `trade/routing.py:27-34` (`OrderIntent`), `:114-151` (`build_order_plan` body)
- Test: `tests/trade/test_routing.py`

**Interfaces:**

- Consumes: `reduce_only`, already computed at `routing.py:114`.
- Produces: `OrderIntent.order_type: str` — `"LIMIT"` or `"MARKET"`, defaults to
  `"MARKET"`. Consumed by Tasks 5 and 7.

- [ ] **Step 1: Write the failing tests**

Add to `tests/trade/test_routing.py`:

First extend the file's existing `_filters` helper (`tests/trade/test_routing.py:7-13`) with
a tick, so these tests do not introduce a second construction style:

```python
def _filters(
    sym: str,
    step: float = 0.001,
    min_qty: float = 0.001,
    min_notional: float = 5.0,
    price_tick: float = 0.01,
) -> ExchangeFilters:
    return ExchangeFilters(
        symbol=sym,
        qty_step=step,
        min_qty=min_qty,
        min_notional=min_notional,
        price_tick=price_tick,
    )
```

Then the tests, using the file's `_book` / `_pos` helpers (`:16-36`) — note `_pos` takes
**leverage**, and notional is `lev * capital`:

```python
def test_opens_are_limit_and_closes_are_market() -> None:
    """Risk-increasing orders make; risk-reducing orders take.

    An unfilled OPEN is bounded opportunity cost and self-corrects on the next
    rebalance. An unfilled CLOSE is open directional risk the book has already
    decided against — that asymmetry is the whole reason for the split.
    """
    book = _book([_pos("AAAUSDT", 0.1)])  # $1000 target, a new long
    plan = build_order_plan(
        book,
        current_positions={"BBBUSDT": 2.0},  # held, not in book -> close
        marks={"AAAUSDT": 100.0, "BBBUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT"), "BBBUSDT": _filters("BBBUSDT")},
        no_trade_band_frac=0.0,
        capital=10_000.0,
    )
    by_symbol = {i.symbol: i for i in plan.intents}
    assert by_symbol["AAAUSDT"].order_type == "LIMIT"
    assert by_symbol["AAAUSDT"].reduce_only is False
    assert by_symbol["BBBUSDT"].order_type == "MARKET"
    assert by_symbol["BBBUSDT"].reduce_only is True


def test_same_side_trim_is_market() -> None:
    """A trim reduces an existing position, so it is risk-reducing too."""
    book = _book([_pos("AAAUSDT", 0.05)])  # $500 target vs $1000 held
    plan = build_order_plan(
        book,
        current_positions={"AAAUSDT": 10.0},  # 10 units at mark 100 = $1000
        marks={"AAAUSDT": 100.0},
        filters={"AAAUSDT": _filters("AAAUSDT")},
        no_trade_band_frac=0.0,
        capital=10_000.0,
    )
    intent = plan.intents[0]
    assert intent.side == "SELL"
    assert intent.reduce_only is True
    assert intent.order_type == "MARKET"
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/trade/test_routing.py -k order_type -q`
Expected: FAIL — `AttributeError: 'OrderIntent' object has no attribute 'order_type'`

- [ ] **Step 3: Implement**

In `trade/routing.py`:

```python
@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str  # "BUY" | "SELL"
    qty: float
    reduce_only: bool
    delta_notional: float
    reason: str  # "open" | "rebalance" | "close" | "skip:<why>"
    order_type: str = "MARKET"  # "LIMIT" (post-only) | "MARKET"
```

In `build_order_plan`, immediately after `reduce_only` is computed at line 114:

```python
        # Risk-reducing orders TAKE, risk-increasing orders MAKE. An unfilled
        # open is bounded opportunity cost that the next rebalance re-plans; an
        # unfilled close leaves directional risk the book has already rejected.
        order_type = "MARKET" if reduce_only else "LIMIT"
```

and pass it on the appended intent only (skipped intents keep the default):

```python
intents.append(
    OrderIntent(sym, side, order_qty, reduce_only, delta_notional, reason, order_type)
)
```

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/trade/test_routing.py -q`
Expected: PASS — all tests in the file, old and new.

- [ ] **Step 5: Gates and commit**

```bash
make lint-py && make typecheck && make test
git add trade/routing.py tests/trade/test_routing.py
git commit -m "feat(routing): choose LIMIT vs MARKET from the reduce_only flag"
```

---

### Task 4: Read the book top

**Files:**

- Modify: `trade/binance_futures.py` (add after `get_marks`, which ends at line 74)
- Test: `tests/trade/test_binance_futures.py`

**Interfaces:**

- Produces: `get_book_tops(symbols: list[str]) -> dict[str, tuple[float, float]]` mapping
  symbol to `(bid, ask)`. Consumed by Task 7.

- [ ] **Step 1: Write the failing tests**

```python
def test_get_book_tops_returns_bid_ask_for_wanted_symbols() -> None:
    client = MagicMock()
    client.futures_orderbook_ticker.return_value = [
        {"symbol": "AAAUSDT", "bidPrice": "99.98", "askPrice": "100.02"},
        {"symbol": "BBBUSDT", "bidPrice": "10.1", "askPrice": "10.2"},
        {"symbol": "ZZZUSDT", "bidPrice": "1.0", "askPrice": "1.1"},
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    tops = adapter.get_book_tops(["AAAUSDT", "BBBUSDT"])
    assert tops == {"AAAUSDT": (99.98, 100.02), "BBBUSDT": (10.1, 10.2)}


def test_get_book_tops_drops_non_positive_quotes() -> None:
    """A zero or missing side is unusable — the caller must skip that symbol."""
    client = MagicMock()
    client.futures_orderbook_ticker.return_value = [
        {"symbol": "AAAUSDT", "bidPrice": "0", "askPrice": "100.02"},
        {"symbol": "BBBUSDT", "bidPrice": "10.1", "askPrice": "10.2"},
    ]
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_book_tops(["AAAUSDT", "BBBUSDT"]) == {"BBBUSDT": (10.1, 10.2)}
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/trade/test_binance_futures.py -k book_tops -q`
Expected: FAIL — `AttributeError: 'BinanceFuturesAdapter' object has no attribute 'get_book_tops'`

- [ ] **Step 3: Implement**

```python
    def get_book_tops(self, symbols: list[str]) -> dict[str, tuple[float, float]]:
        """Best bid/ask per symbol, for post-only limit placement.

        Mirrors `get_marks`: one batch call for the whole universe. A
        non-positive quote on either side is dropped rather than returned as
        zero — a limit priced off a zero would be rejected or, worse, filled
        somewhere absurd.
        """
        rows = self.client.futures_orderbook_ticker()
        wanted = set(symbols)
        out: dict[str, tuple[float, float]] = {}
        for r in rows:
            if r["symbol"] not in wanted:
                continue
            bid = float(r["bidPrice"])
            ask = float(r["askPrice"])
            if bid > 0.0 and ask > 0.0:
                out[r["symbol"]] = (bid, ask)
        return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/trade/test_binance_futures.py -q`
Expected: PASS

- [ ] **Step 5: Gates and commit**

```bash
make lint-py && make typecheck && make test
git add trade/binance_futures.py tests/trade/test_binance_futures.py
git commit -m "feat(trade): add get_book_tops for maker limit placement"
```

---

### Task 5: Submit LIMIT (post-only) or MARKET

**Files:**

- Modify: `trade/binance_futures.py:92-107` (`submit_market`)
- Test: `tests/trade/test_binance_futures.py`

**Interfaces:**

- Consumes: `OrderIntent.order_type` (Task 3).
- Produces: `submit(intent: OrderIntent, price: float | None = None) -> dict[str, Any]`.
  **Replaces `submit_market`**; Task 7 updates the caller and the Protocol.

- [ ] **Step 1: Write the failing tests**

```python
def test_submit_limit_sends_gtx_post_only() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent("AAAUSDT", "BUY", 2.0, False, 200.0, "open", "LIMIT")
    adapter.submit(intent, price=99.98)
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["type"] == "LIMIT"
    assert kwargs["timeInForce"] == "GTX"  # post-only: reject rather than cross
    assert kwargs["price"] == 99.98
    assert kwargs["quantity"] == 2.0


def test_submit_market_omits_price_and_tif() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent("AAAUSDT", "SELL", 2.0, True, -200.0, "close", "MARKET")
    adapter.submit(intent)
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["type"] == "MARKET"
    assert "price" not in kwargs and "timeInForce" not in kwargs
    assert kwargs["reduceOnly"] is True


def test_submit_limit_without_price_raises() -> None:
    """A LIMIT with no price is a caller bug — fail loudly, never silently market."""
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="live")
    intent = OrderIntent("AAAUSDT", "BUY", 2.0, False, 200.0, "open", "LIMIT")
    with pytest.raises(ValueError, match="LIMIT order requires a price"):
        adapter.submit(intent, price=None)
    client.futures_create_order.assert_not_called()


def test_submit_dry_run_reports_type_and_price_without_calling() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    intent = OrderIntent("AAAUSDT", "BUY", 2.0, False, 200.0, "open", "LIMIT")
    out = adapter.submit(intent, price=99.98)
    assert out["dryRun"] is True
    assert out["orderType"] == "LIMIT" and out["price"] == 99.98
    client.futures_create_order.assert_not_called()
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/trade/test_binance_futures.py -k submit -q`
Expected: FAIL — `AttributeError: ... has no attribute 'submit'`

- [ ] **Step 3: Implement**

Replace `submit_market` entirely:

```python
    def submit(self, intent: OrderIntent, price: float | None = None) -> dict[str, Any]:
        """Submit one order. LIMIT orders are post-only (GTX).

        GTX makes the exchange REJECT an order that would cross instead of
        letting it take. That is the point — a crossed "maker" order is just a
        taker fill with extra steps — but it means a wrong-side tick rounding
        fails as a rejection, not a bad fill. See `round_to_tick`.
        """
        if intent.order_type == "LIMIT" and price is None:
            raise ValueError(f"LIMIT order requires a price: {intent.symbol}")
        if self.mode == "dry_run":
            return {
                "dryRun": True,
                "symbol": intent.symbol,
                "side": intent.side,
                "qty": intent.qty,
                "reduceOnly": intent.reduce_only,
                "orderType": intent.order_type,
                "price": price,
            }
        params: dict[str, Any] = {
            "symbol": intent.symbol,
            "side": intent.side,
            "type": intent.order_type,
            "quantity": intent.qty,
            "reduceOnly": intent.reduce_only,
        }
        if intent.order_type == "LIMIT":
            params["price"] = price
            params["timeInForce"] = "GTX"
        return self.client.futures_create_order(**params)  # type: ignore[no-any-return]
```

Update the module docstring at `trade/binance_futures.py:4` — it names `submit_market`.

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/trade/test_binance_futures.py -q`
Expected: PASS. `tests/trade/test_xsmom_executor.py` will now FAIL — it stubs
`submit_market`. That is expected and Task 7 fixes it; do not patch it here.

- [ ] **Step 5: Commit (gates deferred)**

`make test` is red until Task 7 lands, so commit without it and say so:

```bash
make lint-py && make typecheck
git add trade/binance_futures.py tests/trade/test_binance_futures.py
git commit -m "feat(trade): submit() sends post-only GTX limits or market orders"
```

---

### Task 6: Cancel stale resting orders

Fire-and-forget's precondition. Without it, yesterday's unfilled order rests on the book
while today's plan submits another — and the position overshoots silently, because both
orders are individually correct and neither appears in `get_positions()`.

**Files:**

- Modify: `trade/binance_futures.py` (add after `get_book_tops`)
- Test: `tests/trade/test_binance_futures.py`

**Interfaces:**

- Produces: `get_open_order_symbols() -> set[str]` and
  `cancel_open_orders(symbol: str) -> None`. Both consumed by Task 7.

- [ ] **Step 1: Write the failing tests**

```python
def test_get_open_order_symbols_dedups() -> None:
    client = MagicMock()
    client.futures_get_open_orders.return_value = [
        {"symbol": "AAAUSDT", "orderId": 1},
        {"symbol": "AAAUSDT", "orderId": 2},
        {"symbol": "BBBUSDT", "orderId": 3},
    ]
    adapter = BinanceFuturesAdapter(client, mode="live")
    assert adapter.get_open_order_symbols() == {"AAAUSDT", "BBBUSDT"}


def test_get_open_order_symbols_dry_run_returns_empty_without_calling() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    assert adapter.get_open_order_symbols() == set()
    client.futures_get_open_orders.assert_not_called()


def test_cancel_open_orders_calls_per_symbol() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="live")
    adapter.cancel_open_orders("AAAUSDT")
    client.futures_cancel_all_open_orders.assert_called_once_with(symbol="AAAUSDT")


def test_cancel_open_orders_dry_run_is_a_noop() -> None:
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="dry_run")
    adapter.cancel_open_orders("AAAUSDT")
    client.futures_cancel_all_open_orders.assert_not_called()
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/trade/test_binance_futures.py -k "open_order or cancel" -q`
Expected: FAIL — attributes do not exist.

- [ ] **Step 3: Implement**

```python
def get_open_order_symbols(self) -> set[str]:
    """Symbols carrying a resting order right now.

    Read with no symbol argument so it covers symbols that have since left
    the target book — the case with no position to reveal it.
    """
    if self.mode == "dry_run":
        return set()
    rows = self.client.futures_get_open_orders()
    return {r["symbol"] for r in rows}


def cancel_open_orders(self, symbol: str) -> None:
    if self.mode == "dry_run":
        return
    self.client.futures_cancel_all_open_orders(symbol=symbol)
```

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/trade/test_binance_futures.py -q`
Expected: PASS

- [ ] **Step 5: Commit (gates still deferred to Task 7)**

```bash
make lint-py && make typecheck
git add trade/binance_futures.py tests/trade/test_binance_futures.py
git commit -m "feat(trade): add open-order discovery and per-symbol cancel"
```

---

### Task 7: Wire the executor

**Files:**

- Modify: `trade/xsmom_executor.py:28-35` (the `_ExecAdapter` Protocol), `:97` (position
  read), `:134-141` (the submit loop)
- Test: `tests/trade/test_xsmom_executor.py`

**Interfaces:**

- Consumes: `round_to_tick` (Task 1), `ExchangeFilters.price_tick` (Task 2),
  `OrderIntent.order_type` (Task 3), `get_book_tops` (Task 4), `submit` (Task 5),
  `get_open_order_symbols` / `cancel_open_orders` (Task 6).
- Produces: no new public surface.

First extend the file's hand-written `_FakeAdapter` (`tests/trade/test_xsmom_executor.py:44`
— this file does NOT use MagicMock). Add to `__init__`:

```python
self.open_order_symbols: set[str] = set()
self.cancelled: list[str] = []
self.book_tops: dict[str, tuple[float, float]] = {}
self.submitted_prices: list[float | None] = []
self.calls: list[str] = []  # ordering probe
self.cancel_raises = False
```

and the new methods, replacing `submit_market` (`:77-81`) with `submit`:

```python
def get_open_order_symbols(self) -> set[str]:
    return set(self.open_order_symbols)


def cancel_open_orders(self, symbol: str) -> None:
    if self.cancel_raises:
        raise RuntimeError("cancel failed")
    self.calls.append(f"cancel:{symbol}")
    self.cancelled.append(symbol)


def get_book_tops(self, symbols: list[str]) -> dict[str, tuple[float, float]]:
    return {s: self.book_tops[s] for s in symbols if s in self.book_tops}


def submit(self, intent: OrderIntent, price: float | None = None) -> dict[str, object]:
    if self.fail_symbol in (intent.symbol, "*"):  # "*" fails every order
        raise RuntimeError("rejected")
    self.submitted.append(intent)
    self.submitted_prices.append(price)
    return {"ok": True}
```

and record ordering in the existing `get_positions` (`:63`):

```python
    def get_positions(self) -> dict[str, float]:
        self.calls.append("positions")
        return dict(self._positions)
```

`_FakeAdapter.get_filters` (`:69-72`) builds `ExchangeFilters(s, 0.001, 0.001, 5.0)` — add
a tick so limit pricing is exercised: `ExchangeFilters(s, 0.001, 0.001, 5.0, 0.01)`.

- [ ] **Step 1: Write the failing tests**

⚠ `tests/trade/test_xsmom_executor.py` does **not** import pytest — add `import pytest` to
its imports (after `import pandas as pd` at `:8`), or `pytest.raises` below is a NameError.

Follow the call shape of `test_run_once_happy_path_submits` (`:109-130`) exactly — same
`_seed(conn)`, same `now=pd.Timestamp("2022-02-05", tz="UTC")`.

```python
def test_cancels_stale_orders_before_reading_positions(tmp_path: Path) -> None:
    """A partially-filled resting order is still moving the position while it
    sits there, so the planning read must come AFTER the cancel."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=10_000.0, positions={}, marks={})
    adapter.open_order_symbols = {"AAAUSDT"}
    adapter.book_tops = {s: (99.98, 100.02) for s in syms}
    run_once(
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
    assert adapter.calls.index("cancel:AAAUSDT") < adapter.calls.index("positions")


def test_cancel_is_scoped_to_managed_symbols(tmp_path: Path) -> None:
    """ZZZUSDT is neither in the book nor held, so an operator's own order on it
    must survive. The router already closes non-book POSITIONS — which is why a
    dedicated sub-account is required — but do not widen that to orders."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=10_000.0, positions={}, marks={})
    adapter.open_order_symbols = {"AAAUSDT", "ZZZUSDT"}
    adapter.book_tops = {s: (99.98, 100.02) for s in syms}
    run_once(
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
    assert adapter.cancelled == ["AAAUSDT"]


def test_limit_rests_at_the_touch_rounded_passively(tmp_path: Path) -> None:
    """Quotes are deliberately NOT tick-exact, so the floor/ceil branch runs.

    `_FakeAdapter.get_filters` uses tick 0.01. A tick-exact quote like 99.98
    would hit round_to_tick's snap branch BEFORE the side is read, so the test
    would pass even if the BUY/SELL direction were inverted — the end-to-end
    version of the vacuous assertion that failed task 1's first review.
    """
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=10_000.0, positions={}, marks={})
    adapter.book_tops = {s: (99.9847, 100.0231) for s in syms}
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
    assert res.submitted, "expected at least one order"
    seen_limit = False
    for intent, price in zip(adapter.submitted, adapter.submitted_prices, strict=True):
        if intent.order_type != "LIMIT":
            continue
        seen_limit = True
        # BUY floors 99.9847 -> 99.98; SELL ceils 100.0231 -> 100.03.
        assert price == pytest.approx(99.98 if intent.side == "BUY" else 100.03)
    assert seen_limit, "no LIMIT order submitted — the assertion above never ran"


def test_limit_leg_fails_cleanly_when_book_top_missing(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=10_000.0, positions={}, marks={})
    adapter.book_tops = {}  # no quotes at all
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
    assert res.submitted == []
    assert res.failed, "a missing book top must be recorded, never silently skipped"
    assert all("no_book_top" in reason for _, reason in res.failed)


def test_cancel_failure_aborts_before_submitting(tmp_path: Path) -> None:
    """Planning against un-cancelled orders is the overshoot this exists to stop,
    so a failed cancel must abort the run rather than degrade to planning."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=10_000.0, positions={}, marks={})
    adapter.open_order_symbols = {"AAAUSDT"}
    adapter.cancel_raises = True
    with pytest.raises(RuntimeError, match="cancel failed"):
        run_once(
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
    assert adapter.submitted == []


def test_fake_adapter_does_not_drift_from_the_real_one() -> None:
    """Pin `_FakeAdapter`'s surface to the `_Adapter` Protocol.

    Added after the `submit_market` -> `submit` rename: it broke the live path
    in `tools/xsmom_execute.py` and NOTHING at runtime noticed. Every executor
    test drives `_FakeAdapter`, which is duck-typed, so mypy was the only
    signal that the real adapter no longer satisfied the Protocol. A fake that
    can silently diverge from the thing it stands in for makes every test
    using it weaker than it looks.
    """
    from trade.binance_futures import BinanceFuturesAdapter
    from trade.xsmom_executor import _Adapter

    required = {
        name
        for name, value in vars(_Adapter).items()
        if callable(value) and not name.startswith("_")
    }
    assert required, "derived an empty Protocol surface — the check would be vacuous"
    for name in sorted(required):
        assert hasattr(_FakeAdapter, name), f"_FakeAdapter is missing {name}"
        assert hasattr(BinanceFuturesAdapter, name), f"real adapter is missing {name}"
```

⚠ Note the `assert required` line: without it, a change to how the Protocol stores its
members would silently yield an empty set and the loop would pass while checking nothing.
That is the same vacuous-check failure this branch has already hit three times.

⚠ The existing `test_run_once_isolates_per_order_failure` (`:177`) drives failure through
`fail_symbol`, which the new `submit` preserves — do not change that test.

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/trade/test_xsmom_executor.py -q`
Expected: FAIL — both the new tests and the pre-existing `submit_market` stubs.

- [ ] **Step 3: Implement**

Update the Protocol at `trade/xsmom_executor.py:28-35`:

```python
    def get_book_tops(self, symbols: list[str]) -> dict[str, tuple[float, float]]: ...
    def get_open_order_symbols(self) -> set[str]: ...
    def cancel_open_orders(self, symbol: str) -> None: ...
    def submit(self, intent: OrderIntent, price: float | None = None) -> dict[str, Any]: ...
```

(delete the `submit_market` line).

Insert before the position read at line 97 — note the throwaway read that breaks the
ordering cycle (spec decision 6):

```python
    # Cancel stale resting orders BEFORE the planning read. A resting order is
    # not a position, so yesterday's unfilled order would otherwise sit on the
    # book while today's plan submits another one — an overshoot in which both
    # orders are individually correct. Scoped to symbols this executor manages:
    # the operator's own orders on other symbols must survive.
    open_syms = adapter.get_open_order_symbols()
    if open_syms:
        managed = set(symbols) | set(adapter.get_positions())
        for sym in sorted(open_syms & managed):
            adapter.cancel_open_orders(sym)

    positions = adapter.get_positions()
```

Fetch tops beside the marks at line 99:

```python
    book_tops = adapter.get_book_tops(all_symbols)
```

Replace the submit loop at lines 136-141:

```python
        for intent in plan.intents:
            try:
                price: float | None = None
                if intent.order_type == "LIMIT":
                    top = book_tops.get(intent.symbol)
                    if top is None:
                        raise RuntimeError(f"skip:no_book_top {intent.symbol}")
                    bid, ask = top
                    raw = bid if intent.side == "BUY" else ask
                    tick = filters[intent.symbol].price_tick
                    price = round_to_tick(raw, tick, intent.side)
                adapter.submit(intent, price)
                submitted.append(intent)
            except Exception as exc:  # per-order isolation
                # Carry the price into the failure text. A GTX rejection means
                # the limit would have crossed, and price-beside-side is what
                # tells you the tick rounding went the wrong way — a rejection
                # RATE above noise is decision 4's diagnostic, and it is
                # unreadable without the price.
                detail = str(exc) if price is None else f"{exc} (price={price})"
                failed.append((intent, detail))
```

Add `from portfolio.sizing import round_to_tick` to the imports.

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/trade/ -q`
Expected: PASS — the whole trade suite.

- [ ] **Step 5: Full gates and commit**

```bash
make lint-py && make typecheck && make test
```

All three must be green here — this is the task that restores the suite.

```bash
git add trade/xsmom_executor.py tests/trade/test_xsmom_executor.py
git commit -m "feat(executor): cancel stale orders, price limits at the touch"
```

---

### Task 8: Docs and surfaces

**Files:**

- Modify: `CLAUDE.md` (the `trade/` row context), `README.md` if it documents the order path
- Modify: `.claude/context/execution.md` (the execution-layer deep reference)

- [ ] **Step 1: Check which surfaces actually mention the order path**

```bash
grep -rn "submit_market\|MARKET\|taker\|order type" CLAUDE.md README.md .claude/context/execution.md
```

- [ ] **Step 2: Update each hit**

Record three things, since they are the ones a future session will get wrong: risk-reducing
orders are MARKET **by design** (not an oversight); the backtest still charges the **taker**
rate deliberately, so realised cost should come in *below* model; and cancel-before-plan is
a **correctness precondition** of fire-and-forget, not a tidy-up.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "docs: record the XS maker execution path and its preconditions"
```

---

## What this plan does NOT do

- **No live or testnet run.** Every task here is offline and mocked. The rehearsal is a
  separate, operator-gated activity (`docs/plans/scratch/xsmom-testnet-rehearsal-plan-2026-08-13.md`),
  and its prerequisite is **ONE-WAY** position mode — the executor raises on hedge mode at
  `trade/binance_futures.py:80`.
- **No measurement of the saving.** Maker fill rate is unmeasurable before live capital;
  testnet fills are synthetic. Decisions 2 and 4 in the spec both name the live fill rate
  as their reversal trigger for exactly this reason.
- **No change to the backtest cost model** (decision 5).
