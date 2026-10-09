"""Forward-recorded Binance liquidations: file layout, gap computation, batch reader (#984).

The recorder (`monitor/liq_recorder.py`) appends to one JSONL file per UTC day. This
module owns everything a CONSUMER needs: where the files are, what a record looks like,
which windows are gaps, and a loader that hands studies a DataFrame with the gaps
already flagged. It never touches `analytics.db` (signal-watch holds its exclusive lock
every 15 minutes).

THE STREAM, AND WHAT IT IS (confirmed 2026-10-09)
-------------------------------------------------
Binance USD-M futures, all-market liquidation order stream ``!forceOrder@arr`` on
``wss://fstream.binance.com/market/stream?streams=!forceOrder@arr``, update speed 1000ms. Binance's own documentation states:
"For each symbol, only the latest one liquidation order within 1000ms will be pushed as
the snapshot." So **the stream is a SAMPLE by construction**: at most one liquidation per
symbol per second reaches us, and a burst inside one second is collapsed to its last
order. Every study on this data must state that; counts and notional are LOWER BOUNDS,
and the shortfall is largest exactly in a cascade, which is when it matters.

Source: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/All-Market-Liquidation-Order-Streams

ROUTING TRAP (measured 2026-10-09): market streams are served under ``/market``. The
legacy ``/ws/`` and ``/stream`` paths still ACCEPT the connection, keep it open and push
ZERO frames -- 0 frames against 88 on the same stream in the same 120 seconds. A
recorder on the old path heartbeats happily and records nothing, which no gap rule can
see, so ``recorder_status`` also raises a separate DEAD-SUBSCRIPTION alarm (connected
for ``SILENT_ALARM_H`` hours with zero frames). That alarm is not a gap: gaps never
come from message silence.

RECORD SHAPE
------------
One JSON object per line, ``t`` first, ``ts`` = local receive time in epoch ms:

* ``{"t":"msg","ts":..,"msg":<parsed payload>}`` (``raw`` instead of ``msg`` if the frame
  was not JSON)
* ``{"t":"heartbeat","ts":..,"msgs":<frames since last heartbeat>,"conn_age_s":..}``
  — written every 60s, **only while a connection is open**
* ``{"t":"connect"|"reconnect","ts":..,...}`` and ``{"t":"disconnect","ts":..,"reason":..}``
* ``{"t":"connect_error","ts":..,"reason":..}`` — informational, a failed attempt

A GAP IS NEVER "NO MESSAGES"
----------------------------
A quiet market sends nothing, so message silence says nothing about the recorder. A gap
is a window in which we cannot vouch for coverage:

* **no heartbeat for more than ``GAP_THRESHOLD_S``** (heartbeats stop when the process
  dies, the host sleeps, or the socket is down), or
* **a down connection**: from a ``disconnect`` to the next ``connect``/``reconnect``, or
  from the last sign of life to a ``connect`` that was not preceded by a ``disconnect``
  (the process was killed and restarted).

Overlapping windows are merged. Because a heartbeat is only written while connected, one
rule covers dead process, sleeping host and dropped socket alike.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb
    import pandas as pd

# The ONE stream list. A second stream is added only when an Issue names it.
STREAMS: tuple[str, ...] = ("!forceOrder@arr",)
WS_BASE = "wss://fstream.binance.com"

HEARTBEAT_INTERVAL_S = 60
# "No heartbeat for more than ~2 minutes". Twice the interval, so one late tick is not a gap.
GAP_THRESHOLD_S = 120
# `recorder_status` reds when more than this many minutes of the past 24h were uncovered.
GAP_BUDGET_MINUTES = 15.0
# Connected and heartbeating this long with ZERO frames = a dead subscription (see the
# routing trap). The all-market stream carries tens of liquidations an hour at its
# quietest, so a silent hour is not a quiet market. Separate from the gap rule.
SILENT_ALARM_H = 1.0

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "liquidations"
DATA_DIR_ENV = "BUIBUI_LIQ_DIR"

_DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$")
# Binance's wrapped combined-stream frames carry the stream name beside the payload.
_MSG_PREFIX = '{"t":"msg"'


def data_dir(override: str | Path | None = None) -> Path:
    """The data directory: explicit argument, else ``$BUIBUI_LIQ_DIR``, else the repo default."""
    if override:
        return Path(override)
    env = os.environ.get(DATA_DIR_ENV)
    return Path(env) if env else DEFAULT_DATA_DIR


def now_ms() -> int:
    return int(time.time() * 1000)


def utc_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, UTC).strftime("%Y-%m-%d")


def stream_url(streams: Iterable[str] = STREAMS) -> str:
    """Combined-stream URL, so every frame names the stream it came from."""
    return f"{WS_BASE}/market/stream?streams={'/'.join(streams)}"


# --- reading ---------------------------------------------------------------------


def day_files(directory: Path, since_day: str | None = None) -> list[Path]:
    """Day files oldest first. ``.gz`` wins over ``.jsonl`` for the same day.

    Both exist only if a crash landed between the gzip's atomic rename and the removal of
    its source; the ``.gz`` is the complete one, so reading both would double the day.
    """
    chosen: dict[str, Path] = {}
    if not directory.is_dir():
        return []
    for p in directory.iterdir():
        m = _DAY_FILE.match(p.name)
        if not m or (since_day is not None and m.group(1) < since_day):
            continue
        day, is_gz = m.group(1), bool(m.group(2))
        if day not in chosen or is_gz:
            chosen[day] = p
    return [chosen[d] for d in sorted(chosen)]


def iter_records(
    directory: Path, *, since_day: str | None = None, include_messages: bool = False
) -> Iterator[dict[str, Any]]:
    """Yield records in file order. A truncated final line (crash mid-write) is skipped.

    Message lines are skipped UNPARSED unless asked for: the gap logic never needs them
    and they are ~99% of the bytes.
    """
    for path in day_files(directory, since_day):
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not include_messages and line.startswith(_MSG_PREFIX):
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(rec, dict)
                    and "t" in rec
                    and isinstance(rec.get("ts"), int)
                ):
                    yield rec


# --- gaps ------------------------------------------------------------------------


@dataclass(frozen=True)
class Gap:
    start_ms: int
    end_ms: int
    cause: str  # "no_heartbeat" | "disconnected" | "restart" | "a+b" when merged

    @property
    def minutes(self) -> float:
        return (self.end_ms - self.start_ms) / 60_000


def _merge(intervals: list[tuple[int, int, str]]) -> list[Gap]:
    merged: list[list[Any]] = []
    for start, end, cause in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            if cause not in merged[-1][2]:
                merged[-1][2].append(cause)
        else:
            merged.append([start, end, [cause]])
    return [Gap(s, e, "+".join(c)) for s, e, c in merged]


def compute_gaps(
    events: Iterable[dict[str, Any]],
    *,
    now_ms: int,
    threshold_s: float = GAP_THRESHOLD_S,
    window: tuple[int, int] | None = None,
) -> list[Gap]:
    """Uncovered windows implied by heartbeat/connection events. ``msg`` records are ignored.

    ``window`` clips the result to ``[start, end]`` (epoch ms). State is carried from
    records BEFORE the window, so a disconnect that began earlier is still seen.
    """
    thr = int(threshold_s * 1000)
    out: list[tuple[int, int, str]] = []
    last_hb: int | None = None
    last_alive: int | None = None
    down_since: int | None = None

    for e in sorted(
        (e for e in events if e["t"] != "msg"), key=lambda r: r["ts"]
    ):  # stable: file order breaks ts ties
        t, kind = e["ts"], e["t"]
        if kind == "heartbeat":
            if last_hb is not None and t - last_hb > thr:
                out.append((last_hb, t, "no_heartbeat"))
            last_hb = last_alive = t
        elif kind == "disconnect":
            if down_since is None:
                down_since = t
            last_alive = t
        elif kind in ("connect", "reconnect"):
            if down_since is not None:
                out.append((down_since, t, "disconnected"))
            elif last_alive is not None:
                # No disconnect on record: the process died and was restarted.
                out.append((last_alive, t, "restart"))
            down_since = None
            last_alive = t

    if last_hb is not None and now_ms - last_hb > thr:
        out.append((last_hb, now_ms, "no_heartbeat"))
    if down_since is not None:
        out.append((down_since, now_ms, "disconnected"))

    gaps = _merge(out)
    if window is None:
        return gaps
    lo, hi = window
    clipped = [Gap(max(g.start_ms, lo), min(g.end_ms, hi), g.cause) for g in gaps]
    return [g for g in clipped if g.end_ms > g.start_ms]


@dataclass(frozen=True)
class RecorderStatus:
    ok: bool
    last_heartbeat_age_s: float | None
    gap_minutes: float
    gaps: list[Gap]
    window_h: float
    covered_since_ms: int | None
    messages: int = 0
    silent: bool = False

    @property
    def line(self) -> str:
        if self.last_heartbeat_age_s is None:
            return "liq-recorder  NO HEARTBEAT ON RECORD"
        since = ""
        if self.covered_since_ms is not None:
            hrs = (now_ms() - self.covered_since_ms) / 3_600_000
            if hrs < self.window_h:
                since = f"  (recording {hrs:.1f}h)"
        return (
            f"liq-recorder  {'ok' if self.ok else 'RED'}  "
            f"last heartbeat {self.last_heartbeat_age_s:.0f}s ago  "
            f"gap {self.gap_minutes:.1f}min/{self.window_h:g}h "
            f"({len(self.gaps)} windows)  msgs {self.messages}{since}"
            + (
                f"  NO FRAMES FOR {SILENT_ALARM_H:g}h+ (dead subscription?)"
                if self.silent
                else ""
            )
        )


def recorder_status(
    directory: str | Path | None = None,
    *,
    now: int | None = None,
    window_h: float = 24.0,
    max_gap_minutes: float = GAP_BUDGET_MINUTES,
    threshold_s: float = GAP_THRESHOLD_S,
) -> RecorderStatus:
    """Last-heartbeat age and gap minutes over the past ``window_h`` hours.

    ``ok`` = the recorder is alive now (last heartbeat within the threshold), the
    window's uncovered minutes are within ``max_gap_minutes``, AND the subscription is
    not silent (``SILENT_ALARM_H``). Reads from three days
    before the window so state carried across a day boundary is not lost; a recorder
    that was dead for longer than that before restarting has its older outage clipped.
    """
    d = data_dir(directory)
    now_v = now_ms() if now is None else now
    lo = now_v - int(window_h * 3_600_000)
    since_day = utc_day(lo - 3 * 86_400_000)
    events = list(iter_records(d, since_day=since_day))
    hbs = [e["ts"] for e in events if e["t"] == "heartbeat"]
    if not hbs:
        return RecorderStatus(False, None, 0.0, [], window_h, None)
    age = (now_v - max(hbs)) / 1000
    gaps = compute_gaps(
        events, now_ms=now_v, threshold_s=threshold_s, window=(lo, now_v)
    )
    gap_min = sum(g.minutes for g in gaps)
    first = min(e["ts"] for e in events)
    in_window = [e for e in events if e["t"] == "heartbeat" and e["ts"] > lo]
    messages = sum(int(e.get("msgs", 0)) for e in in_window)
    silent_lo = now_v - int(SILENT_ALARM_H * 3_600_000)
    recent = [e for e in in_window if e["ts"] > silent_lo]
    # Heartbeating for most of the silent window (not a dead process: that is a gap) yet
    # no frame in any of it.
    silent = (
        first <= silent_lo
        and len(recent) >= 0.5 * SILENT_ALARM_H * 3600 / HEARTBEAT_INTERVAL_S
        and sum(int(e.get("msgs", 0)) for e in recent) == 0
    )
    return RecorderStatus(
        ok=age <= threshold_s and gap_min <= max_gap_minutes and not silent,
        last_heartbeat_age_s=age,
        gap_minutes=gap_min,
        gaps=gaps,
        window_h=window_h,
        covered_since_ms=first,
        messages=messages,
        silent=silent,
    )


def daily_check_line(directory: str | Path | None = None) -> tuple[bool, str]:
    """``(ok, text)`` for `daily_check.py`'s tier-1 block. Never raises."""
    try:
        s = recorder_status(directory)
    except Exception as exc:
        return (
            False,
            f"liq-recorder  RED  status unreadable: {type(exc).__name__}: {exc}",
        )
    return s.ok, s.line


# --- batch reader ----------------------------------------------------------------

ORDER_COLUMNS = [
    "ts",
    "recv_ms",
    "stream",
    "event_time_ms",
    "symbol",
    "side",
    "order_type",
    "time_in_force",
    "qty",
    "price",
    "avg_price",
    "status",
    "last_filled_qty",
    "filled_qty",
    "trade_time_ms",
    "pair",
    "symbol_type",
    "in_gap",
]


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _flatten(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """One ``msg`` record -> forceOrder rows (wrapped or bare payload; list-tolerant)."""
    payload = rec.get("msg")
    stream = None
    if isinstance(payload, dict) and "data" in payload and "stream" in payload:
        stream, payload = payload["stream"], payload["data"]
    events = payload if isinstance(payload, list) else [payload]
    rows = []
    for ev in events:
        if not isinstance(ev, dict) or ev.get("e") != "forceOrder":
            continue
        o = ev.get("o") or {}
        rows.append(
            {
                "recv_ms": rec["ts"],
                "stream": stream,
                "event_time_ms": ev.get("E"),
                "symbol": o.get("s"),
                "side": o.get("S"),
                "order_type": o.get("o"),
                "time_in_force": o.get("f"),
                "qty": _num(o.get("q")),
                "price": _num(o.get("p")),
                "avg_price": _num(o.get("ap")),
                "status": o.get("X"),
                "last_filled_qty": _num(o.get("l")),
                "filled_qty": _num(o.get("z")),
                "trade_time_ms": o.get("T"),
                "pair": o.get("ps"),
                "symbol_type": o.get("st"),
            }
        )
    return rows


def load_gaps(
    directory: str | Path | None = None, *, now: int | None = None
) -> pd.DataFrame:
    """Every gap across all recorded history, as ``start/end/minutes/cause`` (UTC)."""
    import pandas as pd

    d = data_dir(directory)
    gaps = compute_gaps(iter_records(d), now_ms=now_ms() if now is None else now)
    return pd.DataFrame(
        {
            "start": pd.to_datetime([g.start_ms for g in gaps], unit="ms", utc=True),
            "end": pd.to_datetime([g.end_ms for g in gaps], unit="ms", utc=True),
            "start_ms": [g.start_ms for g in gaps],
            "end_ms": [g.end_ms for g in gaps],
            "minutes": [g.minutes for g in gaps],
            "cause": [g.cause for g in gaps],
        }
    )


def load_orders(
    directory: str | Path | None = None,
    *,
    now: int | None = None,
    exclude_gaps: bool = False,
) -> pd.DataFrame:
    """Recorded liquidation orders, one row each, with ``in_gap`` flagging gap windows.

    The stream is a 1000ms-per-symbol SAMPLE (see the module docstring): counts and
    notional are lower bounds. ``exclude_gaps=True`` drops the flagged rows; the default
    keeps and flags them so a study can show both readings.
    """
    import numpy as np
    import pandas as pd

    d = data_dir(directory)
    rows: list[dict[str, Any]] = []
    for rec in iter_records(d, include_messages=True):
        if rec["t"] == "msg":
            rows.extend(_flatten(rec))
    df = pd.DataFrame(
        rows, columns=[c for c in ORDER_COLUMNS if c not in ("ts", "in_gap")]
    )
    df.insert(0, "ts", pd.to_datetime(df["recv_ms"], unit="ms", utc=True))

    gaps = load_gaps(d, now=now)
    if len(gaps) and len(df):
        starts = gaps["start_ms"].to_numpy()
        ends = gaps["end_ms"].to_numpy()
        # Gaps are merged and sorted, so the candidate window is the last start <= t.
        idx = np.searchsorted(starts, df["recv_ms"].to_numpy(), side="right") - 1
        in_gap = (idx >= 0) & (df["recv_ms"].to_numpy() <= ends[np.clip(idx, 0, None)])
    else:
        in_gap = np.zeros(len(df), dtype=bool)
    df["in_gap"] = in_gap
    return df[~df["in_gap"]].reset_index(drop=True) if exclude_gaps else df


def register_duckdb(
    conn: duckdb.DuckDBPyConnection,
    directory: str | Path | None = None,
    *,
    now: int | None = None,
) -> None:
    """Expose ``liq_orders`` (with ``in_gap``) and ``liq_gaps`` as tables on ``conn``."""
    orders = load_orders(directory, now=now)
    gaps = load_gaps(directory, now=now)
    conn.register("_liq_orders_df", orders)
    conn.register("_liq_gaps_df", gaps)
    conn.execute("CREATE OR REPLACE TABLE liq_orders AS SELECT * FROM _liq_orders_df")
    conn.execute("CREATE OR REPLACE TABLE liq_gaps AS SELECT * FROM _liq_gaps_df")
    conn.unregister("_liq_orders_df")
    conn.unregister("_liq_gaps_df")
