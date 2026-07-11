"""G12 market-state serialiser: snapshot_market_state -> MarketState."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from analytics.brief.types import PunditBoard, SessionClock, SymbolPanel

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
