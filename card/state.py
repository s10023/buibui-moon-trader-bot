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
from portfolio.sizing import SizingConfig, resolve_capital

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
    # daily_pnl_usd / (resolved capital * r_base); see portfolio.sizing.resolve_capital
    daily_r: float
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
    # now_ms is threaded so --as-of stays reproducible: without it the window
    # would be cut from the wall clock and a past-dated card would cite live
    # outcomes recorded after its own as-of date.
    live = {
        (c.strategy, c.tf, c.direction): c
        for c in compute_live_outcomes(
            conn, days=cfg.live_window_days, min_n=1, now_ms=now_ms
        ).cells
    }
    fires: list[RecentFire] = []
    for tf in cfg.fires_timeframes:
        tf_ms = parse_timeframe_secs(tf) * 1000
        span_ms = tf_ms * cfg.fires_lookback_bars
        # The end bound is a bar CLOSE, not a bar open: `signals` rows are keyed
        # by open_time and the daemon writes one only after the bar closes, so
        # bounding at now_ms admits a bar still forming at the anchor — a card
        # dated T citing a fire only knowable after T. Look-ahead, not just
        # irreproducibility.
        df = get_signals_history(conn, symbol, tf, now_ms - span_ms, now_ms - tf_ms)
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
    account_skip_reason: str | None = None,
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
        # Deliberately `sizing.capital`, not `resolve_capital(sizing, equity)`
        # — a third consumer of the capital constant, beside sizing (card.py)
        # and the circuit breaker (below). The XS target row is sized on the
        # sleeve's own pinned capital, not the card's live equity, and when
        # a dated snapshot exists under `docs/plans/xsmom_targets/` it is read
        # verbatim (see `_xs_block`), bypassing this argument entirely.
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
        # `account_skip_reason` distinguishes a DELIBERATE omission (a pinned
        # --as-of run, which cannot freeze a live account) from a credentials
        # or network failure. Both leave account=None, and a reader with only
        # the generic note cannot tell which happened.
        health.append(f"account: {account_skip_reason or 'no provider (degraded)'}")
    else:
        try:
            day_start = now_ms - (now_ms % DAY_MS)
            pnl = account_provider.daily_pnl_usd(day_start, now_ms)
            equity = account_provider.equity_usd()
            capital, _used_live = resolve_capital(sizing, equity)
            # Guard the divisor rather than trusting it: r_base is operator-set
            # and a zero here would kill state building outright.
            r_unit = capital * sizing.r_base
            if r_unit > 0.0:
                daily_r = pnl / r_unit
            else:
                # Fail-open on the value but never on the SIGNAL: 0.0 reads as
                # "no loss" to the breaker at card.py:249, so a silent 0.0 would
                # be indistinguishable from a flat day. `render_card` never
                # prints `state.health` and `FinalCard` has no `health` field,
                # so this note reaches the state JSON, the LLM prompt
                # (`prompt.py`) and `--dry-run` output — never the rendered
                # card or the `ai-cards.jsonl` ledger. Surfacing it as a card
                # warning too is a filed follow-up, not yet done. Within that
                # reach, it is what keeps a misconfigured [portfolio]
                # capital/r_base from looking safe.
                daily_r = 0.0
                health.append(
                    "daily_r unavailable: non-positive risk unit "
                    "(capital x r_base) — the daily-loss circuit breaker "
                    "cannot fire this run"
                )
            account = AccountState(
                positions=account_provider.positions(),
                daily_pnl_usd=pnl,
                daily_r=daily_r,
                equity_usd=equity,
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
