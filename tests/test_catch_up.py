"""Missed-candle catch-up (SoT N6) + its watermark-on-send prereq.

The scanner fires on the latest closed candle only, so any cycle that does not
run permanently loses that candle's signals — the live ledger is the OOS
evidence base, so those gaps are not merely missing rows, they are a biased
sample (measured 2026-07-23: the hourly GH-Actions cron delivered 35%, and its
run-hour distribution was non-uniform at p<0.01).

Ported from wifey #68 (watermark-on-send) + #69 (--catch-up), with one
deliberate divergence: wifey's rule is "never consume a candle you did not
dispatch". Backfilled candles are *deliberately* not dispatched (a 1h signal
surfaced days late is not tradeable) yet must still be recorded, so the rule
here is mode-aware rather than global.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import duckdb
import pandas as pd

from analytics.signal.scanner import run_scan_cycle, scan_symbol
from analytics.store import init_schema
from analytics.store.signals import get_signals_history
from signals.cooldown_store import CooldownStore

_WEDNESDAY_MS = 1704240000000


def _make_ohlcv(open_time_ms: int) -> pd.DataFrame:
    """4 rows; the second-to-last is the latest *closed* candle, last is forming."""
    return pd.DataFrame(
        [
            {
                "open_time": open_time_ms - 2000,
                "open": 100.0,
                "high": 105.0,
                "low": 98.0,
                "close": 102.0,
                "volume": 1.0,
            },
            {
                "open_time": open_time_ms - 1000,
                "open": 102.0,
                "high": 106.0,
                "low": 100.0,
                "close": 103.0,
                "volume": 1.0,
            },
            {
                "open_time": open_time_ms,
                "open": 103.0,
                "high": 107.0,
                "low": 101.0,
                "close": 104.0,
                "volume": 1.0,
            },
            {
                "open_time": open_time_ms + 1000,
                "open": 104.0,
                "high": 104.5,
                "low": 103.5,
                "close": 104.2,
                "volume": 0.1,
            },
        ]
    )


def _make_signals_df(open_time_ms: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "open_time": open_time_ms,
                "direction": "long",
                "reason": "fvg_long@100.00-102.00",
                "sl_price": 98.0,
                "context": "",
            }
        ]
    )


def _registry_patches(signals_df: pd.DataFrame, ohlcv: pd.DataFrame) -> list[Any]:
    return [
        patch("analytics.signal.scanner.get_ohlcv", return_value=ohlcv),
        patch(
            "analytics.signal.scanner.get_funding_rates",
            return_value=pd.DataFrame(),
        ),
        patch(
            "analytics.signal.scanner.SIGNAL_REGISTRY",
            {"fvg": {"detector": lambda df: signals_df, "confidence": 4}},
        ),
        patch(
            "analytics.signal.scanner.STRATEGY_REGISTRY",
            {
                "fvg": type(
                    "S",
                    (),
                    {
                        "requires_funding": False,
                        "requires_secondary": False,
                        "get_confidence": lambda self, tf: 3,
                    },
                )(),
            },
        ),
    ]


class TestWatermarkOnSend:
    """wifey #68 prereq — a run that sends nothing must not consume the candle."""

    def test_non_sending_run_does_not_consume_the_candle_watermark(
        self, tmp_path: Any
    ) -> None:
        """A scan with send_telegram=False must leave the candle alertable.

        Otherwise a dry/smoke run silently eats the candle and the next real
        run dedups the alert away — the signal is lost permanently.
        """
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))

        ohlcv = _make_ohlcv(_WEDNESDAY_MS)
        signals_df = _make_signals_df(_WEDNESDAY_MS)

        patches = _registry_patches(signals_df, ohlcv)
        for p in patches:
            p.start()
        try:
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                send_telegram=False,
            )
        finally:
            for p in patches:
                p.stop()

        assert store.is_new_candle("BTCUSDT", "4h", "fvg", _WEDNESDAY_MS), (
            "a non-sending run consumed the watermark; the next run with "
            "--telegram would silently skip this candle"
        )

    def test_successful_send_consumes_the_candle_watermark(self, tmp_path: Any) -> None:
        """Regression guard, not TDD-driven: dedup must survive the #68 move.

        Written after the fix because nothing in the suite covered this arm —
        `test_cooldown_store.py` exercises the primitive, but no test asserted
        that `run_scan_cycle` stamps the watermark at all. Without this, a
        future refactor could drop marking entirely and every cycle would
        re-alert the same candle with the suite still green.
        """
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))

        patches = _registry_patches(
            _make_signals_df(_WEDNESDAY_MS), _make_ohlcv(_WEDNESDAY_MS)
        )
        patches.append(patch("utils.telegram.send_telegram_message"))
        for p in patches:
            p.start()
        try:
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                send_telegram=True,
            )
        finally:
            for p in patches:
                p.stop()

        assert not store.is_new_candle("BTCUSDT", "4h", "fvg", _WEDNESDAY_MS), (
            "a sent alert did not consume the watermark — the next cycle "
            "would re-alert the same candle"
        )

    def test_failed_send_does_not_consume_the_candle_watermark(
        self, tmp_path: Any
    ) -> None:
        """A Telegram failure must leave the candle alertable on the next run."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))

        patches = _registry_patches(
            _make_signals_df(_WEDNESDAY_MS), _make_ohlcv(_WEDNESDAY_MS)
        )
        patches.append(
            patch(
                "utils.telegram.send_telegram_message",
                side_effect=RuntimeError("telegram down"),
            )
        )
        for p in patches:
            p.start()
        try:
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                send_telegram=True,
            )
        finally:
            for p in patches:
                p.stop()

        assert store.is_new_candle("BTCUSDT", "4h", "fvg", _WEDNESDAY_MS), (
            "a failed send consumed the watermark — the alert is lost"
        )


class TestLastMarked:
    """`last_marked` backs the cold-start guard.

    Catch-up must distinguish "this key has a watermark, replay from it" from
    "this key has never fired, do NOT treat 200 window candles as missed" —
    `is_new_candle` collapses both to True and cannot make that call.
    """

    def test_returns_none_when_never_marked(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        assert store.last_marked("BTCUSDT", "1h", "fvg") is None

    def test_returns_the_marked_open_time(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        assert store.last_marked("BTCUSDT", "1h", "fvg") == 1_000

    def test_is_scoped_per_symbol_timeframe_and_strategy(self, tmp_path: Any) -> None:
        """A watermark on one key must not make a sibling key look warm.

        If it leaked, the first-ever fire of a new strategy would replay the
        whole window instead of just the latest candle.
        """
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        assert store.last_marked("ETHUSDT", "1h", "fvg") is None
        assert store.last_marked("BTCUSDT", "4h", "fvg") is None
        assert store.last_marked("BTCUSDT", "1h", "bos") is None


def _multi_candle_signals(open_time_ms: int) -> pd.DataFrame:
    """Signals on the two most recent CLOSED candles, plus the forming one.

    `_make_ohlcv` lays out [t-2000, t-1000, t, t+1000]; the last is forming, so
    t-1000 and t are the replayable pair and t+1000 must never be emitted.
    """
    return pd.DataFrame(
        [
            {
                "open_time": open_time_ms - 1000,
                "direction": "long",
                "reason": "fvg_long@older",
                "sl_price": 98.0,
                "context": "",
            },
            {
                "open_time": open_time_ms,
                "direction": "long",
                "reason": "fvg_long@latest",
                "sl_price": 98.0,
                "context": "",
            },
            {
                "open_time": open_time_ms + 1000,
                "direction": "long",
                "reason": "fvg_long@forming",
                "sl_price": 98.0,
                "context": "",
            },
        ]
    )


class TestScanSymbolCatchUp:
    """`scan_symbol(catch_up=True)` replays every un-alerted closed candle."""

    def _scan(self, *, catch_up: bool) -> list[Any]:
        patches = _registry_patches(
            _multi_candle_signals(_WEDNESDAY_MS), _make_ohlcv(_WEDNESDAY_MS)
        )
        for p in patches:
            p.start()
        try:
            kwargs: dict[str, Any] = {"catch_up": True} if catch_up else {}
            return scan_symbol(
                ohlcv_df=_make_ohlcv(_WEDNESDAY_MS),
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                **kwargs,
            )
        finally:
            for p in patches:
                p.stop()

    def test_default_emits_only_the_latest_closed_candle(self) -> None:
        """Default path unchanged — this is what makes the port byte-identical."""
        events = self._scan(catch_up=False)
        assert [e.open_time for e in events] == [_WEDNESDAY_MS]

    def test_catch_up_emits_every_closed_candle(self) -> None:
        events = self._scan(catch_up=True)
        assert [e.open_time for e in events] == [
            _WEDNESDAY_MS - 1000,
            _WEDNESDAY_MS,
        ]

    def test_catch_up_prices_each_event_at_its_own_candle_close(self) -> None:
        """A replayed candle must carry ITS close, not the latest one.

        Pricing every backfilled event at the newest close would silently
        rewrite history — entry prices, and therefore every resolved
        outcome_r computed from them, would be wrong.
        """
        events = self._scan(catch_up=True)
        assert [e.price for e in events] == [103.0, 104.0]

    def test_catch_up_still_excludes_the_forming_candle(self) -> None:
        """The forming bar stays excluded — it has seconds of data, not a bar."""
        events = self._scan(catch_up=True)
        assert all(e.open_time <= _WEDNESDAY_MS for e in events)


class TestRunScanCycleCatchUp:
    """Wiring: which candles get replayed, and which ones alert."""

    def _run(
        self,
        tmp_path: Any,
        *,
        catch_up: bool,
        premark: int | None = None,
    ) -> tuple[Any, Any]:
        """Returns (persisted signals df, telegram mock)."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        if premark is not None:
            store.mark_candle("BTCUSDT", "4h", "fvg", premark)

        patches = _registry_patches(
            _multi_candle_signals(_WEDNESDAY_MS), _make_ohlcv(_WEDNESDAY_MS)
        )
        tg = patch("utils.telegram.send_telegram_message")
        patches.append(tg)
        started = [p.start() for p in patches]
        tg_mock = started[-1]
        try:
            kwargs: dict[str, Any] = {"catch_up": True} if catch_up else {}
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                send_telegram=True,
                **kwargs,
            )
        finally:
            for p in patches:
                p.stop()

        rows = get_signals_history(
            conn, "BTCUSDT", "4h", _WEDNESDAY_MS - 10_000, _WEDNESDAY_MS + 10_000
        )
        return rows, tg_mock

    def test_cold_start_records_only_the_latest_candle(self, tmp_path: Any) -> None:
        """A fresh state file must NOT treat the whole window as missed.

        Without this guard the first run after any state reset would burst
        every candle in the 200-bar scan window into the ledger and Telegram.
        """
        rows, _ = self._run(tmp_path, catch_up=True, premark=None)
        assert sorted(rows["open_time"].tolist()) == [_WEDNESDAY_MS]

    def test_warm_watermark_replays_the_missed_candle(self, tmp_path: Any) -> None:
        """With a prior watermark, un-alerted closed candles are recovered."""
        rows, _ = self._run(tmp_path, catch_up=True, premark=_WEDNESDAY_MS - 2000)
        assert sorted(rows["open_time"].tolist()) == [
            _WEDNESDAY_MS - 1000,
            _WEDNESDAY_MS,
        ]

    def test_backfilled_candle_is_recorded_but_not_alerted(self, tmp_path: Any) -> None:
        """Only the newest candle is tradeable, so only it may reach Telegram.

        A 1h signal surfaced hours late is noise in the chat but real evidence
        in the ledger — that asymmetry is the whole point of catch-up.
        """
        _, tg_mock = self._run(tmp_path, catch_up=True, premark=_WEDNESDAY_MS - 2000)
        assert tg_mock.call_count == 1, (
            "backfilled candles must not fire Telegram; only the latest "
            f"closed candle may alert (got {tg_mock.call_count} sends)"
        )

    def test_default_path_records_only_the_latest_candle(self, tmp_path: Any) -> None:
        """catch_up=False must stay byte-identical to the pre-N6 behaviour."""
        rows, _ = self._run(tmp_path, catch_up=False, premark=_WEDNESDAY_MS - 2000)
        assert sorted(rows["open_time"].tolist()) == [_WEDNESDAY_MS]


class TestRunnerPassesCatchUp:
    """The daemon must actually forward the flag — plumbing, but load-bearing.

    A silently-dropped kwarg here would leave `--catch-up` a no-op that still
    looks like it worked, which is worse than not shipping it at all.
    """

    def _call_kwargs(self, tmp_path: Any, *, catch_up: bool | None) -> dict[str, Any]:
        """Drive one daemon cycle with every external touchpoint stubbed.

        `run_signal_watch` reaches for five things before it ever calls
        `run_scan_cycle`, and each one has to be neutralised:
        `create_data_client()` (reads BINANCE_API_KEY from .env),
        `load_coins_config()` (reads gitignored config/coins.json), the OHLCV
        sync/backfill pair, the real `analytics.db`, and the real
        `signal_state.json`. The last two are the dangerous ones — the default
        args point at live files, so this test wrote to the operator's actual
        DB and cooldown state until it was pinned to tmp_path. CI (no .env, no
        coins.json) is the honest environment; a local .env hid all of it.
        """
        from analytics import signal_runner

        kwargs: dict[str, Any] = {} if catch_up is None else {"catch_up": catch_up}
        with (
            patch("analytics.signal_runner.run_scan_cycle", return_value=[]) as rsc,
            patch("analytics.signal_runner.get_ohlcv", return_value=pd.DataFrame()),
            patch(
                "analytics.signal_runner.create_data_client",
                return_value=MagicMock(),
            ),
            patch("analytics.signal_runner.load_coins_config", return_value={}),
            patch("analytics.signal_runner.sync"),
            patch("analytics.signal_runner.backfill"),
            patch("analytics.signal_runner.backfill_outcomes", return_value=0),
        ):
            signal_runner.run_signal_watch(
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                max_cycles=1,
                state_file=str(tmp_path / "state.json"),
                db_path=tmp_path / "test.db",
                **kwargs,
            )
        assert rsc.call_count == 1
        return dict(rsc.call_args.kwargs)

    def test_catch_up_true_reaches_run_scan_cycle(self, tmp_path: Any) -> None:
        assert self._call_kwargs(tmp_path, catch_up=True).get("catch_up") is True

    def test_default_is_off(self, tmp_path: Any) -> None:
        assert self._call_kwargs(tmp_path, catch_up=None).get("catch_up") is False
