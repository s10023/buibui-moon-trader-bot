"""Live-outcomes router — GET /api/live-outcomes.

Cross-symbol roll-up of the live ``signal_alert_outcomes`` ledger. Unlike the
per-symbol /api/stats/{symbol} bundle this aggregates across all symbols and is
never cached (the ledger changes as the daemon resolves trades).
"""

import time

import duckdb
from binance.client import Client
from fastapi import APIRouter, Depends, Query

from analytics.stats import compute_live_outcomes, mark_open_positions, open_positions
from web.api.deps import get_client, get_db, require_token
from web.api.models.live_outcomes import (
    LiveOpenPositionModel,
    LiveOpenPositionsResponse,
    LiveOutcomeCellModel,
    LiveOutcomesResponse,
    LiveOutcomesRollupModel,
    LiveOutcomeStrategyModel,
    LiveOutcomeSymbolModel,
)

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/live-outcomes", response_model=LiveOutcomesResponse)
def get_live_outcomes(
    days: int = Query(default=30, ge=0, le=365),
    min_n: int = Query(default=1, ge=1, le=100),
    symbol: str | None = Query(default=None),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> LiveOutcomesResponse:
    """Return the live signal-alert outcome roll-up + per-cell breakdowns.

    ``days`` windows the per-cell / per-strategy tables (0 = all time).
    ``symbol`` scopes the roll-up and both tables to one symbol; omitting it is
    the global view. An unknown symbol returns a zero roll-up, not 404. Empty
    ledger returns a zero roll-up, not 404.
    """
    result = compute_live_outcomes(db, days=days, min_n=min_n, symbol=symbol)
    return LiveOutcomesResponse(
        days=result.days,
        min_n=result.min_n,
        rollup=LiveOutcomesRollupModel(
            total_rows=result.rollup.total_rows,
            resolved=result.rollup.resolved,
            open=result.rollup.open,
            open_no_tp=result.rollup.open_no_tp,
            wins=result.rollup.wins,
            losses=result.rollup.losses,
            expired=result.rollup.expired,
        ),
        cells=[
            LiveOutcomeCellModel(
                strategy=c.strategy,
                tf=c.tf,
                direction=c.direction,
                n=c.n,
                wins=c.wins,
                losses=c.losses,
                expired=c.expired,
                win_rate=c.win_rate,
                avg_r=c.avg_r,
            )
            for c in result.cells
        ],
        by_strategy=[
            LiveOutcomeStrategyModel(
                strategy=s.strategy,
                n=s.n,
                wins=s.wins,
                losses=s.losses,
                expired=s.expired,
                win_rate=s.win_rate,
                avg_r=s.avg_r,
            )
            for s in result.by_strategy
        ],
        symbols=[
            LiveOutcomeSymbolModel(symbol=s.symbol, n=s.n) for s in result.symbols
        ],
    )


@router.get("/live-outcomes/open", response_model=LiveOpenPositionsResponse)
def get_live_outcomes_open(
    symbol: str | None = Query(default=None),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
    client: Client = Depends(get_client),
) -> LiveOpenPositionsResponse:
    """Return unresolved alerts marked to the current price.

    The mark-price call is best-effort: any failure sets ``marks_ok=False`` and
    returns every ledger row with null price columns. This route never 5xxs
    because the exchange is unreachable, rate-limited, or slow.
    """
    positions = open_positions(db, symbol=symbol)

    marks: dict[str, float] = {}
    marks_ok = True
    try:
        payload = client.futures_mark_price()
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = row.get("symbol")
            price = row.get("markPrice")
            if sym is None or price is None:
                continue
            try:
                marks[str(sym)] = float(price)
            except (TypeError, ValueError):
                continue
    except Exception:  # noqa: BLE001 — price feed is best-effort, never 5xx
        marks_ok = False
        marks = {}

    marked = mark_open_positions(positions, marks)

    return LiveOpenPositionsResponse(
        symbol=symbol,
        marks_ok=marks_ok,
        marked_at_ms=int(time.time() * 1000),
        positions=[
            LiveOpenPositionModel(
                signal_id=m.position.signal_id,
                symbol=m.position.symbol,
                strategy=m.position.strategy,
                tf=m.position.tf,
                direction=m.position.direction,
                fired_at_ms=m.position.fired_at_ms,
                entry_price=m.position.entry_price,
                sl_price=m.position.sl_price,
                tp_price=m.position.tp_price,
                mark=m.mark,
                unrealized_r=m.unrealized_r,
                dist_sl_pct=m.dist_sl_pct,
                dist_tp_pct=m.dist_tp_pct,
            )
            for m in marked
        ],
    )
