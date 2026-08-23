"""Tests for analytics/analytics_runner.py — lifecycle wiring + resilience."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import duckdb
import pandas as pd
import pytest

from analytics.analytics_runner import run_backfill, run_sync
from analytics.data_fetcher import OHLCV_COLUMNS
from analytics.data_store import init_schema, upsert_ohlcv


def _patches(**overrides: Any) -> Any:
    """Patch every collaborator of the runner; return the patch context tuple."""
    return (
        patch("analytics.analytics_runner.create_client", return_value=MagicMock()),
        patch(
            "analytics.analytics_runner.refresh_symbol_lifecycle",
            **overrides.get("lifecycle", {"return_value": 0}),
        ),
        patch(
            "analytics.analytics_runner.backfill",
            **overrides.get("backfill", {"return_value": 1}),
        ),
        patch(
            "analytics.analytics_runner.sync",
            **overrides.get("sync", {"return_value": 1}),
        ),
        patch("analytics.analytics_runner._sync_ancillary"),
    )


def _make_df(
    open_times: list[int], symbol: str = "BTCUSDT", timeframe: str = "1h"
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "open_time": t,
                "open": 30000.0,
                "high": 31000.0,
                "low": 29500.0,
                "close": 30500.0,
                "volume": 100.0,
            }
            for t in open_times
        ],
        columns=OHLCV_COLUMNS,
    )


class TestRunBackfillResilience:
    def test_continues_past_failing_symbol_then_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        calls: list[str] = []

        def fake_backfill(
            conn: Any,
            client: Any,
            symbol: str,
            timeframe: str,
            since_ms: int,
            venue: Any = None,
        ) -> int:
            calls.append(symbol)
            if symbol == "AAAUSDT":
                raise RuntimeError("boom")
            return 1

        p = _patches(backfill={"side_effect": fake_backfill})
        with p[0], p[1], p[2], p[3], p[4], pytest.raises(SystemExit):
            run_backfill(["AAAUSDT", "BBBUSDT"], ["1h"], 0, db_path=tmp_path / "t.db")
        assert "BBBUSDT" in calls  # later symbol still processed

    def test_all_green_does_not_exit(self, tmp_path: Path) -> None:
        p = _patches()
        with p[0], p[1], p[2], p[3], p[4]:
            run_backfill(["AAAUSDT"], ["1h"], 0, db_path=tmp_path / "t.db")

    def test_lifecycle_failure_is_nonfatal(self, tmp_path: Path) -> None:
        p = _patches(lifecycle={"side_effect": RuntimeError("api down")})
        with p[0], p[1] as mock_life, p[2] as mock_backfill, p[3], p[4]:
            run_backfill(["AAAUSDT"], ["1h"], 0, db_path=tmp_path / "t.db")
        assert mock_life.called
        assert mock_backfill.called  # ingest proceeded despite lifecycle failure

    def test_lifecycle_called_with_resolved_symbols(self, tmp_path: Path) -> None:
        p = _patches()
        with p[0], p[1] as mock_life, p[2], p[3], p[4]:
            run_backfill(["AAAUSDT", "BBBUSDT"], ["1h"], 0, db_path=tmp_path / "t.db")
        assert mock_life.call_args[0][2] == ["AAAUSDT", "BBBUSDT"]


class TestRunSyncResilience:
    def test_continues_past_failing_symbol_then_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        calls: list[str] = []

        def fake_sync(
            conn: Any, client: Any, symbol: str, timeframe: str, venue: Any = None
        ) -> int:
            calls.append(symbol)
            if symbol == "AAAUSDT":
                raise RuntimeError("boom")
            return 1

        p = _patches(sync={"side_effect": fake_sync})
        with p[0], p[1], p[2], p[3], p[4], pytest.raises(SystemExit):
            run_sync(["AAAUSDT", "BBBUSDT"], ["1h"], db_path=tmp_path / "t.db")
        assert "BBBUSDT" in calls


class TestVenueIsAlwaysBinance:
    """analytics_runner's client is always create_client() (real Binance) regardless of
    DATA_SOURCE, so its stored rows must stay tagged 'binance' even when a stray
    DATA_SOURCE=okx is set in the environment. resolve_venue()'s env fallback exists for
    callers that pick their client via the same env var (signal_runner's
    create_data_client()); this runner hardcodes Binance and must say so explicitly
    rather than let backfill()/sync() infer a mismatched venue from the environment.
    """

    def test_run_backfill_stores_binance_even_with_data_source_okx(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("DATA_SOURCE", "okx")
        db_path = tmp_path / "t.db"
        df = _make_df([1_000])
        with (
            patch("analytics.analytics_runner.create_client", return_value=MagicMock()),
            patch("analytics.analytics_runner.refresh_symbol_lifecycle"),
            patch("analytics.analytics_runner._sync_ancillary"),
            patch("analytics.data_sync.fetch_klines", return_value=df),
        ):
            run_backfill(["BTCUSDT"], ["1h"], 0, db_path=db_path)

        conn = duckdb.connect(str(db_path))
        try:
            venues = {
                r[0]
                for r in conn.execute("SELECT DISTINCT venue FROM ohlcv_all").fetchall()
            }
        finally:
            conn.close()
        assert venues == {"binance"}

    def test_run_sync_stores_binance_even_with_data_source_okx(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("DATA_SOURCE", "okx")
        db_path = tmp_path / "t.db"
        seed = duckdb.connect(str(db_path))
        init_schema(seed)
        upsert_ohlcv(seed, _make_df([1_000_000]), venue="binance")
        seed.close()

        with (
            patch("analytics.analytics_runner.create_client", return_value=MagicMock()),
            patch("analytics.analytics_runner.refresh_symbol_lifecycle"),
            patch("analytics.analytics_runner._sync_ancillary"),
            patch(
                "analytics.data_sync.fetch_klines",
                return_value=_make_df([2_000_000]),
            ),
        ):
            run_sync(["BTCUSDT"], ["1h"], db_path=db_path)

        conn = duckdb.connect(str(db_path))
        try:
            venues = {
                r[0]
                for r in conn.execute("SELECT DISTINCT venue FROM ohlcv_all").fetchall()
            }
        finally:
            conn.close()
        assert venues == {"binance"}
