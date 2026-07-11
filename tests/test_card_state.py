"""MarketState dataclasses, to_dict determinism, digest."""

from __future__ import annotations

from card.state import (
    AccountState,
    MarketState,
    OpenPosition,
    RecentFire,
    state_digest,
)


def _minimal_state(**overrides: object) -> MarketState:
    base: dict[str, object] = {
        "symbol": "BTCUSDT",
        "now_ms": 1_760_000_000_000,
        "direction_hint": None,
        "panel": None,
        "session_clock": None,
        "pundit": None,
        "xs": None,
        "recent_fires": [],
        "account": None,
        "health": [],
    }
    base.update(overrides)
    return MarketState(**base)  # type: ignore[arg-type]


class TestMarketState:
    def test_to_dict_is_json_safe_and_ordered(self) -> None:
        state = _minimal_state(
            recent_fires=[
                RecentFire(
                    strategy="fvg",
                    tf="4h",
                    direction="long",
                    open_time=1,
                    entry_price=100.0,
                    stars=3,
                    avg_r=0.12,
                    win_rate=0.5,
                    dsr=0.9,
                )
            ],
            account=AccountState(
                positions=[
                    OpenPosition(
                        symbol="BTCUSDT",
                        side="long",
                        qty=0.1,
                        entry=100.0,
                        mark=101.0,
                        upnl_usd=0.1,
                    )
                ],
                daily_pnl_usd=-5.0,
                daily_r=-0.2,
                equity_usd=1000.0,
            ),
        )
        d = state.to_dict()
        assert d["symbol"] == "BTCUSDT"
        assert d["recent_fires"][0]["strategy"] == "fvg"
        assert d["account"]["positions"][0]["side"] == "long"

    def test_digest_deterministic_and_input_sensitive(self) -> None:
        a = _minimal_state()
        b = _minimal_state()
        c = _minimal_state(symbol="ETHUSDT")
        assert state_digest(a) == state_digest(b)
        assert state_digest(a) != state_digest(c)
        assert len(state_digest(a)) == 64
