"""Tests for analytics.workers — one pool-size helper with a BUIBUI_MAX_WORKERS override (#868)."""

from __future__ import annotations

import pytest

from analytics import workers
from analytics.workers import ENV_VAR, env_max_workers, pool_size

CPU_COUNTS = [None, 1, 2, 3, 4, 5, 8, 16]
TASK_COUNTS = [0, 1, 2, 3, 7, 50]


@pytest.fixture(autouse=True)
def _no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # The suite must not inherit an operator's override from the shell.
    monkeypatch.delenv(ENV_VAR, raising=False)


def _cpus(monkeypatch: pytest.MonkeyPatch, n: int | None) -> None:
    monkeypatch.setattr(workers.os, "cpu_count", lambda: n)


class TestDefaultsReproduceTheOldFormulas:
    """With no override, every site must size its pool exactly as before #868."""

    @pytest.mark.parametrize("cpus", CPU_COUNTS)
    @pytest.mark.parametrize("n", TASK_COUNTS)
    def test_task_bounded_sites(
        self, monkeypatch: pytest.MonkeyPatch, cpus: int | None, n: int
    ) -> None:
        # scanner.run_scan_cycle and both param_sweep pools.
        _cpus(monkeypatch, cpus)
        old = max(1, min((cpus or 2) - 1, n))
        assert pool_size(n) == old

    @pytest.mark.parametrize("cpus", CPU_COUNTS)
    def test_capped_backtest_runner_sites(
        self, monkeypatch: pytest.MonkeyPatch, cpus: int | None
    ) -> None:
        # Both backtest_runner combo pools: min(4, max(1, (cpu_count or 1) - 1)).
        _cpus(monkeypatch, cpus)
        old = min(4, max(1, (cpus or 1) - 1))
        assert pool_size(cap=4) == old


class TestOverride:
    def test_override_replaces_the_cpu_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _cpus(monkeypatch, 16)
        monkeypatch.setenv(ENV_VAR, "3")
        assert pool_size(50) == 3

    def test_override_replaces_the_cap_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An explicit operator choice outranks the runner's built-in cap of 4.
        _cpus(monkeypatch, 16)
        monkeypatch.setenv(ENV_VAR, "8")
        assert pool_size(cap=4) == 8

    def test_task_count_still_bounds_an_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_VAR, "12")
        assert pool_size(2) == 2

    def test_override_of_one_runs_serially(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _cpus(monkeypatch, 16)
        monkeypatch.setenv(ENV_VAR, "1")
        assert pool_size(50) == 1
        assert pool_size(cap=4) == 1

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_means_unset(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        _cpus(monkeypatch, 8)
        monkeypatch.setenv(ENV_VAR, raw)
        assert env_max_workers() is None
        assert pool_size(50) == 7

    def test_surrounding_whitespace_is_tolerated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_VAR, " 2 ")
        assert env_max_workers() == 2

    @pytest.mark.parametrize("raw", ["0", "-2", "four", "2.5", "3x"])
    def test_invalid_values_raise_rather_than_fall_back(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        # A silent fallback would run the full default pool, the exact load the
        # operator set the variable to avoid.
        monkeypatch.setenv(ENV_VAR, raw)
        with pytest.raises(ValueError, match=ENV_VAR):
            pool_size(10)


class TestEverySiteUsesTheHelper:
    """No pool site may restate the formula, or the override silently misses it."""

    @pytest.mark.parametrize(
        "path",
        [
            "analytics/signal/scanner.py",
            "analytics/param_sweep.py",
            "analytics/backtest_runner.py",
        ],
    )
    def test_no_inline_cpu_count_sizing(self, path: str) -> None:
        from pathlib import Path

        src = (Path(__file__).resolve().parent.parent / path).read_text(
            encoding="utf-8"
        )
        assert "os.cpu_count(" not in src
        assert "pool_size(" in src
