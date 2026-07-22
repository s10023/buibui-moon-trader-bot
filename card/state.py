"""G12 market-state serialiser: snapshot_market_state -> MarketState."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import duckdb
import pandas as pd

from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig
from analytics.brief.types import PunditBoard, SessionClock, SymbolPanel
from analytics.forecast.config import ForecastConfig
from analytics.signal._common import parse_timeframe_secs
from analytics.stats.live_outcomes import compute_live_outcomes
from analytics.store.confidence import get_confidence_rating_rows
from analytics.store.signals import get_signals_history
from analytics.xsmom.live import target_book_to_dict
from analytics.xsmom.replay import replay_targets
from card.config import CardConfig
from portfolio.sizing import SizingConfig

DAY_MS = 86_400_000


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    side: str  # "long" | "short"
    qty: float
    entry: float
    mark: float
    upnl_usd: float


@dataclass(frozen=True)
class AccountState:
    positions: list[OpenPosition]
    daily_pnl_usd: float
    daily_r: float  # daily_pnl_usd / (capital * r_base)
    equity_usd: float | None


class AccountProvider(Protocol):
    """Read-only live-account seam; the CLI injects the real one."""

    def positions(self) -> list[OpenPosition]: ...

    def daily_pnl_usd(self, start_ms: int, end_ms: int) -> float: ...

    def equity_usd(self) -> float | None: ...


@dataclass(frozen=True)
class RecentFire:
    strategy: str
    tf: str
    direction: str
    open_time: int
    entry_price: float
    stars: int | None
    avg_r: float | None
    win_rate: float | None
    dsr: float | None
    # Live-ledger record for the same cell — an independent second channel.
    # `avg_r`/`stars` above come from `backtest_trades` via recalibrate and can
    # be strongly positive on a cell the live ledger says loses money.
    live_n: int | None
    live_avg_r: float | None


@dataclass(frozen=True)
class MarketState:
    symbol: str
    now_ms: int
    direction_hint: str | None
    panel: SymbolPanel | None
    session_clock: SessionClock | None
    pundit: PunditBoard | None
    xs: dict[str, Any] | None
    recent_fires: list[RecentFire]
    account: AccountState | None
    health: list[str]

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (nested dataclasses flattened by asdict)."""
        return asdict(self)


def state_digest(state: MarketState) -> str:
    """sha256 of the canonical (sorted-keys) state JSON — the audit anchor."""
    return hashlib.sha256(
        json.dumps(state.to_dict(), sort_keys=True).encode()
    ).hexdigest()


BriefFn = Callable[..., Any]
TargetsFn = Callable[..., Any]


def _xs_block(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    capital: float,
    now_ms: int,
    targets_dir: str,
    targets_fn: TargetsFn,
) -> dict[str, Any] | None:
    """Symbol's XS target row + book governor; None when not in the book.

    Prefers today's gitignored snapshot (docs/plans/xsmom_targets/<date>.json)
    so the card matches what the executor saw; falls back to a fresh
    replay_targets computation.
    """
    now = pd.Timestamp(now_ms, unit="ms", tz="UTC")
    snap = Path(targets_dir) / f"{now.date().isoformat()}.json"
    if snap.exists():
        book: dict[str, Any] = json.loads(snap.read_text(encoding="utf-8"))
    else:
        book = target_book_to_dict(targets_fn(conn, ForecastConfig(), capital, now=now))
    row = next(
        (
            p
            for p in book.get("positions", [])
            if p.get("symbol") == symbol and p.get("side") != "flat"
        ),
        None,
    )
    if row is None:
        return None
    return {
        **row,
        "governor": book.get("governor"),
        "as_of_date": book.get("as_of_date"),
    }


def _fires_block(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    cfg: CardConfig,
    now_ms: int,
) -> list[RecentFire]:
    """Fired events in the last fires_lookback_bars per TF, quality-annotated.

    Two independent quality channels are attached per cell: the backtest star
    (`confidence_ratings`, from recalibrate) and the live record
    (`signal_alert_outcomes`). Both are cross-symbol so the pair is
    like-for-like — recalibrate pools symbols per (strategy, tf).
    """
    ratings = get_confidence_rating_rows(conn, cfg.ratings_config)
    live = {
        (c.strategy, c.tf, c.direction): c
        for c in compute_live_outcomes(conn, days=cfg.live_window_days, min_n=1).cells
    }
    fires: list[RecentFire] = []
    for tf in cfg.fires_timeframes:
        span_ms = parse_timeframe_secs(tf) * 1000 * cfg.fires_lookback_bars
        df = get_signals_history(conn, symbol, tf, now_ms - span_ms, now_ms)
        for row in df.to_dict("records"):
            r = ratings.get((str(row["strategy"]), tf, str(row["direction"])))
            if r is None:
                r = ratings.get((str(row["strategy"]), tf, "combined"))
            # No 'combined' fallback here: the live ledger stores a real
            # direction on every row, so a miss means no live history.
            lv = live.get((str(row["strategy"]), tf, str(row["direction"])))
            fires.append(
                RecentFire(
                    strategy=str(row["strategy"]),
                    tf=tf,
                    direction=str(row["direction"]),
                    open_time=int(row["open_time"]),
                    entry_price=float(row["entry_price"]),
                    stars=int(r["stars"]) if r and r["stars"] is not None else None,
                    avg_r=float(r["avg_r"]) if r and r["avg_r"] is not None else None,
                    win_rate=(
                        float(r["win_rate"])
                        if r and r["win_rate"] is not None
                        else None
                    ),
                    dsr=float(r["dsr"]) if r and r["dsr"] is not None else None,
                    live_n=lv.n if lv else None,
                    live_avg_r=(
                        float(lv.avg_r) if lv and lv.avg_r is not None else None
                    ),
                )
            )
    return fires


def snapshot_market_state(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    cfg: CardConfig,
    sizing: SizingConfig,
    *,
    now_ms: int,
    account_provider: AccountProvider | None,
    direction_hint: str | None = None,
    brief_fn: BriefFn = compute_brief,
    targets_fn: TargetsFn = replay_targets,
) -> MarketState:
    """Compose the full market state; one failing block never kills the card."""
    health: list[str] = []

    panel: SymbolPanel | None = None
    clock: SessionClock | None = None
    pundit: PunditBoard | None = None
    try:
        bundle = brief_fn(
            conn,
            BriefConfig(
                symbols=(symbol,),
                as_of_ms=now_ms,
                ledger_path=Path(cfg.pundit_calls_path),
                priors_path=Path(cfg.priors_path),
            ),
        )
        panel = bundle.panels[0] if bundle.panels else None
        clock = bundle.session_clock
        pundit = bundle.pundit
        if panel is not None and panel.error is not None:
            health.append(f"panel: {panel.error}")
    except Exception as exc:
        health.append(f"panel: {exc}")

    xs: dict[str, Any] | None = None
    try:
        xs = _xs_block(
            conn, symbol, sizing.capital, now_ms, cfg.targets_dir, targets_fn
        )
    except Exception as exc:
        health.append(f"xs: {exc}")

    fires: list[RecentFire] = []
    try:
        fires = _fires_block(conn, symbol, cfg, now_ms)
    except Exception as exc:
        health.append(f"recent_fires: {exc}")

    account: AccountState | None = None
    if account_provider is None:
        health.append("account: no provider (degraded)")
    else:
        try:
            day_start = now_ms - (now_ms % DAY_MS)
            pnl = account_provider.daily_pnl_usd(day_start, now_ms)
            account = AccountState(
                positions=account_provider.positions(),
                daily_pnl_usd=pnl,
                daily_r=pnl / (sizing.capital * sizing.r_base),
                equity_usd=account_provider.equity_usd(),
            )
        except Exception as exc:
            health.append(f"account: {exc}")

    return MarketState(
        symbol=symbol,
        now_ms=now_ms,
        direction_hint=direction_hint,
        panel=panel,
        session_clock=clock,
        pundit=pundit,
        xs=xs,
        recent_fires=fires,
        account=account,
        health=health,
    )
