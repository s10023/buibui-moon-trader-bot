"""Glue the DuckDB outcome ledger + 1d OHLCV + regime into the paper book.

The only module in `portfolio/` that touches the database. Reads resolved
`signal_alert_outcomes` rows, builds a daily grid spanning the ledger, aligns
each symbol's 1d close to that grid (forward-filled), optionally labels each
entry's 1d regime via `analytics.regime.classify_series`, and runs `PaperBook`.

`book_from_trades` is the reusable seam: it takes an already-built
`list[LedgerTrade]` (e.g. re-resolved under an exit policy) and runs the same
grid/close/regime/PaperBook machinery. `replay_ledger` is the DB front door that
builds the trades from the ledger and delegates to it.
"""

from __future__ import annotations

import duckdb
import numpy as np

from analytics.data_store import get_ohlcv
from analytics.regime import classify_series
from portfolio.book import BookResult, LedgerTrade, PaperBook
from portfolio.sizing import SizingConfig

_DAY = 86_400_000

# `outcome_r` CHANGES BASIS partway through this ledger, at e5d92bb (#432,
# 2026-06-11) "live-ledger cost parity (net_R)". Rows resolved before it are
# GROSS; rows resolved after are NET of modelled costs. The backfill only scores
# rows whose outcome is still NULL, so the old half keeps the gross basis
# permanently — the table holds both bases and always will. Replaying the mixture
# averages the paper book across an accounting change, which is a second and
# independent defect on top of the era straddle `buibui portfolio replay` already
# reports.
#
# The drag is deterministic in columns this table already stores, so the old half
# restates EXACTLY. This is arithmetic, not an estimate, and nothing is rewritten
# on disk — the ledger keeps both bases so the finding stays checkable.
#
# ⚠ The costs are MODELLED, not realised: the raw component of a stop-out is still
# exactly -1.0, i.e. the DECLARED risk. So neither half expresses gap risk — a gap
# through the stop books like a clean touch — and every figure here, restated or
# not, is an optimistic bound whose error runs one way.
#
# ⚠ SPLIT ON `outcome_filled_at_ms`, NEVER `candle_ts_ms`. What matters is which
# resolver version SCORED the row. Candle time smears the step across the
# resolution lag (median 23h, tail 310h) and manufactures a phantom boundary two
# months early — that misreading cost one session before it was caught.
#
# Both constants verified two independent ways on 2026-08-14: back-solving from
# all 940 post-parity loss rows returns exactly 7.000 bps on 663 of them (mean
# 6.929, sd 0.561), and `config/strategy_params.toml` carries `fee_pct = 0.0005`
# plus `slippage_bps = 2.0` — the same 7 bps by a disjoint path. Restating moves
# the pooled ledger figure from -0.1303R to -0.1665R (n=4,907, 0 unrestatable).
_COST_PARITY_MS = 1_781_149_644_000  # e5d92bb, 2026-06-11T03:47:24Z
_DEFAULT_FEE_PCT = 0.000_5  # config/strategy_params.toml: fee_pct
_DEFAULT_SLIPPAGE_PCT = 0.000_2  # config/strategy_params.toml: slippage_bps = 2.0


def restate_gross_r(
    realized_r: float,
    entry_price: float,
    sl_price: float,
    *,
    fee_pct: float = _DEFAULT_FEE_PCT,
    slippage_pct: float = _DEFAULT_SLIPPAGE_PCT,
) -> float:
    """Put a pre-parity (gross) `outcome_r` onto the post-parity net basis.

    Mirrors `analytics.signal.outcome_backfill._net_outcome_r`: the same
    ``2 * (fee + slippage) * entry / risk`` drag the resolver has applied since
    e5d92bb. Because the drag scales with ``entry / risk`` it is *inversely*
    proportional to stop width, so narrow-stop detectors pay far more of it in R
    — which is why any live-ledger comparison between cells of differing stop
    width inherits a bias from this, not just a level shift.

    Funding is deliberately not restated: the resolver defaults ``funding_r`` to
    0.0 and the ledger stores no funding column, so a pre-parity row cannot carry
    one. A zero-risk row is returned unchanged (costs in R are undefined when
    nothing is risked — same convention as the resolver).
    """
    risk = abs(entry_price - sl_price)
    if risk <= 0.0:
        return realized_r
    return realized_r - 2.0 * (fee_pct + slippage_pct) * entry_price / risk


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


def book_from_trades(
    conn: duckdb.DuckDBPyConnection,
    cfg: SizingConfig,
    trades: list[LedgerTrade],
) -> BookResult:
    """Run a prebuilt ledger-trade list through the paper book.

    Builds the daily grid spanning the trades, aligns each symbol's 1d close
    (forward-filled) and — when `cfg.apply_high_vol_halving` — its 1d regime,
    then replays via `PaperBook`. The reusable seam shared by `replay_ledger`
    and the exit-policy A/B (which feeds re-resolved `(realized_r, exit_ts)`).
    """
    if not trades:
        return _empty_result(cfg)

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


def replay_ledger(
    conn: duckdb.DuckDBPyConnection,
    cfg: SizingConfig,
    *,
    restate_cost_basis: bool = True,
) -> BookResult:
    """Replay resolved signal outcomes through the paper book.

    Parameters
    ----------
    conn:
        Open DuckDB connection with `signal_alert_outcomes` and `ohlcv` tables.
    cfg:
        Sizing configuration controlling risk fractions, vol governor, and
        whether high-vol regime halving is applied.
    restate_cost_basis:
        When True (default), rows resolved before `_COST_PARITY_MS` are put onto
        the post-parity net basis via `restate_gross_r`, so the whole replay runs
        on ONE accounting basis. Pass False only to reproduce a pre-2026-08-14
        figure; it replays a mixture and the result is not a like-for-like series.

    Returns
    -------
    BookResult
        Daily MTM curves (fixed and compound basis) plus per-trade accounting.
    """
    rows = conn.execute(_RESOLVED_SQL).fetchall()
    if not rows:
        return _empty_result(cfg)

    def _r(row: tuple[object, ...]) -> float:
        # Restate on the RESOLUTION clock (r[6] = outcome_filled_at_ms), never the
        # candle clock — see the note above `_COST_PARITY_MS`.
        raw = float(row[10])  # type: ignore[arg-type]
        if not restate_cost_basis or int(row[6]) >= _COST_PARITY_MS:  # type: ignore[call-overload]
            return raw
        return restate_gross_r(
            raw,
            float(row[7]),  # type: ignore[arg-type]
            float(row[8]),  # type: ignore[arg-type]
        )

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
            realized_r=_r(r),
        )
        for r in rows
    ]
    return book_from_trades(conn, cfg, trades)
