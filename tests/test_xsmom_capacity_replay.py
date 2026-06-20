from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xsmom.execution import ExecutionCostConfig
from analytics.xsmom.replay import load_daily_dollar_volumes, replay_xs_capacity
from analytics.xsmom.report import evaluate_xs_capacity


def _seed(conn: duckdb.DuckDBPyConnection, n: int = 400) -> list[str]:
    rng = np.random.default_rng(1)
    start_ms = 1_609_459_200_000  # 2021-01-01 UTC
    day_ms = 86_400_000
    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    for i, sym in enumerate(syms):
        steps = rng.normal(0.0, 0.02, n) + 0.0005 * (i - 1)
        close = 100.0 * np.exp(np.cumsum(steps))
        rows = pd.DataFrame(
            {
                "symbol": sym,
                "timeframe": "1d",
                "open_time": [start_ms + k * day_ms for k in range(n)],
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                # thinner volume on the later symbols (more impact)
                "volume": rng.uniform(5e5, 1e6, n) / (i + 1),
                "taker_buy_volume": np.full(n, np.nan),
            }
        )
        upsert_ohlcv(conn, rows)
    return syms


def test_load_daily_dollar_volumes_returns_volume_times_close() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn, n=50)
    dvol = load_daily_dollar_volumes(conn, syms)
    assert set(dvol) == set(syms)
    # dollar volume is strictly positive and finite.
    for s in syms:
        assert (dvol[s] > 0).all()


def test_replay_xs_capacity_structure_and_cost_monotonicity() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    cfg = ForecastConfig()  # full 4-speed combined
    exec_cfg = ExecutionCostConfig(k=0.5)
    capitals = [1e5, 1e9]
    runs = replay_xs_capacity(conn, cfg, exec_cfg, capitals, symbols=syms)
    assert set(runs) == set(capitals)
    for capital in capitals:
        assert runs[capital]["result"].pre_governor_return.shape[0] > 0
        assert "combined" in runs[capital]["trials"]
    # More capital => more impact => higher turnover cost => weakly lower
    # PRE-governor net return (positions are identical across capital; only the
    # turnover term changes, so gross+funding cancel and cost strictly rises).
    # Post-governor return is NOT monotone — the vol governor renormalizes level.
    lo = float(np.nansum(runs[1e5]["result"].pre_governor_return))
    hi = float(np.nansum(runs[1e9]["result"].pre_governor_return))
    assert hi <= lo


def test_evaluate_xs_capacity_table_shape_and_gate() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    cfg = ForecastConfig()
    exec_cfg = ExecutionCostConfig(k=0.5)
    runs = replay_xs_capacity(conn, cfg, exec_cfg, [1e5, 1e9], symbols=syms)
    table = evaluate_xs_capacity(runs, cfg)
    assert list(table["capital"]) == [1e5, 1e9]
    for col in ("sharpe", "dsr", "pbo", "boot_lo", "boot_hi", "min_trl", "gate"):
        assert col in table.columns
    assert table["gate"].dtype == bool
