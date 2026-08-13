from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

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
        rows = pd.DataFrame(
            {
                "symbol": sym,
                "timeframe": "1d",
                "open_time": [start + k * _DAY for k in range(n)],
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000.0,
                "taker_buy_volume": 500.0,
            }
        )
        upsert_ohlcv(conn, rows)
    return syms


class _FakeAdapter:
    def __init__(
        self,
        equity: float,
        positions: dict[str, float],
        marks: dict[str, float],
        mode: str = "dry_run",
    ) -> None:
        self._equity = equity
        self._positions = positions
        self._marks = marks
        self.mode = mode
        self.submitted: list[OrderIntent] = []
        self.config_calls = 0
        self.fail_symbol: str | None = None
        self.open_order_symbols: set[str] = set()
        self.cancelled: list[str] = []
        self.book_tops: dict[str, tuple[float, float]] = {}
        self.submitted_prices: list[float | None] = []
        self.calls: list[str] = []  # ordering probe
        self.cancel_raises = False

    def get_equity(self) -> float:
        return self._equity

    def get_positions(self) -> dict[str, float]:
        self.calls.append("positions")
        return dict(self._positions)

    def get_marks(self, symbols: list[str]) -> dict[str, float]:
        return {s: self._marks.get(s, 100.0) for s in symbols}

    def get_filters(self, symbols: list[str]):  # type: ignore[no-untyped-def]
        from trade.routing import ExchangeFilters

        return {s: ExchangeFilters(s, 0.001, 0.001, 5.0, 0.01) for s in symbols}

    def ensure_account_config(self, symbols: list[str], *, leverage: int) -> None:
        self.config_calls += 1

    def get_open_order_symbols(self) -> set[str]:
        return set(self.open_order_symbols)

    def cancel_open_orders(self, symbol: str) -> None:
        if self.cancel_raises:
            raise RuntimeError("cancel failed")
        self.calls.append(f"cancel:{symbol}")
        self.cancelled.append(symbol)

    def get_book_tops(self, symbols: list[str]) -> dict[str, tuple[float, float]]:
        return {s: self.book_tops[s] for s in symbols if s in self.book_tops}

    def submit(
        self, intent: OrderIntent, price: float | None = None
    ) -> dict[str, object]:
        if self.fail_symbol in (intent.symbol, "*"):  # "*" fails every order
            raise RuntimeError("rejected")
        self.submitted.append(intent)
        self.submitted_prices.append(price)
        return {"ok": True}


def _limits(**kw: Any) -> RiskLimits:
    base: dict[str, Any] = {
        "max_gross_leverage": 10.0,
        "max_position_notional_frac": 1.0,
        "max_drawdown_frac": 0.5,
        "max_run_turnover_frac": 10.0,
        "max_data_staleness_hours": 1e9,
        "min_active_positions": 0,
    }
    base.update(kw)
    return RiskLimits(**base)


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
    # Cold-start opens route through LIMIT pricing (this task's own wiring), so
    # a quote is required for the submit to succeed; the prior submit_market
    # path ignored order_type entirely and never needed one.
    adapter.book_tops = dict.fromkeys(syms, (99.98, 100.02))
    state_path = tmp_path / "execution_state_dry_run.json"
    res = run_once(
        conn,
        adapter,
        ForecastConfig(),
        syms,
        _limits(),
        no_trade_band_frac=0.0,
        exchange_leverage=5,
        state_path=state_path,
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
        conn,
        adapter,
        ForecastConfig(),
        syms,
        _limits(max_data_staleness_hours=0.0),  # force staleness breach
        no_trade_band_frac=0.0,
        exchange_leverage=5,
        state_path=tmp_path / "s.json",
        now=pd.Timestamp("2022-12-31", tz="UTC"),  # well after the last seeded bar
    )
    assert res.verdict.allowed is False
    assert res.submitted == [] and adapter.config_calls == 0


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


def test_run_once_isolates_per_order_failure(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=10_000.0, positions={}, marks={})
    adapter.fail_symbol = "*"  # every submit raises
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
    run_once(
        conn,
        adapter,
        ForecastConfig(),
        syms,
        _limits(),
        no_trade_band_frac=0.0,
        exchange_leverage=5,
        state_path=p,
        now=pd.Timestamp("2022-02-05", tz="UTC"),
    )
    assert load_state(p)["peak_equity"] == 12_000.0  # not lowered to 10k


def test_capital_override_does_not_ratchet_peak_equity(tmp_path: Path) -> None:
    """A hypothetical sizing run must not write the REAL risk state.

    Measured 2026-08-06: a one-off `--capital 5000` against an account whose
    true equity was 1201.33 ratcheted `peak_equity` 2350.80 -> 5000.00
    *permanently*, leaving a floor of 3750.00 — 3.1x the real equity — that
    would have gone on halting even if the account tripled. `--capital` is
    documented purely as a sizing knob ("size the book off this fixed capital
    instead of live account equity"), so mutating the drawdown high-water mark
    is an undocumented side effect on a safety control.
    """
    p = tmp_path / "s.json"
    save_state(p, {"peak_equity": 2350.80, "kill_switch": False, "last_run": {}})
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=1201.33, positions={}, marks={})
    run_once(
        conn,
        adapter,
        ForecastConfig(),
        syms,
        _limits(),
        no_trade_band_frac=0.0,
        exchange_leverage=5,
        state_path=p,
        now=pd.Timestamp("2022-02-05", tz="UTC"),
        capital_override=5_000.0,
    )
    assert load_state(p)["peak_equity"] == 2350.80


def test_live_equity_still_ratchets_peak_equity(tmp_path: Path) -> None:
    """Positive control for the test above — the ratchet must still WORK.

    Without this, `new_peak = prior_peak` unconditionally (i.e. deleting the
    high-water mark entirely) would satisfy the override test while silently
    disabling the drawdown guard on real runs.
    """
    p = tmp_path / "s.json"
    save_state(p, {"peak_equity": 2350.80, "kill_switch": False, "last_run": {}})
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=5_000.0, positions={}, marks={})
    run_once(
        conn,
        adapter,
        ForecastConfig(),
        syms,
        _limits(),
        no_trade_band_frac=0.0,
        exchange_leverage=5,
        state_path=p,
        now=pd.Timestamp("2022-02-05", tz="UTC"),
    )
    assert load_state(p)["peak_equity"] == 5_000.0


def test_last_run_records_whether_capital_was_pinned(tmp_path: Path) -> None:
    """The corrupting run left NO audit trail — `last_run` records only the last
    run, so a capital pin that poisons the peak is unrecoverable from disk.
    Recording the pin is what makes the next such incident diagnosable.
    """
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    kw: dict[str, Any] = {
        "no_trade_band_frac": 0.0,
        "exchange_leverage": 5,
        "now": pd.Timestamp("2022-02-05", tz="UTC"),
    }

    pinned = tmp_path / "pinned.json"
    run_once(
        conn,
        _FakeAdapter(equity=1201.33, positions={}, marks={}),
        ForecastConfig(),
        syms,
        _limits(),
        state_path=pinned,
        capital_override=2_000.0,
        **kw,
    )
    assert load_state(pinned)["last_run"]["capital_override"] == 2_000.0

    live = tmp_path / "live.json"
    run_once(
        conn,
        _FakeAdapter(equity=1201.33, positions={}, marks={}),
        ForecastConfig(),
        syms,
        _limits(),
        state_path=live,
        **kw,
    )
    assert load_state(live)["last_run"]["capital_override"] is None


def test_run_once_closes_off_universe_position(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    # open position in a symbol no longer in the universe -> must be closed
    adapter = _FakeAdapter(equity=10_000.0, positions={"ZZZUSDT": 5.0}, marks={})
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
    closes = [i for i in res.submitted if i.symbol == "ZZZUSDT"]
    assert len(closes) == 1 and closes[0].reduce_only is True


def test_cold_start_build_passes_overlay(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(
        equity=10_000.0, positions={}, marks=dict.fromkeys(syms, 100.0)
    )
    # Tight steady-state turnover frac, generous gross cap: a cold start (no
    # positions) must STILL pass because the executor forwards current_gross=0,
    # so the establishing branch lifts the turnover cap to the gross cap.
    limits = _limits(max_run_turnover_frac=0.0001)
    res = run_once(
        conn,
        adapter,
        ForecastConfig(),
        syms,
        limits,
        no_trade_band_frac=0.0,
        exchange_leverage=5,
        state_path=tmp_path / "state.json",
    )
    assert res.verdict.allowed is True


def test_steady_state_turnover_blocks(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    # Large existing positions => current gross >> half target => NOT establishing
    # => tight steady-state turnover cap applies => the rebalance is blocked.
    adapter = _FakeAdapter(
        equity=10_000.0,
        positions=dict.fromkeys(syms, 1000.0),
        marks=dict.fromkeys(syms, 100.0),
    )
    limits = _limits(max_run_turnover_frac=0.0001)
    res = run_once(
        conn,
        adapter,
        ForecastConfig(),
        syms,
        limits,
        no_trade_band_frac=0.0,
        exchange_leverage=5,
        state_path=tmp_path / "state.json",
    )
    assert res.verdict.allowed is False and any(
        "turnover" in a.lower() for a in res.verdict.aborts
    )


def test_capital_override_sizes_off_fixed_capital(tmp_path: Path) -> None:
    # `capital_override` makes the book size off a fixed capital instead of the
    # account equity — so a testnet run with the faucet's ~15k balance can be
    # forced to size like the real ~2.3k account (matching min-notional/lot-size
    # discretization for a faithful A/B). The adapter equity is deliberately
    # distinct from the override to prove it is ignored for sizing + reporting.
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=50_000.0, positions={}, marks={})
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
        capital_override=2_300.0,
    )
    assert res.equity == 2_300.0
    assert res.book.capital == 2_300.0


def test_capital_override_none_uses_adapter_equity(tmp_path: Path) -> None:
    # Default (no override) is unchanged: the executor sizes off the live
    # account equity reported by the adapter.
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=10_000.0, positions={}, marks={})
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
    assert res.equity == 10_000.0
    assert res.book.capital == 10_000.0


def test_missing_mark_forces_steady_state_cap(tmp_path: Path) -> None:
    # A held position whose mark is missing (0.0) must NOT be counted as
    # zero-gross — that would under-count current gross and wrongly trip the
    # looser cold-start turnover cap. The executor passes current_gross=None,
    # so the overlay applies the tighter steady-state cap and blocks.
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(
        equity=10_000.0,
        positions={"AAAUSDT": 1_000.0},
        marks={"AAAUSDT": 0.0, "BBBUSDT": 100.0, "CCCUSDT": 100.0},
    )
    limits = _limits(max_run_turnover_frac=0.0001)
    res = run_once(
        conn,
        adapter,
        ForecastConfig(),
        syms,
        limits,
        no_trade_band_frac=0.0,
        exchange_leverage=5,
        state_path=tmp_path / "state.json",
    )
    assert res.verdict.allowed is False and any(
        "turnover" in a.lower() for a in res.verdict.aborts
    )


def test_cancels_stale_orders_before_reading_positions(tmp_path: Path) -> None:
    """A partially-filled resting order is still moving the position while it
    sits there, so the planning read must come AFTER the cancel."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    adapter = _FakeAdapter(equity=10_000.0, positions={}, marks={})
    adapter.open_order_symbols = {"AAAUSDT"}
    adapter.book_tops = dict.fromkeys(syms, (99.98, 100.02))
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
    adapter.book_tops = dict.fromkeys(syms, (99.98, 100.02))
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
    adapter.book_tops = dict.fromkeys(syms, (99.9847, 100.0231))
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
