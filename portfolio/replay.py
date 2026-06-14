"""Glue the DuckDB outcome ledger + 1d OHLCV + regime into the paper book.

The only module in `portfolio/` that touches the database. Reads resolved
`signal_alert_outcomes` rows, builds a daily grid spanning the ledger, aligns
each symbol's 1d close to that grid (forward-filled), optionally labels each
entry's 1d regime via `analytics.regime.classify_series`, and runs `PaperBook`.
"""

from __future__ import annotations

import duckdb
import numpy as np

from analytics.data_store import get_ohlcv
from analytics.regime import classify_series
from portfolio.book import BookResult, LedgerTrade, PaperBook
from portfolio.sizing import SizingConfig

_DAY = 86_400_000

_RESOLVED_SQL = (
    "SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms, "
    "       outcome_filled_at_ms, entry_price, sl_price, outcome, outcome_r "
    "FROM signal_alert_outcomes "
    "WHERE outcome IN ('win', 'loss', 'expired') AND outcome_r IS NOT NULL "
    "  AND candle_ts_ms IS NOT NULL AND outcome_filled_at_ms IS NOT NULL "
    "ORDER BY candle_ts_ms"
)


def _empty_result(cfg: SizingConfig) -> BookResult:
    return BookResult(
        daily_index=np.array([], dtype=np.int64),
        capital=cfg.capital,
        pnl_fixed=np.array([]),
        pnl_comp=np.array([]),
        sized=[],
        skipped=[],
    )


def replay_ledger(conn: duckdb.DuckDBPyConnection, cfg: SizingConfig) -> BookResult:
    """Replay resolved signal outcomes through the paper book.

    Parameters
    ----------
    conn:
        Open DuckDB connection with `signal_alert_outcomes` and `ohlcv` tables.
    cfg:
        Sizing configuration controlling risk fractions, vol governor, and
        whether high-vol regime halving is applied.

    Returns
    -------
    BookResult
        Daily MTM curves (fixed and compound basis) plus per-trade accounting.
    """
    rows = conn.execute(_RESOLVED_SQL).fetchall()
    if not rows:
        return _empty_result(cfg)

    trades = [
        LedgerTrade(
            signal_id=str(r[0]),
            symbol=str(r[1]),
            tf=str(r[2]),
            strategy=str(r[3]),
            direction=str(r[4]),
            entry_ts_ms=int(r[5]),
            exit_ts_ms=int(r[6]),
            entry_price=float(r[7]),
            sl_price=float(r[8]),
            outcome=str(r[9]),
            realized_r=float(r[10]),
        )
        for r in rows
    ]

    min_entry = min(t.entry_ts_ms for t in trades)
    max_exit = max(t.exit_ts_ms for t in trades)
    start_day = (min_entry // _DAY) * _DAY
    end_day = (max_exit // _DAY) * _DAY
    daily_index = np.arange(start_day, end_day + _DAY, _DAY, dtype=np.int64)

    symbols = sorted({t.symbol for t in trades})
    close_by_symbol: dict[str, np.ndarray] = {}
    regime_by_signal: dict[str, str] = {}
    regime_by_symbol_grid: dict[str, np.ndarray] = {}

    for sym in symbols:
        bars = get_ohlcv(conn, sym, "1d", int(start_day), int(end_day + _DAY))
        if bars.empty:
            close_by_symbol[sym] = np.full(len(daily_index), np.nan)
            continue
        ot = bars["open_time"].to_numpy(dtype=np.int64)
        cl = bars["close"].to_numpy(dtype=np.float64)
        idx = np.searchsorted(ot, daily_index, side="right") - 1
        valid = idx >= 0
        aligned = np.full(len(daily_index), np.nan)
        aligned[valid] = cl[idx[valid]]
        close_by_symbol[sym] = aligned

        if cfg.apply_high_vol_halving:
            # classify_series requires the timeframe as the second argument;
            # "1d" is in _BARS_PER_DAY so this never raises ValueError.
            raw_labels: np.ndarray = np.asarray(
                classify_series(bars, "1d").to_numpy(), dtype=object
            )
            grid_labels: np.ndarray = np.full(len(daily_index), "unknown", dtype=object)
            grid_labels[valid] = raw_labels[idx[valid]]
            regime_by_symbol_grid[sym] = grid_labels

    if cfg.apply_high_vol_halving:
        for t in trades:
            grid = regime_by_symbol_grid.get(t.symbol)
            if grid is None:
                continue
            entry_idx = (
                int(np.searchsorted(daily_index, t.entry_ts_ms, side="right")) - 1
            )
            if 0 <= entry_idx < len(grid):
                regime_by_signal[t.signal_id] = str(grid[entry_idx])

    book = PaperBook(
        cfg,
        daily_index,
        close_by_symbol,
        regime_by_signal=regime_by_signal if cfg.apply_high_vol_halving else None,
    )
    return book.run(trades)
