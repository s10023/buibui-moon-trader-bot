"""Tests for analytics/liquidations.py: gap rule, status line, batch reader (#984).

The load-bearing property is that a gap is a missing HEARTBEAT or a down connection and
NEVER message silence: a quiet market sends nothing. Mutation check: making `compute_gaps`
treat "no msg for >2min" as a gap fails `test_quiet_but_connected_hour_is_not_a_gap`.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import duckdb

from analytics import liquidations as liq

MIN = 60_000
T0 = 1_760_000_000_000  # an arbitrary epoch-ms anchor
DAY0 = liq.utc_day(T0)


def hb(ts: int) -> dict[str, Any]:
    return {"t": "heartbeat", "ts": ts, "msgs": 0}


def ev(kind: str, ts: int, **kw: Any) -> dict[str, Any]:
    return {"t": kind, "ts": ts, **kw}


def heartbeats(start: int, minutes: int) -> list[dict[str, Any]]:
    return [hb(start + i * MIN) for i in range(minutes + 1)]


def force_order(symbol: str = "BTCUSDT", *, wrapped: bool = True) -> dict[str, Any]:
    payload = {
        "e": "forceOrder",
        "E": 1_760_000_000_500,
        "o": {
            "s": symbol,
            "S": "SELL",
            "o": "LIMIT",
            "f": "IOC",
            "q": "0.014",
            "p": "9910.26",
            "ap": "9910.26",
            "X": "FILLED",
            "l": "0.014",
            "z": "0.014",
            "T": 1_760_000_000_400,
        },
    }
    return {"stream": "!forceOrder@arr", "data": payload} if wrapped else payload


def write_day(
    directory: Path, day: str, records: list[dict[str, Any]], *, gz: bool = False
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)
    if gz:
        path = directory / f"{day}.jsonl.gz"
        path.write_bytes(gzip.compress(text.encode()))
    else:
        path = directory / f"{day}.jsonl"
        path.write_text(text, encoding="utf-8")
    return path


class TestGapRule:
    def test_quiet_but_connected_hour_is_not_a_gap(self) -> None:
        """No messages at all for an hour, heartbeats every minute -> zero gaps."""
        events = [ev("connect", T0), *heartbeats(T0, 60)]
        assert liq.compute_gaps(events, now_ms=T0 + 60 * MIN) == []

    def test_missing_heartbeat_window_is_a_gap(self) -> None:
        events = [ev("connect", T0), *heartbeats(T0, 2), hb(T0 + 12 * MIN)]
        gaps = liq.compute_gaps(events, now_ms=T0 + 12 * MIN)
        assert [(g.start_ms, g.end_ms, g.cause) for g in gaps] == [
            (T0 + 2 * MIN, T0 + 12 * MIN, "no_heartbeat")
        ]
        assert gaps[0].minutes == 10

    def test_threshold_is_strictly_greater_than_two_minutes(self) -> None:
        on_edge = [hb(T0), hb(T0 + liq.GAP_THRESHOLD_S * 1000)]
        over = [hb(T0), hb(T0 + liq.GAP_THRESHOLD_S * 1000 + 1)]
        assert liq.compute_gaps(on_edge, now_ms=T0 + 2 * MIN) == []
        assert len(liq.compute_gaps(over, now_ms=T0 + 3 * MIN)) == 1

    def test_one_late_heartbeat_is_tolerated(self) -> None:
        events = [hb(T0), hb(T0 + 90_000), hb(T0 + 150_000)]
        assert liq.compute_gaps(events, now_ms=T0 + 150_000) == []

    def test_messages_never_fill_or_create_gaps(self) -> None:
        """A message inside a heartbeat hole does not close it; its absence opens none."""
        events = [
            hb(T0),
            {"t": "msg", "ts": T0 + 5 * MIN, "msg": {}},
            hb(T0 + 10 * MIN),
        ]
        assert len(liq.compute_gaps(events, now_ms=T0 + 10 * MIN)) == 1

    def test_disconnect_to_reconnect_is_a_gap_even_if_heartbeats_straddle_it(
        self,
    ) -> None:
        events = [
            ev("connect", T0),
            hb(T0),
            ev("disconnect", T0 + 30_000, reason="x"),
            ev("reconnect", T0 + 40_000),
            hb(T0 + 40_000),
        ]
        gaps = liq.compute_gaps(events, now_ms=T0 + 40_000)
        assert [(g.start_ms, g.end_ms, g.cause) for g in gaps] == [
            (T0 + 30_000, T0 + 40_000, "disconnected")
        ]

    def test_connect_without_disconnect_counts_from_last_sign_of_life(self) -> None:
        """Killed process: no disconnect was written, the restart's connect closes it."""
        events = [ev("connect", T0), hb(T0), hb(T0 + MIN), ev("connect", T0 + 100_000)]
        gaps = liq.compute_gaps(events, now_ms=T0 + 100_000)
        assert [(g.start_ms, g.end_ms, g.cause) for g in gaps] == [
            (T0 + MIN, T0 + 100_000, "restart")
        ]

    def test_trailing_gap_when_the_recorder_is_dead_now(self) -> None:
        events = [ev("connect", T0), *heartbeats(T0, 3)]
        gaps = liq.compute_gaps(events, now_ms=T0 + 30 * MIN)
        assert [(g.start_ms, g.end_ms) for g in gaps] == [(T0 + 3 * MIN, T0 + 30 * MIN)]

    def test_open_disconnect_runs_to_now(self) -> None:
        """Inside the heartbeat threshold, only the down connection makes the gap."""
        events = [ev("connect", T0), hb(T0), ev("disconnect", T0 + 10_000, reason="r")]
        gaps = liq.compute_gaps(events, now_ms=T0 + 90_000)
        assert [(g.start_ms, g.end_ms, g.cause) for g in gaps] == [
            (T0 + 10_000, T0 + 90_000, "disconnected")
        ]

    def test_overlapping_windows_merge(self) -> None:
        events = [
            hb(T0),
            ev("disconnect", T0 + MIN, reason="r"),
            ev("reconnect", T0 + 9 * MIN),
            hb(T0 + 10 * MIN),
        ]
        gaps = liq.compute_gaps(events, now_ms=T0 + 10 * MIN)
        assert len(gaps) == 1
        assert gaps[0].start_ms == T0 and gaps[0].end_ms == T0 + 10 * MIN
        assert set(gaps[0].cause.split("+")) == {"no_heartbeat", "disconnected"}

    def test_window_clips_and_keeps_state_from_before_it(self) -> None:
        events = [
            hb(T0),
            ev("disconnect", T0 + MIN, reason="r"),
            ev("reconnect", T0 + 20 * MIN),
        ]
        gaps = liq.compute_gaps(
            events, now_ms=T0 + 20 * MIN, window=(T0 + 10 * MIN, T0 + 20 * MIN)
        )
        assert [(g.start_ms, g.end_ms) for g in gaps] == [
            (T0 + 10 * MIN, T0 + 20 * MIN)
        ]

    def test_no_phantom_gap_before_recording_began(self) -> None:
        events = [ev("connect", T0 + 5 * MIN), *heartbeats(T0 + 5 * MIN, 5)]
        gaps = liq.compute_gaps(
            events, now_ms=T0 + 10 * MIN, window=(T0 - 60 * MIN, T0 + 10 * MIN)
        )
        assert gaps == []


class TestStatus:
    def test_healthy_recorder(self, tmp_path: Path) -> None:
        now = T0 + 60 * MIN
        write_day(tmp_path, DAY0, [ev("connect", T0), *heartbeats(T0, 60)])
        s = liq.recorder_status(tmp_path, now=now + 20_000)
        assert s.ok and s.gap_minutes == 0
        assert s.last_heartbeat_age_s == 20.0
        assert "ok" in s.line and "20s ago" in s.line

    def test_quiet_market_day_is_healthy(self, tmp_path: Path) -> None:
        """Zero messages all day is not a failure."""
        write_day(tmp_path, DAY0, [ev("connect", T0), *heartbeats(T0, 120)])
        assert liq.recorder_status(tmp_path, now=T0 + 120 * MIN).ok

    def test_connected_but_receiving_no_frames_for_hours_is_a_dead_subscription(
        self, tmp_path: Path
    ) -> None:
        """The routing trap: the legacy URL connects and heartbeats but delivers nothing.
        Separate from the gap rule -- gap minutes stay 0 -- and only after 3h."""
        write_day(tmp_path, DAY0, [ev("connect", T0), *heartbeats(T0, 240)])
        s = liq.recorder_status(tmp_path, now=T0 + 240 * MIN)
        assert s.gap_minutes == 0 and s.silent and not s.ok
        assert "NO FRAMES" in s.line

    def test_hours_with_frames_are_not_silent(self, tmp_path: Path) -> None:
        recs = [ev("connect", T0), *heartbeats(T0, 240)]
        recs[-30] = {**recs[-30], "msgs": 3}  # one busy minute inside the last 3h
        write_day(tmp_path, DAY0, recs)
        s = liq.recorder_status(tmp_path, now=T0 + 240 * MIN)
        assert s.ok and not s.silent and s.messages == 3

    def test_a_short_silence_is_a_quiet_market_not_an_alarm(
        self, tmp_path: Path
    ) -> None:
        write_day(tmp_path, DAY0, [ev("connect", T0), *heartbeats(T0, 150)])
        assert liq.recorder_status(tmp_path, now=T0 + 150 * MIN).ok

    def test_stale_heartbeat_is_red(self, tmp_path: Path) -> None:
        write_day(tmp_path, DAY0, [ev("connect", T0), *heartbeats(T0, 5)])
        s = liq.recorder_status(tmp_path, now=T0 + 45 * MIN)
        assert not s.ok and s.gap_minutes == 40.0

    def test_gap_budget_reds_a_recovered_recorder(self, tmp_path: Path) -> None:
        recs = [ev("connect", T0), *heartbeats(T0, 2), *heartbeats(T0 + 122 * MIN, 5)]
        write_day(tmp_path, DAY0, recs)
        s = liq.recorder_status(tmp_path, now=T0 + 127 * MIN)
        assert s.gap_minutes == 120.0 and not s.ok
        assert liq.recorder_status(tmp_path, now=T0 + 127 * MIN, max_gap_minutes=200).ok

    def test_no_files_is_red_not_a_crash(self, tmp_path: Path) -> None:
        s = liq.recorder_status(tmp_path / "missing", now=T0)
        assert not s.ok and s.last_heartbeat_age_s is None
        assert "NO HEARTBEAT" in s.line

    def test_state_carries_across_a_gzipped_day_boundary(self, tmp_path: Path) -> None:
        prev = liq.utc_day(T0 - 86_400_000)
        write_day(
            tmp_path, prev, [ev("connect", T0 - 10 * MIN), hb(T0 - 10 * MIN)], gz=True
        )
        write_day(tmp_path, DAY0, [ev("connect", T0 + 4 * MIN), hb(T0 + 4 * MIN)])
        s = liq.recorder_status(tmp_path, now=T0 + 4 * MIN)
        assert s.gap_minutes == 14.0  # last sign of life -> restart connect

    def test_daily_check_line_never_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "x"
        bad.write_text("not a directory")
        ok, text = liq.daily_check_line(bad)
        assert ok is False and text.startswith("liq-recorder")

    def test_data_dir_env_override(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setenv(liq.DATA_DIR_ENV, str(tmp_path))
        assert liq.data_dir() == tmp_path
        assert liq.data_dir("elsewhere") == Path("elsewhere")


class TestFiles:
    def test_gz_wins_over_jsonl_for_the_same_day(self, tmp_path: Path) -> None:
        write_day(tmp_path, DAY0, [hb(T0)], gz=True)
        write_day(tmp_path, DAY0, [hb(T0), hb(T0 + MIN)])
        assert [p.name for p in liq.day_files(tmp_path)] == [f"{DAY0}.jsonl.gz"]
        assert len(list(liq.iter_records(tmp_path))) == 1

    def test_truncated_last_line_is_skipped(self, tmp_path: Path) -> None:
        p = write_day(tmp_path, DAY0, [hb(T0), hb(T0 + MIN)])
        with p.open("a", encoding="utf-8") as fh:
            fh.write('{"t":"heartbeat","ts":17600')  # crash mid-write
        assert len(list(liq.iter_records(tmp_path))) == 2

    def test_non_day_files_are_ignored(self, tmp_path: Path) -> None:
        write_day(tmp_path, DAY0, [hb(T0)])
        (tmp_path / "README.md").write_text("x")
        (tmp_path / f"{DAY0}.jsonl.gz.tmp").write_bytes(b"partial")
        assert len(liq.day_files(tmp_path)) == 1


class TestBatchReader:
    def _populate(self, tmp_path: Path) -> None:
        recs: list[dict[str, Any]] = [
            ev("connect", T0),
            hb(T0),
            {"t": "msg", "ts": T0 + 10_000, "msg": force_order("BTCUSDT")},  # covered
            hb(T0 + MIN),
            # heartbeats stop, a liquidation arrives while the recorder cannot vouch
            {"t": "msg", "ts": T0 + 5 * MIN, "msg": force_order("ETHUSDT")},
            hb(T0 + 10 * MIN),
            {
                "t": "msg",
                "ts": T0 + 10 * MIN + 5_000,
                "msg": force_order("SOLUSDT", wrapped=False),
            },
            {"t": "msg", "ts": T0 + 10 * MIN + 6_000, "raw": "not json"},
            hb(T0 + 11 * MIN),
        ]
        write_day(tmp_path, DAY0, recs)

    def test_orders_are_flattened_and_gap_rows_flagged(self, tmp_path: Path) -> None:
        self._populate(tmp_path)
        df = liq.load_orders(tmp_path, now=T0 + 11 * MIN)
        assert list(df["symbol"]) == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        assert list(df["in_gap"]) == [False, True, False]
        row = df.iloc[0]
        assert row["side"] == "SELL" and row["price"] == 9910.26 and row["qty"] == 0.014
        assert row["stream"] == "!forceOrder@arr"
        assert df.iloc[2]["stream"] is None or df.iloc[2]["stream"] != "!forceOrder@arr"

    def test_exclude_gaps_drops_flagged_rows(self, tmp_path: Path) -> None:
        self._populate(tmp_path)
        df = liq.load_orders(tmp_path, now=T0 + 11 * MIN, exclude_gaps=True)
        assert list(df["symbol"]) == ["BTCUSDT", "SOLUSDT"]

    def test_gap_windows_are_exposed(self, tmp_path: Path) -> None:
        self._populate(tmp_path)
        gaps = liq.load_gaps(tmp_path, now=T0 + 11 * MIN)
        assert list(gaps["start_ms"]) == [T0 + MIN] and list(gaps["end_ms"]) == [
            T0 + 10 * MIN
        ]
        assert list(gaps["minutes"]) == [9.0]

    def test_duckdb_relations(self, tmp_path: Path) -> None:
        self._populate(tmp_path)
        conn = duckdb.connect(":memory:")
        liq.register_duckdb(conn, tmp_path, now=T0 + 11 * MIN)
        n_clean = conn.execute(
            "SELECT count(*) FROM liq_orders WHERE NOT in_gap"
        ).fetchone()
        assert n_clean == (2,)
        assert conn.execute("SELECT count(*) FROM liq_gaps").fetchone() == (1,)

    def test_empty_directory_loads_empty(self, tmp_path: Path) -> None:
        assert len(liq.load_orders(tmp_path)) == 0
        assert len(liq.load_gaps(tmp_path)) == 0


def test_stream_list_has_exactly_one_entry() -> None:
    """A second stream is added only when an Issue names it."""
    assert liq.STREAMS == ("!forceOrder@arr",)
    # Legacy /ws and /stream connect but push nothing (measured 2026-10-09): /market only.
    assert (
        liq.stream_url()
        == "wss://fstream.binance.com/market/stream?streams=!forceOrder@arr"
    )
