"""Data-health footer: OHLCV staleness per symbol x tf."""

from __future__ import annotations

import duckdb

from analytics.brief._common import TF_MS
from analytics.brief.config import BriefConfig
from analytics.brief.types import HealthReport, HealthRow


def _check_symbol_tf(
    conn: duckdb.DuckDBPyConnection, symbol: str, tf: str, as_of_ms: int
) -> HealthRow:
    tf_ms = TF_MS[tf]
    row = conn.execute(
        "SELECT max(open_time) FROM ohlcv "
        "WHERE symbol = ? AND timeframe = ? AND open_time <= ?",
        [symbol, tf, as_of_ms - tf_ms],  # completed bars only
    ).fetchone()
    last_open = row[0] if row is not None else None
    if last_open is None:
        return HealthRow(symbol=symbol, tf=tf, status="missing", bars_behind=0)
    bars_behind = int((as_of_ms - (int(last_open) + tf_ms)) // tf_ms)
    status = "stale" if bars_behind >= 1 else "ok"
    return HealthRow(symbol=symbol, tf=tf, status=status, bars_behind=bars_behind)


def build_health(
    conn: duckdb.DuckDBPyConnection, cfg: BriefConfig, notes: list[str]
) -> HealthReport:
    tfs = list(dict.fromkeys([*cfg.zone_tfs, "1h"]))
    rows = [
        _check_symbol_tf(conn, symbol, tf, cfg.as_of_ms)
        for symbol in cfg.symbols
        for tf in tfs
    ]
    return HealthReport(
        rows=rows,
        notes=list(notes),
        data_ok=all(r.status == "ok" for r in rows),
    )
