"""Tests for cli/signal.py argument wiring (watch handler)."""

import argparse
import pathlib
from unittest.mock import MagicMock

from cli import signal as cli_signal


def _watch_args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "config": pathlib.Path("config/signal_watch.toml"),
        "symbols": None,
        "timeframes": None,
        "strategies": None,
        "tp_r": None,
        "min_sl_pct": None,
        "telegram": False,
        "state_file": "signal_state.json",
        "secondary_symbol": None,
        "smt_pairs": None,
        "once": True,
        "db_path": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_watch_forwards_db_path_to_runner(monkeypatch: object) -> None:
    mock = MagicMock()
    monkeypatch.setattr(cli_signal.signal_runner, "run_signal_watch", mock)  # type: ignore[attr-defined]

    cli_signal.run_signal_watch(_watch_args(db_path="/tmp/smoke.duckdb"))

    kwargs = mock.call_args.kwargs
    # --db-path must reach the runner so a smoke run never writes the real DB.
    assert kwargs["db_path"] == pathlib.Path("/tmp/smoke.duckdb")
    assert kwargs["max_cycles"] == 1


def test_watch_omits_db_path_when_not_given(monkeypatch: object) -> None:
    mock = MagicMock()
    monkeypatch.setattr(cli_signal.signal_runner, "run_signal_watch", mock)  # type: ignore[attr-defined]

    cli_signal.run_signal_watch(_watch_args(db_path=None))

    # No override → runner uses its own DEFAULT_DB_PATH default.
    assert "db_path" not in mock.call_args.kwargs
