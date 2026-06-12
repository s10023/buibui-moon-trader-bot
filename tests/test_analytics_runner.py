"""Tests for analytics/analytics_runner.py — lifecycle wiring + resilience."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from analytics.analytics_runner import run_backfill, run_sync


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


class TestRunBackfillResilience:
    def test_continues_past_failing_symbol_then_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        calls: list[str] = []

        def fake_backfill(
            conn: Any, client: Any, symbol: str, timeframe: str, since_ms: int
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

        def fake_sync(conn: Any, client: Any, symbol: str, timeframe: str) -> int:
            calls.append(symbol)
            if symbol == "AAAUSDT":
                raise RuntimeError("boom")
            return 1

        p = _patches(sync={"side_effect": fake_sync})
        with p[0], p[1], p[2], p[3], p[4], pytest.raises(SystemExit):
            run_sync(["AAAUSDT", "BBBUSDT"], ["1h"], db_path=tmp_path / "t.db")
        assert "BBBUSDT" in calls
