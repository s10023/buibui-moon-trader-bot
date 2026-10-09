"""#970: the backtest resolves ``volume_suppress`` exactly as the live scanner does.

The live scanner honours ``volume_suppress_{long,short}_per_tf``; the backtest
loader never parsed them, so every backtest of a live config ran those cells
under the strategy-level or directional flag instead. The rule now lives in
``analytics.volume_suppress`` and both sides call it, but "both call it" is the
claim a parity test exists to check rather than assume: the loaders parse the
TOML separately, and a table one of them drops looks identical to a rule one of
them restates.
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

from analytics.backtest_config import (
    BacktestSweepConfig,
    StrategyOverride,
    load_backtest_config,
)
from analytics.backtest_lib import BacktestResult
from analytics.backtest_runner import _collect_sweep_results
from analytics.data_store import init_schema, upsert_ohlcv
from analytics.signal.resolvers import (
    _resolve_volume_suppress,
    _resolve_volume_suppress_long,
    _resolve_volume_suppress_short,
)
from analytics.signal_config import load_signal_config
from analytics.store.backtest_runs import _backtest_run_id
from analytics.volume_suppress import pick_volume_suppress

TFS = ("15m", "1h", "4h", "1d")
DIRECTIONS = ("long", "short")
REPO_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
LIVE_CONFIGS = (
    "signal_watch.toml",
    "signal_watch_weekdays.toml",
    "signal_watch_all.toml",
)

# One strategy per precedence level. ``fvg`` is deliberately absent from the
# file: a strategy with no override must fall through to the global flag.
_FIXTURE_TOML = """\
[backtest]
volume_suppress = {glob}

[strategy_params.engulfing]
tp_r = 2.0

[strategy_params.bos]
volume_suppress = {not_glob}

[strategy_params.pin_bar]
volume_suppress = false
volume_suppress_long = true

[strategy_params.orb]
volume_suppress_short = false

[strategy_params.doji]
volume_suppress = false
volume_suppress_long = false

[strategy_params.doji.volume_suppress_long_per_tf]
"1h" = true
"4h" = true

[strategy_params.marubozu]
volume_suppress = true

[strategy_params.marubozu.volume_suppress_long_per_tf]
"15m" = false

[strategy_params.marubozu.volume_suppress_short_per_tf]
"15m" = false
"1d" = false
"""


def _write_fixture(tmp_path: Path, glob: bool) -> Path:
    p = tmp_path / "signal_watch.toml"
    p.write_text(
        _FIXTURE_TOML.format(glob=str(glob).lower(), not_glob=str(not glob).lower()),
        encoding="utf-8",
    )
    return p


def _expected(glob: bool) -> dict[tuple[str, str, str], bool]:
    """Hand-written truth for the fixture, so a bug shared by both sides still fails."""
    exp: dict[tuple[str, str, str], bool] = {}
    for tf, d in itertools.product(TFS, DIRECTIONS):
        exp[("engulfing", tf, d)] = glob  # no override -> global
        exp[("fvg", tf, d)] = glob  # not in the file -> global
        exp[("bos", tf, d)] = not glob  # strategy-wide beats global
        exp[("pin_bar", tf, d)] = d == "long"  # direction beats strategy
        exp[("orb", tf, d)] = glob if d == "long" else False  # short off only
        # doji: strategy False, long False, long@1h/4h True (per-tf beats direction)
        exp[("doji", tf, d)] = d == "long" and tf in ("1h", "4h")
        # marubozu: strategy True; long off @15m, short off @15m/1d (per-tf beats strategy)
        exp[("marubozu", tf, d)] = not (
            (d == "long" and tf == "15m") or (d == "short" and tf in ("15m", "1d"))
        )
    return exp


def _live_effective(path: Path, strategy: str, tf: str, direction: str) -> bool:
    """The live path: scanner resolvers, then the engine's per-direction pick."""
    cfg = load_signal_config(path)
    return pick_volume_suppress(
        direction,
        _resolve_volume_suppress(
            cfg.strategy_params, strategy, cfg.backtest.volume_suppress
        ),
        _resolve_volume_suppress_long(cfg.strategy_params, strategy, tf),
        _resolve_volume_suppress_short(cfg.strategy_params, strategy, tf),
    )


def _backtest_effective(
    cfg: BacktestSweepConfig, strategy: str, tf: str, direction: str
) -> bool:
    """The backtest path: what the runner hands ``run_backtest`` for one cell."""
    return pick_volume_suppress(
        direction,
        cfg.effective_volume_suppress(strategy),
        cfg.effective_volume_suppress_long(strategy, tf),
        cfg.effective_volume_suppress_short(strategy, tf),
    )


class TestFixtureParity:
    @pytest.mark.parametrize("glob", [True, False])
    def test_both_loaders_match_the_hand_written_table(
        self, tmp_path: Path, glob: bool
    ) -> None:
        path = _write_fixture(tmp_path, glob)
        bt = load_backtest_config(path)
        expected = _expected(glob)
        for (strategy, tf, direction), want in expected.items():
            cell = (strategy, tf, direction)
            assert _backtest_effective(bt, strategy, tf, direction) is want, cell
            assert _live_effective(path, strategy, tf, direction) is want, cell

    @pytest.mark.parametrize("glob", [True, False])
    def test_live_and_backtest_agree_on_every_cell(
        self, tmp_path: Path, glob: bool
    ) -> None:
        path = _write_fixture(tmp_path, glob)
        bt = load_backtest_config(path)
        for strategy, tf, direction in itertools.product(
            [*bt.strategy_params, "fvg"], TFS, DIRECTIONS
        ):
            assert _backtest_effective(bt, strategy, tf, direction) == _live_effective(
                path, strategy, tf, direction
            ), (
                strategy,
                tf,
                direction,
            )

    def test_per_tf_tables_are_parsed(self, tmp_path: Path) -> None:
        """Mutation guard: a loader that drops the tables fails here, not only above."""
        bt = load_backtest_config(_write_fixture(tmp_path, True))
        assert bt.strategy_params["doji"].volume_suppress_long_per_tf == {
            "1h": True,
            "4h": True,
        }
        assert bt.strategy_params["marubozu"].volume_suppress_short_per_tf == {
            "15m": False,
            "1d": False,
        }
        assert bt.strategy_params["engulfing"].volume_suppress_long_per_tf == {}

    def test_timeframe_is_what_selects_the_per_tf_value(self, tmp_path: Path) -> None:
        """Dropping ``tf`` (the pre-#970 call) must change the answer on this fixture."""
        bt = load_backtest_config(_write_fixture(tmp_path, True))
        assert bt.effective_volume_suppress_long("doji", "1h") is True
        assert bt.effective_volume_suppress_long("doji") is False
        assert bt.effective_volume_suppress_short("marubozu", "15m") is False
        assert bt.effective_volume_suppress_short("marubozu") is None


class TestRealConfigParity:
    @pytest.mark.parametrize("name", LIVE_CONFIGS)
    def test_shipped_config_resolves_identically(self, name: str) -> None:
        path = REPO_CONFIG_DIR / name
        live = load_signal_config(path)
        bt = load_backtest_config(path)
        assert set(bt.strategy_params) == set(live.strategy_params)
        assert bt.volume_suppress == live.backtest.volume_suppress
        for strategy, tf, direction in itertools.product(
            [*live.strategy_params, "not_in_config"], TFS, DIRECTIONS
        ):
            assert _backtest_effective(bt, strategy, tf, direction) == _live_effective(
                path, strategy, tf, direction
            ), (
                name,
                strategy,
                tf,
                direction,
            )

    def test_the_shipped_configs_really_use_the_per_tf_tables(self) -> None:
        """Teeth: parity over configs that never exercise the tables proves nothing."""
        differing = 0
        for name in LIVE_CONFIGS:
            bt = load_backtest_config(REPO_CONFIG_DIR / name)
            for strategy, tf in itertools.product(bt.strategy_params, TFS):
                for direction in DIRECTIONS:
                    with_tf = _backtest_effective(bt, strategy, tf, direction)
                    long_ = bt.effective_volume_suppress_long(strategy)
                    short = bt.effective_volume_suppress_short(strategy)
                    without_tf = pick_volume_suppress(
                        direction, bt.effective_volume_suppress(strategy), long_, short
                    )
                    differing += with_tf != without_tf
        assert differing > 0


class TestPickVolumeSuppress:
    @pytest.mark.parametrize(
        ("direction", "base", "long", "short", "want"),
        [
            ("long", False, True, None, True),
            ("long", True, False, True, False),
            ("short", True, True, False, False),
            ("short", False, None, True, True),
            ("long", True, None, False, True),
            ("short", False, True, None, False),
        ],
    )
    def test_directional_value_wins_for_its_own_direction_only(
        self,
        direction: str,
        base: bool,
        long: bool | None,
        short: bool | None,
        want: bool,
    ) -> None:
        assert pick_volume_suppress(direction, base, long, short) is want


def _legacy_run_id(vs: bool, vsl: bool | None, vss: bool | None) -> str:
    """The pre-#970 key, spelled from scratch so only a constant pins the format."""
    key = "BTCUSDT|4h|bos|90|0.02|2.0|0.0|off|1|None"
    if vs:
        key += "|vol_suppress"
    if vsl:
        key += "|vol_sup_l"
    if vss:
        key += "|vol_sup_s"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _run_id(vs: bool, vsl: bool | None, vss: bool | None) -> str:
    return _backtest_run_id(
        "BTCUSDT",
        "4h",
        "bos",
        90,
        0.02,
        2.0,
        0.0,
        "off",
        1,
        None,
        volume_suppress=vs or None,
        volume_suppress_long=vsl,
        volume_suppress_short=vss,
    )


class TestRunIdAxis:
    @pytest.mark.parametrize(
        ("vs", "vsl", "vss"),
        [
            (vs, vsl, vss)
            for vs, vsl, vss in itertools.product(
                (False, True), (None, True, False), (None, True, False)
            )
            # Everything except a directional False under a strategy-wide True.
            if not (vs and (vsl is False or vss is False))
        ],
    )
    def test_configs_without_a_directional_off_keep_their_historical_id(
        self, vs: bool, vsl: bool | None, vss: bool | None
    ) -> None:
        assert _run_id(vs, vsl, vss) == _legacy_run_id(vs, vsl, vss)

    def test_a_directional_off_under_a_strategy_wide_on_is_its_own_book(self) -> None:
        """Per-tf False under vs=True trades that direction unsuppressed.

        Both that cell and the plain vs=True cell used to hash to ONE id, so the
        sweep that first honoured the per-tf table would have silently replaced
        the row measured without it (the ST86 failure).
        """
        plain = _run_id(True, None, None)
        assert _run_id(True, False, None) != plain
        assert _run_id(True, None, False) != plain
        assert _run_id(True, False, None) != _run_id(True, None, False)

    def test_off_is_not_distinguished_when_nothing_is_suppressed(self) -> None:
        assert _run_id(False, False, False) == _run_id(False, None, None)


def _seed(conn: duckdb.DuckDBPyConnection, tf: str, n: int = 20) -> None:
    base = 1_700_000_000_000
    upsert_ohlcv(
        conn,
        pd.DataFrame(
            {
                "symbol": ["BTCUSDT"] * n,
                "timeframe": [tf] * n,
                "open_time": [base + i * 3_600_000 for i in range(n)],
                "open": [100.0 + i for i in range(n)],
                "high": [105.0 + i for i in range(n)],
                "low": [95.0 + i for i in range(n)],
                "close": [101.0 + i for i in range(n)],
                "volume": [1_000.0] * n,
                "taker_buy_volume": [500.0] * n,
            }
        ),
        venue="binance",
    )


def _sweep(
    strategy_params: dict[str, StrategyOverride],
) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    """Run the sweep's detect+backtest+save loop over 1h and 4h for ``fvg``.

    Returns the volume_suppress kwargs handed to ``run_backtest`` per timeframe
    and the stored run_id per timeframe.
    """
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    for tf in ("1h", "4h"):
        _seed(conn, tf)
    cfg = BacktestSweepConfig(
        symbols=["BTCUSDT"],
        timeframes=["1h", "4h"],
        strategies=["fvg"],
        save_results=True,
        strategy_params=strategy_params,
    )
    signals = pd.DataFrame(
        {
            "open_time": [1_700_000_000_000],
            "direction": ["long"],
            "reason": ["fvg"],
            "sl_price": [99.0],
            "context": [None],
            "low_volume": [False],
            "tp_price": [103.0],
        }
    )

    def _fake(*args: object, **kwargs: object) -> BacktestResult:
        return BacktestResult(symbol="BTCUSDT", timeframe=str(args[3]), strategy="fvg")

    with (
        patch(
            "analytics.backtest_runner.detect_signals_for_strategy",
            return_value=signals,
        ),
        patch("analytics.backtest_runner.run_backtest", side_effect=_fake) as mock_bt,
    ):
        _collect_sweep_results(
            conn,
            cfg,
            cfg.tp_r,
            ["BTCUSDT"],
            ["fvg"],
            0,
            9_999_999_999_999,
            sweep_id="sweep-970",
        )
    kwargs_by_tf: dict[str, dict[str, object]] = {
        str(c.args[3]): {
            "volume_suppress": c.kwargs["volume_suppress"],
            "volume_suppress_long": c.kwargs["volume_suppress_long"],
            "volume_suppress_short": c.kwargs["volume_suppress_short"],
        }
        for c in mock_bt.call_args_list
    }
    rows = conn.execute("SELECT timeframe, run_id FROM backtest_runs").fetchall()
    return kwargs_by_tf, {str(tf): str(rid) for tf, rid in rows}


class TestSweepThreadsTheTimeframe:
    def test_run_backtest_receives_the_per_tf_value_and_the_row_id_follows(
        self,
    ) -> None:
        overridden = {
            "fvg": StrategyOverride(
                volume_suppress=True, volume_suppress_long_per_tf={"1h": False}
            )
        }
        plain = {"fvg": StrategyOverride(volume_suppress=True)}

        kw, ids = _sweep(overridden)
        kw_plain, ids_plain = _sweep(plain)

        # The sweep passes the cell's own timeframe to the resolver...
        assert kw["1h"]["volume_suppress_long"] is False
        assert kw["4h"]["volume_suppress_long"] is None
        assert kw_plain["1h"]["volume_suppress_long"] is None
        # ...and the saved row is namespaced by it: the overridden cell is a
        # different book, the untouched timeframe keeps its historical id.
        assert ids["1h"] != ids_plain["1h"]
        assert ids["4h"] == ids_plain["4h"]
