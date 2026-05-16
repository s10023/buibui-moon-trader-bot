"""Tests for `tools/gate_audit.py` — Phase A engine-side gate audit.

Covers the four gate handlers (volume-suppress, volume-spike-boost, day-filter,
adr-exempt), the four output grains, and the ±threshold verdict rule. The ADR
handler is exercised with a stub OHLCV loader so no DB is touched.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from tools import gate_audit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# 2025-01-06 00:00 UTC = Monday. One ms per day stride.
_DAY_MS = 86_400_000
_MON = pd.Timestamp("2025-01-06T00:00:00Z").value // 1_000_000  # ms epoch


def _trade(
    *,
    strategy: str = "bos",
    tf: str = "1h",
    direction: str = "long",
    symbol: str = "BTCUSDT",
    signal_time: int = _MON,
    pnl_r: float = 0.0,
    low_volume: bool = False,
    volume_spike: bool = False,
) -> dict[str, object]:
    return {
        "strategy": strategy,
        "tf": tf,
        "direction": direction,
        "symbol": symbol,
        "signal_time": signal_time,
        "entry_price": 100.0,
        "sl_price": 99.0,
        "exit_price": 101.0,
        "outcome": "win",
        "pnl_r": pnl_r,
        "low_volume": low_volume,
        "volume_spike": volume_spike,
        "run_id": "run-0",
    }


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Gate handlers
# ---------------------------------------------------------------------------


class TestGateVolumeSuppress:
    def test_only_off_strategies_with_low_volume_flagged(self) -> None:
        df = _frame(
            [
                _trade(strategy="bos", low_volume=True),  # off + lowvol → mask
                _trade(strategy="bos", low_volume=False),  # off + normal → keep
                _trade(strategy="orb", low_volume=True),  # already on → keep
                _trade(strategy="bos", low_volume=True, direction="short"),  # mask
            ]
        )
        mask = gate_audit._gate_volume_suppress(df, {"volume_suppress_off": {"bos"}})
        assert mask.tolist() == [True, False, False, True]

    def test_empty_off_set_returns_no_suppression(self) -> None:
        df = _frame([_trade(strategy="bos", low_volume=True)])
        mask = gate_audit._gate_volume_suppress(df, {"volume_suppress_off": set()})
        assert mask.tolist() == [False]

    def test_null_low_volume_treated_as_false(self) -> None:
        # Pre-PR#371 rows have NULL low_volume — must not crash on .astype(bool)
        # and must never get flagged as suppressed (unknown-volume → kept).
        df = _frame(
            [
                _trade(strategy="bos", low_volume=True),
                _trade(strategy="bos", low_volume=False),
                _trade(strategy="bos", low_volume=False),
            ]
        )
        # Model DuckDB's nullable BOOLEAN: dtype="boolean" admits pd.NA.
        df["low_volume"] = pd.array([True, False, pd.NA], dtype="boolean")
        mask = gate_audit._gate_volume_suppress(df, {"volume_suppress_off": {"bos"}})
        assert mask.tolist() == [True, False, False]


class TestGateVolumeSpikeBoost:
    def test_only_boosted_strategies_with_spike_flagged(self) -> None:
        df = _frame(
            [
                _trade(strategy="engulfing", volume_spike=True),
                _trade(strategy="engulfing", volume_spike=False),
                _trade(strategy="bos", volume_spike=True),
            ]
        )
        mask = gate_audit._gate_volume_spike_boost(
            df, {"volume_spike_boost_on": {"engulfing"}}
        )
        assert mask.tolist() == [True, False, False]

    def test_null_volume_spike_treated_as_false(self) -> None:
        df = _frame(
            [
                _trade(strategy="engulfing", volume_spike=True),
                _trade(strategy="engulfing", volume_spike=False),
            ]
        )
        df["volume_spike"] = pd.array([True, pd.NA], dtype="boolean")
        mask = gate_audit._gate_volume_spike_boost(
            df, {"volume_spike_boost_on": {"engulfing"}}
        )
        assert mask.tolist() == [True, False]


class TestGateDayFilter:
    @pytest.mark.parametrize(
        "dow_offset,expected",
        [
            (0, True),  # Mon → drop
            (1, False),  # Tue → keep
            (2, False),  # Wed → keep
            (3, False),  # Thu → keep
            (4, True),  # Fri → drop
            (5, True),  # Sat → drop
            (6, True),  # Sun → drop
        ],
    )
    def test_tue_thu_suppression(self, dow_offset: int, expected: bool) -> None:
        df = _frame([_trade(signal_time=_MON + dow_offset * _DAY_MS)])
        mask = gate_audit._gate_day_filter(df, {"candidate": "tue_thu"})
        assert mask.tolist() == [expected]

    def test_weekdays_only_drops_sat_sun(self) -> None:
        df = _frame(
            [
                _trade(signal_time=_MON + d * _DAY_MS)  # Mon–Sun
                for d in range(7)
            ]
        )
        mask = gate_audit._gate_day_filter(df, {"candidate": "weekdays"})
        assert mask.tolist() == [False] * 5 + [True, True]

    def test_off_drops_nothing(self) -> None:
        df = _frame([_trade(signal_time=_MON + d * _DAY_MS) for d in range(7)])
        mask = gate_audit._gate_day_filter(df, {"candidate": "off"})
        assert mask.tolist() == [False] * 7

    def test_unknown_candidate_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown candidate"):
            gate_audit._gate_day_filter(_frame([_trade()]), {"candidate": "bogus"})


class TestGateAdrExempt:
    """ADR handler — uses a stub OHLCV loader so no DB is touched."""

    @staticmethod
    def _ohlcv_with_consumed(consumed_at_signal: float, move_up: bool) -> pd.DataFrame:
        """Build a 2-day OHLCV frame where the signal-day candle has the given
        consumed_ratio (today_range / adr14) and the requested chasing direction
        (close in upper half = move_up). ADR14 rolling(14, min_periods=1).mean()
        on 2 rows = (prior_range + today_range) / 2; solving for the prior range
        that produces the target ratio: prior = (2 - target) * today / target.
        """
        open_price = 100.0
        r_today = 0.10  # 10% intraday range on signal day
        r_prior = (2.0 - consumed_at_signal) * r_today / consumed_at_signal

        prior_row = {
            "open_time": _MON - _DAY_MS,
            "open": open_price,
            "high": open_price * (1 + r_prior),
            "low": open_price,
            "close": open_price * (1 + r_prior / 2),
        }
        if move_up:
            s_high = open_price * (1 + r_today)
            s_low = open_price
            s_close = s_high - 0.01  # upper half
        else:
            s_high = open_price
            s_low = open_price * (1 - r_today)
            s_close = s_low + 0.01  # lower half
        signal_row = {
            "open_time": _MON,
            "open": open_price,
            "high": s_high,
            "low": s_low,
            "close": s_close,
        }
        return pd.DataFrame([prior_row, signal_row])

    def test_chasing_long_above_threshold_suppressed(self) -> None:
        df = _frame([_trade(strategy="bos", direction="long")])
        ohlcv = self._ohlcv_with_consumed(0.90, move_up=True)
        params = {
            "exempt": {"bos"},
            "threshold": 0.80,
            "ohlcv_loader": lambda _s, _t: ohlcv,
        }
        mask = gate_audit._gate_adr_exempt(df, params)
        assert mask.tolist() == [True]

    def test_chasing_long_below_threshold_kept(self) -> None:
        df = _frame([_trade(strategy="bos", direction="long")])
        ohlcv = self._ohlcv_with_consumed(0.50, move_up=True)
        params = {
            "exempt": {"bos"},
            "threshold": 0.80,
            "ohlcv_loader": lambda _s, _t: ohlcv,
        }
        assert gate_audit._gate_adr_exempt(df, params).tolist() == [False]

    def test_counter_direction_kept_even_above_threshold(self) -> None:
        # Move was up; SHORT trade is counter-trend → ADR gate doesn't fire.
        df = _frame([_trade(strategy="bos", direction="short")])
        ohlcv = self._ohlcv_with_consumed(0.95, move_up=True)
        params = {
            "exempt": {"bos"},
            "threshold": 0.80,
            "ohlcv_loader": lambda _s, _t: ohlcv,
        }
        assert gate_audit._gate_adr_exempt(df, params).tolist() == [False]

    def test_non_exempt_strategies_skipped(self) -> None:
        df = _frame([_trade(strategy="orb", direction="long")])
        ohlcv = self._ohlcv_with_consumed(0.95, move_up=True)
        params = {
            "exempt": {"bos"},  # orb is not exempt
            "threshold": 0.80,
            "ohlcv_loader": lambda _s, _t: ohlcv,
        }
        assert gate_audit._gate_adr_exempt(df, params).tolist() == [False]

    def test_missing_ohlcv_safe_passthrough(self) -> None:
        df = _frame([_trade(strategy="bos", direction="long")])
        params = {
            "exempt": {"bos"},
            "threshold": 0.80,
            "ohlcv_loader": lambda _s, _t: pd.DataFrame(),
        }
        assert gate_audit._gate_adr_exempt(df, params).tolist() == [False]

    def test_empty_exempt_set_returns_all_false(self) -> None:
        df = _frame([_trade(strategy="bos", direction="long")])
        params = {
            "exempt": set(),
            "threshold": 0.80,
            "ohlcv_loader": lambda _s, _t: pd.DataFrame(),
        }
        assert gate_audit._gate_adr_exempt(df, params).tolist() == [False]


# ---------------------------------------------------------------------------
# Audit table + verdict rule
# ---------------------------------------------------------------------------


class TestBuildAuditTable:
    def test_enable_verdict_when_suppressed_is_loss(self) -> None:
        # 40 low-vol losers + 10 normal-vol winners on strategy bos → ENABLE.
        rows = [
            _trade(strategy="bos", low_volume=True, pnl_r=-1.0) for _ in range(40)
        ] + [_trade(strategy="bos", low_volume=False, pnl_r=1.0) for _ in range(10)]
        df = _frame(rows)
        gate = gate_audit.GATE_REGISTRY["volume-suppress"]
        table = gate_audit.build_audit_table(
            df,
            gate,
            {"volume_suppress_off": {"bos"}},
            ["strategy"],
            min_n=30,
            threshold=0.05,
        )
        assert table.iloc[0]["verdict"] == "ENABLE"
        assert table.iloc[0]["n_supp"] == 40
        assert table.iloc[0]["n_kept"] == 10

    def test_disable_verdict_when_suppressed_is_win(self) -> None:
        # 40 low-vol winners + 10 normal-vol losers → suppressing kills winners.
        rows = [
            _trade(strategy="bos", low_volume=True, pnl_r=1.0) for _ in range(40)
        ] + [_trade(strategy="bos", low_volume=False, pnl_r=-1.0) for _ in range(10)]
        df = _frame(rows)
        gate = gate_audit.GATE_REGISTRY["volume-suppress"]
        table = gate_audit.build_audit_table(
            df,
            gate,
            {"volume_suppress_off": {"bos"}},
            ["strategy"],
            min_n=30,
            threshold=0.05,
        )
        assert table.iloc[0]["verdict"] == "DISABLE"

    def test_insufficient_when_below_min_n(self) -> None:
        # 5 suppressed losers — below min_n=30 → INSUFFICIENT.
        rows = [
            _trade(strategy="bos", low_volume=True, pnl_r=-1.0) for _ in range(5)
        ] + [_trade(strategy="bos", low_volume=False, pnl_r=1.0) for _ in range(20)]
        df = _frame(rows)
        gate = gate_audit.GATE_REGISTRY["volume-suppress"]
        table = gate_audit.build_audit_table(
            df,
            gate,
            {"volume_suppress_off": {"bos"}},
            ["strategy"],
            min_n=30,
            threshold=0.05,
        )
        assert table.iloc[0]["verdict"] == "INSUFFICIENT"

    def test_grain_strategy_tf_dir_separates_directions(self) -> None:
        # LONG side: 40 lowvol losers → ENABLE.
        # SHORT side: 40 lowvol winners → DISABLE. Verdict must split.
        rows = (
            [
                _trade(
                    strategy="bos",
                    tf="1h",
                    direction="long",
                    low_volume=True,
                    pnl_r=-1.0,
                )
                for _ in range(40)
            ]
            + [
                _trade(
                    strategy="bos",
                    tf="1h",
                    direction="short",
                    low_volume=True,
                    pnl_r=1.0,
                )
                for _ in range(40)
            ]
            + [
                _trade(
                    strategy="bos",
                    tf="1h",
                    direction="long",
                    low_volume=False,
                    pnl_r=1.0,
                )
                for _ in range(10)
            ]
        )
        df = _frame(rows)
        gate = gate_audit.GATE_REGISTRY["volume-suppress"]
        table = gate_audit.build_audit_table(
            df,
            gate,
            {"volume_suppress_off": {"bos"}},
            ["strategy", "tf", "direction"],
            min_n=30,
            threshold=0.05,
        )
        verdicts = dict(zip(table["direction"], table["verdict"], strict=True))
        assert verdicts["long"] == "ENABLE"
        assert verdicts["short"] == "DISABLE"


# ---------------------------------------------------------------------------
# Param resolution + CLI smoke
# ---------------------------------------------------------------------------


class TestResolveParams:
    def _args(self, **kw: object) -> argparse.Namespace:
        defaults: dict[str, object] = {"day_filter": None}
        defaults.update(kw)
        return argparse.Namespace(**defaults)

    def test_day_filter_default_tue_thu(self) -> None:
        params = gate_audit._resolve_params("day-filter", None, self._args())
        assert params == {"candidate": "tue_thu"}

    def test_day_filter_uses_args_override(self) -> None:
        params = gate_audit._resolve_params(
            "day-filter", None, self._args(day_filter="weekdays")
        )
        assert params == {"candidate": "weekdays"}

    def test_volume_suppress_no_config_returns_empty_set(self) -> None:
        params = gate_audit._resolve_params("volume-suppress", None, self._args())
        assert params == {"volume_suppress_off": set()}

    def test_adr_exempt_requires_ohlcv_loader(self) -> None:
        with pytest.raises(ValueError, match="adr-exempt gate requires"):
            gate_audit._resolve_params("adr-exempt", None, self._args())

    def test_adr_exempt_accepts_stub_loader(self) -> None:
        def loader(_s: str, _t: str) -> pd.DataFrame:
            return pd.DataFrame()

        params = gate_audit._resolve_params(
            "adr-exempt", None, self._args(), ohlcv_loader=loader
        )
        assert params["exempt"] == set()
        assert params["threshold"] == 0.80
        assert params["ohlcv_loader"] is loader

    def test_unknown_gate_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown gate"):
            gate_audit._resolve_params("bogus", None, self._args())


class TestParser:
    def test_required_gate_positional(self) -> None:
        parser = gate_audit.build_parser()
        args = parser.parse_args(["volume-suppress"])
        assert args.gate == "volume-suppress"
        assert args.grain == "all"
        assert args.min_n == 30

    def test_invalid_gate_choice(self) -> None:
        parser = gate_audit.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["bogus-gate"])

    def test_run_id_accepts_uuid_string(self) -> None:
        # backtest_runs.run_id is VARCHAR (UUID), not int — must not coerce.
        parser = gate_audit.build_parser()
        args = parser.parse_args(
            ["volume-suppress", "--run-id", "8576a830-fc21-463a-8a11-8d2a2a26d29e"]
        )
        assert args.run_id == "8576a830-fc21-463a-8a11-8d2a2a26d29e"


# ---------------------------------------------------------------------------
# Config scoping (load_trades + _resolve_config_run_ids) — file-backed DuckDB
# ---------------------------------------------------------------------------


_MINIMAL_TOML_TUE_THU = """\
symbols    = ["BTCUSDT"]
timeframes = ["1h"]
day_filter = "tue_thu"
"""

_MINIMAL_TOML_MON_FRI = """\
symbols    = ["BTCUSDT"]
timeframes = ["1h"]
day_filter = "mon_fri"
"""


def _build_test_db(db_path: Path) -> None:
    """Two sweeps with disjoint day_filters; trades partitioned by run_id."""
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE backtest_runs (
                run_id VARCHAR, sweep_id VARCHAR, day_filter VARCHAR, run_at_ms BIGINT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE backtest_trades (
                run_id VARCHAR, symbol VARCHAR, timeframe VARCHAR, strategy VARCHAR,
                direction VARCHAR, signal_time BIGINT, entry_price DOUBLE,
                sl_price DOUBLE, exit_price DOUBLE, outcome VARCHAR, pnl_r DOUBLE,
                low_volume BOOLEAN, volume_spike BOOLEAN
            )
            """
        )
        # Sweep A: day_filter=tue_thu, two runs
        conn.execute(
            "INSERT INTO backtest_runs VALUES "
            "('run-A1', 'sweep-A', 'tue_thu', 1000), "
            "('run-A2', 'sweep-A', 'tue_thu', 1000), "
            "('run-B1', 'sweep-B', 'mon_fri', 2000)"
        )
        conn.execute(
            "INSERT INTO backtest_trades VALUES "
            "('run-A1','BTCUSDT','1h','bos','long',0,100.0,99.0,101.0,'win',1.0,FALSE,FALSE),"
            "('run-A2','BTCUSDT','1h','bos','long',0,100.0,99.0,99.5,'loss',-0.5,TRUE,FALSE),"
            "('run-B1','BTCUSDT','1h','bos','long',0,100.0,99.0,101.0,'win',1.0,FALSE,FALSE)"
        )


class TestResolveConfigRunIds:
    def test_picks_latest_sweep_for_day_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        _build_test_db(db)
        cfg = tmp_path / "cfg.toml"
        cfg.write_text(_MINIMAL_TOML_TUE_THU)
        run_ids = gate_audit._resolve_config_run_ids(db, cfg)
        assert set(run_ids) == {"run-A1", "run-A2"}

    def test_returns_empty_when_no_sweep_matches(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        _build_test_db(db)
        cfg = tmp_path / "cfg.toml"
        cfg.write_text('symbols = ["BTCUSDT"]\nday_filter = "weekend"\n')
        run_ids = gate_audit._resolve_config_run_ids(db, cfg)
        assert run_ids == []

    def test_picks_most_recent_when_multiple_sweeps_share_day_filter(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "t.db"
        _build_test_db(db)
        # Insert a SECOND tue_thu sweep that is more recent
        with duckdb.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO backtest_runs VALUES "
                "('run-C1', 'sweep-C', 'tue_thu', 9000)"
            )
        cfg = tmp_path / "cfg.toml"
        cfg.write_text(_MINIMAL_TOML_TUE_THU)
        run_ids = gate_audit._resolve_config_run_ids(db, cfg)
        assert run_ids == ["run-C1"]

    def test_ignores_single_run_backtests_with_null_sweep_id(
        self, tmp_path: Path
    ) -> None:
        """Stray ad-hoc `buibui backtest` (no --sweep) writes rows with
        sweep_id IS NULL. These must NOT shadow a real sweep, even if newer.
        """
        db = tmp_path / "t.db"
        _build_test_db(db)
        with duckdb.connect(str(db)) as conn:
            # Inject a NULL-sweep tue_thu run that is more recent than sweep-A
            conn.execute(
                "INSERT INTO backtest_runs VALUES ('run-null-1', NULL, 'tue_thu', 9999)"
            )
        cfg = tmp_path / "cfg.toml"
        cfg.write_text(_MINIMAL_TOML_TUE_THU)
        run_ids = gate_audit._resolve_config_run_ids(db, cfg)
        # Resolver must skip the NULL-sweep row and return sweep-A's runs
        assert set(run_ids) == {"run-A1", "run-A2"}


class TestLoadTrades:
    def test_run_ids_filter_quoted_safely(self, tmp_path: Path) -> None:
        # Pass run_id strings through parameterized SQL — must not break on
        # hyphens or be mis-typed as int.
        db = tmp_path / "t.db"
        _build_test_db(db)
        df = gate_audit.load_trades(db, ["run-A1", "run-A2"], since_ms=None)
        assert set(df["run_id"].tolist()) == {"run-A1", "run-A2"}
        assert len(df) == 2

    def test_run_ids_empty_list_returns_empty_frame(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        _build_test_db(db)
        df = gate_audit.load_trades(db, [], since_ms=None)
        assert df.empty

    def test_no_run_ids_returns_all(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        _build_test_db(db)
        df = gate_audit.load_trades(db, None, since_ms=None)
        assert len(df) == 3
