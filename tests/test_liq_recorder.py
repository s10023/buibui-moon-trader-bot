"""Tests for monitor/liq_recorder.py (#984). No network: the socket and clock are injected."""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import subprocess
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from analytics import liquidations as liq
from monitor import liq_recorder as lr

DAY1 = 1_760_000_000_000  # 2025-10-09T08:53:20Z
DAY1_END = 1_760_054_400_000  # 2025-10-10T00:00:00Z
NEXT_DAY = DAY1_END + 5_000


def read_all(directory: Path, *, messages: bool = True) -> list[dict[str, Any]]:
    return list(liq.iter_records(directory, include_messages=messages))


def kinds(directory: Path) -> list[str]:
    return [r["t"] for r in read_all(directory)]


class FakeClosed(Exception):
    """Stands in for websockets' ConnectionClosed."""


class FakeSocket:
    """Script items: str/bytes -> a frame, int -> set the clock, Exception -> raise.

    When the script is exhausted the socket either keeps blocking (a QUIET market) or
    raises FakeClosed, depending on ``then``.
    """

    def __init__(self, script: list[Any], clock: dict[str, int], then: str) -> None:
        self.script, self.clock, self.then = list(script), clock, then

    async def recv(self) -> str | bytes:
        while True:
            if not self.script:
                if self.then == "close":
                    raise FakeClosed("1006 abnormal closure")
                await asyncio.sleep(3600)
            item = self.script.pop(0)
            if isinstance(item, int):
                self.clock["t"] = item
                continue
            if isinstance(item, Exception):
                raise item
            await asyncio.sleep(0)  # a real recv yields to the loop
            return item  # type: ignore[no-any-return]


class FakeNet:
    """One entry per connection attempt: a script list, or an Exception to fail the attempt."""

    def __init__(
        self, sessions: list[Any], clock: dict[str, int], then: str = "close"
    ) -> None:
        self.sessions, self.clock, self.then = list(sessions), clock, then
        self.attempts = 0
        self.urls: list[str] = []

    def __call__(self, url: str) -> AbstractAsyncContextManager[FakeSocket]:
        self.urls.append(url)
        self.attempts += 1
        plan = self.sessions.pop(0) if self.sessions else []

        @asynccontextmanager
        async def ctx() -> Any:
            if isinstance(plan, Exception):
                raise plan
            yield FakeSocket(plan, self.clock, self.then)

        return ctx()


def make(tmp_path: Path, net: FakeNet, clock: dict[str, int], **kw: Any) -> lr.Recorder:
    defaults: dict[str, Any] = {
        "connector": net,
        "clock_ms": lambda: clock["t"],
        "heartbeat_interval_s": 0.01,
        "backoff_base_s": 0.001,
        "backoff_cap_s": 0.004,
        "stop_poll_s": 0.01,
    }
    return lr.Recorder(tmp_path, **{**defaults, **kw})


def run(rec: lr.Recorder, *, until: Callable[[], bool], timeout: float = 5.0) -> None:
    """Run the recorder until `until()` holds, then stop it cleanly."""

    async def main() -> None:
        stop = asyncio.Event()

        async def stopper() -> None:
            end = asyncio.get_running_loop().time() + timeout
            while not until() and asyncio.get_running_loop().time() < end:
                await asyncio.sleep(0.005)
            stop.set()

        await asyncio.gather(rec.run(stop), stopper())

    asyncio.run(main())


class TestDayWriter:
    def test_rotation_gzips_the_finished_day(self, tmp_path: Path) -> None:
        w = lr.DayWriter(tmp_path)
        w.write({"t": "heartbeat", "ts": DAY1})
        w.write({"t": "heartbeat", "ts": DAY1 + 1000})
        assert (tmp_path / f"{liq.utc_day(DAY1)}.jsonl").exists()

        w.write({"t": "heartbeat", "ts": NEXT_DAY})
        w.close()

        day1, day2 = liq.utc_day(DAY1), liq.utc_day(NEXT_DAY)
        assert day1 != day2
        assert not (tmp_path / f"{day1}.jsonl").exists()
        with gzip.open(tmp_path / f"{day1}.jsonl.gz", "rt", encoding="utf-8") as fh:
            assert [json.loads(line)["ts"] for line in fh] == [DAY1, DAY1 + 1000]
        assert (tmp_path / f"{day2}.jsonl").read_text(encoding="utf-8").count("\n") == 1
        assert not list(tmp_path.glob("*.tmp"))

    def test_reader_sees_a_rotated_and_a_live_day_together(
        self, tmp_path: Path
    ) -> None:
        w = lr.DayWriter(tmp_path)
        for ts in (DAY1, NEXT_DAY):
            w.write({"t": "heartbeat", "ts": ts})
        w.close()
        assert [r["ts"] for r in read_all(tmp_path)] == [DAY1, NEXT_DAY]

    def test_clock_stepping_backwards_never_reopens_a_gzipped_day(
        self, tmp_path: Path
    ) -> None:
        w = lr.DayWriter(tmp_path)
        w.write({"t": "heartbeat", "ts": NEXT_DAY})
        w.write({"t": "heartbeat", "ts": DAY1})  # NTP step into the past
        w.close()
        assert [p.name for p in tmp_path.iterdir()] == [
            f"{liq.utc_day(NEXT_DAY)}.jsonl"
        ]

    def test_startup_gzips_a_previous_runs_unrotated_file(self, tmp_path: Path) -> None:
        old = tmp_path / f"{liq.utc_day(DAY1)}.jsonl"
        old.write_text('{"t":"heartbeat","ts":1}\n', encoding="utf-8")
        lr.DayWriter(tmp_path).compress_finished(liq.utc_day(NEXT_DAY))
        assert not old.exists() and old.with_name(old.name + ".gz").exists()

    def test_startup_drops_a_jsonl_left_beside_its_complete_gz(
        self, tmp_path: Path
    ) -> None:
        day = liq.utc_day(DAY1)
        (tmp_path / f"{day}.jsonl.gz").write_bytes(
            gzip.compress(b'{"t":"heartbeat","ts":1}\n')
        )
        (tmp_path / f"{day}.jsonl").write_text(
            '{"t":"heartbeat","ts":1}\n', encoding="utf-8"
        )
        lr.DayWriter(tmp_path).compress_finished(liq.utc_day(NEXT_DAY))
        assert [p.name for p in tmp_path.iterdir()] == [f"{day}.jsonl.gz"]


class TestRecorder:
    def test_frames_are_appended_raw_with_a_receive_timestamp(
        self, tmp_path: Path
    ) -> None:
        clock = {"t": DAY1}
        frame = (
            '{"stream":"!forceOrder@arr","data":{"e":"forceOrder","o":{"s":"BTCUSDT"}}}'
        )
        net = FakeNet([[frame, b'{"x":1}', "not json"]], clock)
        rec = make(tmp_path, net, clock)
        run(rec, until=lambda: rec.n_messages >= 3)

        msgs = [r for r in read_all(tmp_path) if r["t"] == "msg"]
        assert msgs[0]["msg"]["data"]["o"]["s"] == "BTCUSDT" and msgs[0]["ts"] == DAY1
        assert msgs[1]["msg"] == {"x": 1}  # bytes frames decode
        assert msgs[2] == {"t": "msg", "ts": DAY1, "raw": "not json"}
        assert net.urls[0] == liq.stream_url()

    def test_event_sequence_across_a_dropped_connection(self, tmp_path: Path) -> None:
        clock = {"t": DAY1}
        net = FakeNet([['{"a":1}'], ['{"b":2}']], clock, then="close")
        rec = make(tmp_path, net, clock)
        run(rec, until=lambda: net.attempts >= 3)

        seq = [k for k in kinds(tmp_path) if k != "heartbeat"]
        assert seq[:5] == ["connect", "msg", "disconnect", "reconnect", "msg"]
        dis = next(r for r in read_all(tmp_path) if r["t"] == "disconnect")
        assert "FakeClosed" in dis["reason"]

    def test_backoff_is_exponential_and_capped(self, tmp_path: Path) -> None:
        clock = {"t": DAY1}
        boom = OSError("refused")
        net = FakeNet([boom, boom, boom, boom, boom, []], clock, then="block")
        rec = make(tmp_path, net, clock)
        run(rec, until=lambda: net.attempts >= 6 and "connect" in kinds(tmp_path))

        assert rec.backoffs[:5] == [0.001, 0.002, 0.004, 0.004, 0.004]
        errs = [r for r in read_all(tmp_path) if r["t"] == "connect_error"]
        assert len(errs) == 5 and "refused" in errs[0]["reason"]

    def test_quiet_connected_market_writes_heartbeats_and_no_gap(
        self, tmp_path: Path
    ) -> None:
        """A socket that never delivers a frame still heartbeats; that is not a gap."""
        real = {"t": DAY1}
        net = FakeNet([[]], real, then="block")
        t0 = {"n": 0}

        def clk() -> int:
            t0["n"] += 1000
            return DAY1 + t0["n"]

        rec = make(tmp_path, net, real, clock_ms=clk)
        run(rec, until=lambda: sum(k == "heartbeat" for k in kinds(tmp_path)) >= 4)

        recs = read_all(tmp_path)
        assert [r["t"] for r in recs][0] == "connect"
        assert sum(r["t"] == "heartbeat" for r in recs) >= 4
        assert not any(r["t"] == "msg" for r in recs)
        assert recs[-1]["t"] == "disconnect" and recs[-1]["reason"] == "shutdown"
        assert liq.compute_gaps(recs, now_ms=recs[-1]["ts"]) == []

    def test_heartbeat_counts_frames_since_the_previous_one(
        self, tmp_path: Path
    ) -> None:
        clock = {"t": DAY1}
        net = FakeNet([['{"a":1}', '{"a":2}']], clock, then="block")
        rec = make(tmp_path, net, clock, heartbeat_interval_s=0.05)
        run(rec, until=lambda: sum(k == "heartbeat" for k in kinds(tmp_path)) >= 2)
        hbs = [r for r in read_all(tmp_path) if r["t"] == "heartbeat"]
        assert hbs[0]["msgs"] == 0 and hbs[1]["msgs"] == 2

    def test_max_connection_age_forces_an_immediate_reconnect(
        self, tmp_path: Path
    ) -> None:
        """The scheduled-24h-cut path, forced short: disconnect -> reconnect, no backoff."""
        clock = {"t": DAY1}
        net = FakeNet([[], [], []], clock, then="block")
        rec = make(tmp_path, net, clock, max_connection_age_s=0.05)
        run(rec, until=lambda: net.attempts >= 3)

        seq = [k for k in kinds(tmp_path) if k != "heartbeat"]
        assert seq[:5] == [
            "connect",
            "disconnect",
            "reconnect",
            "disconnect",
            "reconnect",
        ]
        dis = next(r for r in read_all(tmp_path) if r["t"] == "disconnect")
        assert dis["reason"] == "max_connection_age"
        assert rec.backoffs[:2] == [0.0, 0.0]

    def test_a_flapping_connection_backs_off_instead_of_hammering(
        self, tmp_path: Path
    ) -> None:
        clock = {"t": DAY1}
        net = FakeNet([[], [], [], [], []], clock, then="close")
        rec = make(tmp_path, net, clock, stable_after_s=3600)
        run(rec, until=lambda: net.attempts >= 5)
        assert rec.backoffs[:4] == [0.001, 0.002, 0.004, 0.004]

    def test_stop_event_ends_cleanly_with_a_disconnect(self, tmp_path: Path) -> None:
        clock = {"t": DAY1}
        net = FakeNet([[]], clock, then="block")
        rec = make(tmp_path, net, clock)
        run(rec, until=lambda: "heartbeat" in kinds(tmp_path))
        last = read_all(tmp_path)[-1]
        assert last["t"] == "disconnect" and last["reason"] == "shutdown"
        assert net.attempts == 1  # did not reconnect after stopping

    def test_stop_file_ends_cleanly_and_is_consumed(self, tmp_path: Path) -> None:
        clock = {"t": DAY1}
        net = FakeNet([[]], clock, then="block")
        rec = make(tmp_path, net, clock)

        async def main() -> None:
            task = asyncio.ensure_future(rec.run())
            while "heartbeat" not in kinds(tmp_path):
                await asyncio.sleep(0.005)
            (tmp_path / lr.STOP_FILE).write_text("stop", encoding="utf-8")
            await asyncio.wait_for(task, 5)

        asyncio.run(main())
        last = read_all(tmp_path)[-1]
        assert last["t"] == "disconnect" and last["reason"] == "stop-file"
        assert not (tmp_path / lr.STOP_FILE).exists()

    def test_a_stale_stop_file_does_not_kill_a_fresh_start(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / lr.STOP_FILE).write_text("stop", encoding="utf-8")
        clock = {"t": DAY1}
        net = FakeNet([[]], clock, then="block")
        rec = make(tmp_path, net, clock)
        run(rec, until=lambda: "heartbeat" in kinds(tmp_path))
        assert "connect" in kinds(tmp_path)

    def test_day_rotation_mid_run_gzips_and_keeps_every_record(
        self, tmp_path: Path
    ) -> None:
        clock = {"t": DAY1}
        net = FakeNet([['{"a":1}', NEXT_DAY, '{"a":2}']], clock, then="block")
        rec = make(tmp_path, net, clock, heartbeat_interval_s=3600)
        run(rec, until=lambda: rec.n_messages >= 2)

        assert (tmp_path / f"{liq.utc_day(DAY1)}.jsonl.gz").exists()
        assert not (tmp_path / f"{liq.utc_day(DAY1)}.jsonl").exists()
        got = [r["msg"] for r in read_all(tmp_path) if r["t"] == "msg"]
        assert got == [{"a": 1}, {"a": 2}]

    def test_recorder_never_opens_analytics_db(self, tmp_path: Path) -> None:
        clock = {"t": DAY1}
        rec = make(tmp_path, FakeNet([[]], clock, then="block"), clock)
        run(rec, until=lambda: "heartbeat" in kinds(tmp_path))
        assert not list(tmp_path.glob("*.db"))


def _env_without_pythonpath() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def test_bare_invocation_works(tmp_path: Path) -> None:
    """`python monitor/liq_recorder.py` has no PYTHONPATH and a foreign cwd (AGENTS.md)."""
    root = Path(__file__).resolve().parents[1]
    script = str(root / "monitor" / "liq_recorder.py")
    d = str(tmp_path / "data")

    status = subprocess.run(
        [sys.executable, script, "--status", "--data-dir", d],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_env_without_pythonpath(),
    )
    assert status.returncode == 1, status.stderr  # nothing recorded -> not healthy
    assert "NO HEARTBEAT" in status.stdout

    stop = subprocess.run(
        [sys.executable, script, "--stop", "--data-dir", d],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_env_without_pythonpath(),
    )
    assert stop.returncode == 0, stop.stderr
    assert (tmp_path / "data" / lr.STOP_FILE).exists()


@pytest.mark.parametrize(
    "attempt,expected", [(0, 0.0), (1, 1.0), (2, 2.0), (3, 4.0), (9, 60.0)]
)
def test_default_backoff_schedule(
    tmp_path: Path, attempt: int, expected: float
) -> None:
    rec = lr.Recorder(tmp_path)
    assert rec._delay(attempt) == expected
