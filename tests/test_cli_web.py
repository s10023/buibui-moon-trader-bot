"""Tests for cli/web.py — the web entry's config resolution.

The behaviour under test: `buibui web` with no --config used to leave
BUIBUI_CONFIG unset, so the backend served GET /api/active-config empty and the
UI read "no config" while the signal daemon beside it was running a real one.
It now auto-picks by UTC weekday, exactly like `buibui signal watch`.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cli.web import run_web_server


def _args(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 8000,
        "reload": False,
        "config": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class TestWebConfigResolution:
    def test_explicit_config_is_used_verbatim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BUIBUI_CONFIG", raising=False)
        with patch("uvicorn.run") as run:
            run_web_server(_args(config="config/signal_watch_all.toml"))
        assert os.environ["BUIBUI_CONFIG"] == "config/signal_watch_all.toml"
        assert run.call_count == 1

    def test_missing_config_auto_picks_by_utc_weekday(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("BUIBUI_CONFIG", raising=False)
        with patch("uvicorn.run"):
            run_web_server(_args())
        picked = Path(os.environ["BUIBUI_CONFIG"]).name
        # Whatever today is, it must be one of the three that partition the
        # calendar -- never empty, which was the old behaviour.
        assert picked in {
            "signal_watch.toml",
            "signal_watch_weekdays.toml",
            "signal_watch_all.toml",
        }
        assert "auto-selected" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            # Mon and Fri share `mon_fri`; the filenames mislead, so this pins
            # the mapping to the day_filter scope rather than to the name.
            (datetime(2026, 8, 3, tzinfo=UTC), "signal_watch_weekdays.toml"),  # Mon
            (datetime(2026, 8, 5, tzinfo=UTC), "signal_watch.toml"),  # Wed
            (datetime(2026, 8, 7, tzinfo=UTC), "signal_watch_weekdays.toml"),  # Fri
            (datetime(2026, 8, 8, tzinfo=UTC), "signal_watch_all.toml"),  # Sat
        ],
    )
    def test_web_picker_agrees_with_the_daemon_picker(
        self, day: datetime, expected: str
    ) -> None:
        # The web entry must not grow its own weekday table. If these ever
        # disagree the UI reports a different config than the daemon is running.
        from analytics.signal_config import pick_default_config_for_today

        assert pick_default_config_for_today(now=day).name == expected
