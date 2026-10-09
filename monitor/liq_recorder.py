"""Forward recorder for Binance's `!forceOrder@arr` liquidation stream (#984).

Long-running: appends every raw frame, plus its own connect/disconnect/reconnect events
and a 60s heartbeat, to ``<data_dir>/<YYYY-MM-DD>.jsonl`` (UTC day), and gzips a file
when its day ends. It never opens ``analytics.db``.

THE STREAM IS A SAMPLE. Binance documents that "for each symbol, only the latest one
liquidation order within 1000ms will be pushed as the snapshot" -- at most one
liquidation per symbol per second reaches this file. Source:
https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/All-Market-Liquidation-Order-Streams
Record format, gap semantics and the reader live in `analytics/liquidations.py`.
The URL must use the `/market` route: the legacy `/ws` and `/stream` paths connect and
then push nothing (see the routing trap in that module's docstring).

Run (repo root)::

    python monitor/liq_recorder.py [--data-dir DIR] [--max-connection-age S]
    python monitor/liq_recorder.py --status      # exit 1 unless the recorder is healthy
    python monitor/liq_recorder.py --stop        # ask the running recorder to exit cleanly

Stopping: Ctrl-C / SIGTERM, or the stop file that ``--stop`` drops in the data dir (the
only graceful path under Task Scheduler, whose "End" is a hard kill). A clean stop writes
a ``disconnect`` (reason ``shutdown``/``stop-file``) and exits 0, so Task Scheduler's
restart-on-failure does not resurrect it. A hard kill leaves no ``disconnect``; the gap
logic reads the next ``connect`` as a restart and counts the dead time.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import os
import shutil
import signal
import sys
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import IO, Any, Protocol

if __package__ in (None, ""):  # bare `python monitor/liq_recorder.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics.liquidations import (  # noqa: E402
    HEARTBEAT_INTERVAL_S,
    STREAMS,
    data_dir,
    day_files,
    now_ms,
    recorder_status,
    stream_url,
    utc_day,
)

log = logging.getLogger("liq_recorder")

STOP_FILE = "STOP"
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 60.0
# A session shorter than this does not reset the backoff: connect-then-drop flapping
# must not be retried at full speed.
STABLE_AFTER_S = 60.0


class Socket(Protocol):
    async def recv(self) -> str | bytes: ...


Connector = Callable[[str], AbstractAsyncContextManager[Any]]


def default_connector(url: str) -> AbstractAsyncContextManager[Any]:
    """websockets' client (a DIRECT dependency). Its keepalive pings answer Binance's
    3-minute server ping and detect a dead TCP connection, which would otherwise look
    exactly like a quiet market."""
    from websockets.asyncio.client import connect

    return connect(
        url, ping_interval=20, ping_timeout=20, open_timeout=15, close_timeout=5
    )


def gzip_file(path: Path) -> Path:
    """Atomically replace ``path`` (a finished ``.jsonl``) with ``path + .gz``."""
    gz = path.with_name(path.name + ".gz")
    tmp = path.with_name(path.name + ".gz.tmp")
    with path.open("rb") as src, gzip.open(tmp, "wb") as dst:
        shutil.copyfileobj(src, dst)
    os.replace(tmp, gz)
    path.unlink()
    return gz


class DayWriter:
    """Appends records to the file of their UTC receive day; gzips a day when it ends.

    The day never moves backwards: a clock step into an earlier day keeps writing to the
    current file, so a finished (gzipped) day is never reopened beside its own ``.gz``.
    """

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        directory.mkdir(parents=True, exist_ok=True)
        self._day: str | None = None
        self._fh: IO[str] | None = None
        self.gzipped: list[Path] = []

    def compress_finished(self, today: str) -> None:
        """Gzip every ``.jsonl`` of an earlier day (a previous run died before rotating)."""
        for p in day_files(self.dir):
            if p.suffix == ".jsonl" and p.name[:10] < today:
                self.gzipped.append(gzip_file(p))
        # A `.jsonl` beside a complete `.gz` is a leftover of a crash after the rename.
        for p in self.dir.glob("*.jsonl"):
            if p.name[:10] < today and p.with_name(p.name + ".gz").exists():
                p.unlink()

    def write(self, record: dict[str, Any], *, sync: bool = False) -> None:
        day = utc_day(record["ts"])
        if self._day is None or day > self._day:
            self._rotate(day)
        assert self._fh is not None
        self._fh.write(
            json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
        )
        self._fh.flush()
        if sync:
            os.fsync(self._fh.fileno())

    def _rotate(self, day: str) -> None:
        previous = self._day
        self.close()
        if previous is not None:
            self.gzipped.append(gzip_file(self.dir / f"{previous}.jsonl"))
        self.compress_finished(day)
        self._day = day
        self._fh = (self.dir / f"{day}.jsonl").open("a", encoding="utf-8")

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


class Recorder:
    def __init__(
        self,
        directory: Path,
        *,
        streams: tuple[str, ...] = STREAMS,
        connector: Connector = default_connector,
        clock_ms: Callable[[], int] = now_ms,
        sleep: Callable[[float], Any] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
        backoff_base_s: float = BACKOFF_BASE_S,
        backoff_cap_s: float = BACKOFF_CAP_S,
        stable_after_s: float = STABLE_AFTER_S,
        max_connection_age_s: float | None = None,
        stop_poll_s: float = 1.0,
    ) -> None:
        self.writer = DayWriter(directory)
        self.stop_path = directory / STOP_FILE
        self.url = stream_url(streams)
        self._connector = connector
        self._clock = clock_ms
        self._sleep = sleep
        self._mono = monotonic
        self._hb_interval = heartbeat_interval_s
        self._base = backoff_base_s
        self._cap = backoff_cap_s
        self._stable = stable_after_s
        self._max_age = max_connection_age_s
        self._stop_poll = stop_poll_s
        self._msgs_since_hb = 0
        self.n_messages = 0
        self.backoffs: list[float] = []
        self._stop_reason = "shutdown"

    # -- records ------------------------------------------------------------------

    def _event(self, kind: str, **fields: Any) -> None:
        self.writer.write({"t": kind, "ts": self._clock(), **fields}, sync=True)

    def _on_frame(self, frame: str | bytes) -> None:
        text = frame.decode("utf-8", "replace") if isinstance(frame, bytes) else frame
        ts = self._clock()
        try:
            rec: dict[str, Any] = {"t": "msg", "ts": ts, "msg": json.loads(text)}
        except json.JSONDecodeError:
            rec = {"t": "msg", "ts": ts, "raw": text}
        self.writer.write(rec)
        self._msgs_since_hb += 1
        self.n_messages += 1

    async def _heartbeat_loop(self, started: float) -> None:
        # Immediately on connect, so a fresh connection is vouched for before 60s pass.
        while True:
            self._event(
                "heartbeat",
                msgs=self._msgs_since_hb,
                conn_age_s=round(self._mono() - started, 1),
            )
            self._msgs_since_hb = 0
            await self._sleep(self._hb_interval)

    async def _stop_file_watcher(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if self.stop_path.exists():
                self.stop_path.unlink(missing_ok=True)
                self._stop_reason = "stop-file"
                stop.set()
                return
            await asyncio.sleep(self._stop_poll)

    # -- one connection -----------------------------------------------------------

    async def _session(self, ws: Socket, stop: asyncio.Event, started: float) -> str:
        """Read until the socket closes (raises), the age limit hits or a stop arrives."""
        hb = asyncio.ensure_future(self._heartbeat_loop(started))
        stopped = asyncio.ensure_future(stop.wait())
        recv: asyncio.Future[str | bytes] | None = None
        try:
            while True:
                remaining = None
                if self._max_age is not None:
                    remaining = self._max_age - (self._mono() - started)
                    if remaining <= 0:
                        return "max_connection_age"
                recv = asyncio.ensure_future(ws.recv())
                done, _ = await asyncio.wait(
                    {recv, stopped, hb},
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if recv in done:
                    self._on_frame(recv.result())  # a closed socket raises here
                    recv = None
                    continue
                if stopped in done:
                    return self._stop_reason
                if hb in done:
                    hb.result()  # only ever an exception
        finally:
            for fut in (recv, hb, stopped):
                if fut is not None:
                    fut.cancel()
            await asyncio.gather(
                *(f for f in (recv, hb, stopped) if f), return_exceptions=True
            )

    def _delay(self, attempt: int) -> float:
        """0 after a stable session (the 24h cut), then base * 2^(n-1) capped."""
        return 0.0 if attempt <= 0 else min(self._cap, self._base * 2 ** (attempt - 1))

    # -- main loop ----------------------------------------------------------------

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        self.writer.compress_finished(utc_day(self._clock()))
        self.stop_path.unlink(
            missing_ok=True
        )  # a stale request must not kill a fresh start
        watcher = asyncio.ensure_future(self._stop_file_watcher(stop))
        attempt = 0
        first = True
        try:
            while not stop.is_set():
                reason = "closed"
                started = self._mono()
                connected = False
                try:
                    async with self._connector(self.url) as ws:
                        connected = True
                        if first:
                            self._event("connect", url=self.url)
                        else:
                            self._event(
                                "reconnect",
                                attempt=attempt,
                                backoff_s=self.backoffs[-1] if self.backoffs else 0.0,
                            )
                        first = False
                        started = self._mono()
                        reason = await self._session(ws, stop, started)
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    if not connected:
                        self._event("connect_error", reason=reason)
                if connected:
                    self._event(
                        "disconnect",
                        reason=reason,
                        conn_age_s=round(self._mono() - started, 1),
                    )
                    log.warning("disconnected: %s", reason)
                if stop.is_set():
                    break
                stable = connected and (
                    self._mono() - started >= self._stable
                    or reason == "max_connection_age"
                )
                if stable:
                    attempt = 0
                else:
                    attempt += 1
                delay = self._delay(attempt)
                self.backoffs.append(delay)
                await self._interruptible_sleep(delay, stop)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            self.writer.close()

    async def _interruptible_sleep(self, delay: float, stop: asyncio.Event) -> None:
        if delay <= 0:
            await asyncio.sleep(0)
            return
        sleeper = asyncio.ensure_future(self._sleep(delay))
        waiter = asyncio.ensure_future(stop.wait())
        try:
            await asyncio.wait({sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            waiter.cancel()
            await asyncio.gather(sleeper, waiter, return_exceptions=True)


# --- CLI ---------------------------------------------------------------------------


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop, stop: asyncio.Event
) -> None:
    def handler(signum: int, frame: Any) -> None:
        loop.call_soon_threadsafe(stop.set)

    # `loop.add_signal_handler` does not exist on Windows' event loops.
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), handler)


async def _amain(args: argparse.Namespace) -> None:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    _install_signal_handlers(loop, stop)
    rec = Recorder(
        data_dir(args.data_dir),
        heartbeat_interval_s=args.heartbeat_interval,
        max_connection_age_s=args.max_connection_age,
    )
    log.info("recording %s -> %s", rec.url, rec.writer.dir)
    await rec.run(stop)
    log.info("stopped cleanly (%d messages this run)", rec.n_messages)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0] if __doc__ else None
    )
    ap.add_argument("--data-dir", help="default: $BUIBUI_LIQ_DIR or data/liquidations")
    ap.add_argument("--heartbeat-interval", type=float, default=HEARTBEAT_INTERVAL_S)
    ap.add_argument(
        "--max-connection-age",
        type=float,
        default=None,
        help="proactively reconnect after S seconds (default: leave it to Binance's 24h cut)",
    )
    ap.add_argument(
        "--status", action="store_true", help="print health, exit 1 unless ok"
    )
    ap.add_argument(
        "--stop", action="store_true", help="ask the running recorder to exit"
    )
    args = ap.parse_args(argv)

    if args.status:
        s = recorder_status(args.data_dir)
        print(s.line)
        return 0 if s.ok else 1
    if args.stop:
        d = data_dir(args.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / STOP_FILE).write_text("stop\n", encoding="utf-8")
        print(f"stop requested via {d / STOP_FILE}")
        return 0

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)sZ %(levelname)s %(message)s",
    )
    logging.Formatter.converter = time.gmtime
    asyncio.run(_amain(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
