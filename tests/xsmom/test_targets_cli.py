from __future__ import annotations

import sys
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xsmom.live import build_target_book, position_deltas
from tools.xsmom_targets import (
    format_target_table,
    load_latest_snapshot,
    main,
    write_snapshot,
)

_DAY = 86_400_000
_SYMS = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]


def _make_closes(n: int = 400) -> dict[str, pd.Series]:
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(5)
    return {
        sym: pd.Series(
            100.0 * np.exp(np.cumsum(rng.normal(0.0005 * (i - 1), 0.02, n))), index=idx
        )
        for i, sym in enumerate(_SYMS)
    }


def _seed_db(path: str, n: int = 400) -> None:
    conn = duckdb.connect(path)
    init_schema(conn)
    rng = np.random.default_rng(5)
    start = 1_609_459_200_000
    for i, sym in enumerate(_SYMS):
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0005 * (i - 1), 0.02, n)))
        upsert_ohlcv(
            conn,
            pd.DataFrame(
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
            ),
        )
    conn.close()


def test_format_and_snapshot_round_trip(tmp_path: Any) -> None:
    closes = _make_closes()
    fundings = {s: pd.Series(0.0, index=c.index) for s, c in closes.items()}
    book = build_target_book(closes, fundings, ForecastConfig(), 10_000.0)
    txt = format_target_table(book, position_deltas(book, None))
    assert "XS target positions" in txt
    path = write_snapshot(book, tmp_path)
    assert path.exists()
    loaded = load_latest_snapshot(tmp_path)
    assert loaded is not None
    assert loaded["next_period_date"] == book.next_period_date


def test_load_latest_snapshot_empty_dir(tmp_path: Any) -> None:
    assert load_latest_snapshot(tmp_path) is None


def test_main_prints_and_writes_snapshot(
    tmp_path: Any, capsys: Any, monkeypatch: Any
) -> None:
    db = tmp_path / "a.db"
    _seed_db(str(db))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "xsmom_targets",
            "--db",
            str(db),
            "--symbols",
            ",".join(_SYMS),
            "--snapshot-dir",
            str(tmp_path),
            "--capital",
            "10000",
        ],
    )
    main()
    out = capsys.readouterr().out
    assert "XS target positions" in out
    assert list(tmp_path.glob("*.json"))
