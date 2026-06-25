from __future__ import annotations

from pathlib import Path
from typing import Any

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
