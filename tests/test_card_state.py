"""MarketState dataclasses, to_dict determinism, digest."""

from __future__ import annotations

import duckdb
import pytest

from analytics.brief.types import (
    BriefBundle,
    HealthReport,
    PunditBoard,
    error_panel,
)
from analytics.store.confidence import get_confidence_rating_rows
from analytics.store.schema import init_schema
from card.config import CardConfig
from card.state import (
    AccountState,
    MarketState,
    OpenPosition,
    RecentFire,
    snapshot_market_state,
    state_digest,
)
from portfolio.sizing import SizingConfig


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


_NOW_MS = 1_760_000_000_000


def _fake_bundle(symbol: str) -> BriefBundle:
    return BriefBundle(
        as_of_ms=_NOW_MS,
        day_ahead="Fri 2026-07-10",
        session_clock=None,
        panels=[error_panel(symbol, "no data")],
        pundit=PunditBoard(
            priors_status="absent",
            priors_age_days=None,
            min_n_marker=None,
            ledger_status="absent",
            ledger_total=0,
            ledger_skipped=0,
            recent_calls=[],
            authors=[],
            families=[],
        ),
        health=HealthReport(rows=[], notes=[], data_ok=False),
    )


class FakeProvider:
    def positions(self) -> list:
        return [
            OpenPosition(
                symbol="BTCUSDT",
                side="short",
                qty=0.5,
                entry=100.0,
                mark=99.0,
                upnl_usd=0.5,
            )
        ]

    def daily_pnl_usd(self, start_ms: int, end_ms: int) -> float:
        return -50.0

    def equity_usd(self) -> float | None:
        return 9_000.0


class TestConfidenceRatingRows:
    def test_rows_keyed_by_strategy_tf_direction(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        conn.execute(
            "INSERT INTO confidence_ratings "
            "(config_name, strategy, tf, direction, stars, avg_r, win_rate, "
            "updated_at_ms, day_filter, dsr) "
            "VALUES ('signal_watch', 'fvg', '4h', 'combined', 3, 0.12, 0.5, "
            "0, NULL, 0.9)"
        )
        rows = get_confidence_rating_rows(conn, "signal_watch")
        assert rows[("fvg", "4h", "combined")]["stars"] == 3
        assert rows[("fvg", "4h", "combined")]["avg_r"] == pytest.approx(0.12)
        assert get_confidence_rating_rows(conn, "other") == {}


class TestSnapshotMarketState:
    def test_composes_blocks_with_injected_seams(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        conn.execute(
            "INSERT INTO signals (symbol, timeframe, strategy, open_time, "
            "direction, entry_price, sl_price, reason, confidence, fired_at) "
            f"VALUES ('BTCUSDT', '1h', 'fvg', {_NOW_MS - 1_000_000}, 'long', "
            "100.0, 99.0, 'r', 3, 0)"
        )
        conn.execute(
            "INSERT INTO confidence_ratings "
            "(config_name, strategy, tf, direction, stars, avg_r, win_rate, "
            "updated_at_ms, day_filter, dsr) "
            "VALUES ('signal_watch', 'fvg', '1h', 'long', 4, 0.2, 0.6, 0, "
            "NULL, 0.96)"
        )
        state = snapshot_market_state(
            conn,
            "BTCUSDT",
            CardConfig(),
            SizingConfig(),
            now_ms=_NOW_MS,
            account_provider=FakeProvider(),
            direction_hint="long",
            brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
            targets_fn=lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("no 1d data")
            ),
        )
        assert state.symbol == "BTCUSDT"
        assert state.direction_hint == "long"
        # panel: error panel surfaced + health note
        assert state.panel is not None and state.panel.error == "no data"
        assert any("panel" in n for n in state.health)
        # pundit: the brief's PunditBoard, composed not re-parsed
        assert state.pundit is not None
        assert state.pundit.priors_status == "absent"
        # xs: builder raised -> None + health note (no snapshot file, targets_fn raises)
        assert state.xs is None
        assert any(n.startswith("xs:") for n in state.health)
        # fires: annotated from confidence_ratings (direction-specific row)
        assert len(state.recent_fires) == 1
        assert state.recent_fires[0].stars == 4
        assert state.recent_fires[0].dsr == pytest.approx(0.96)
        # account: daily_r = -50 / (10_000 * 0.0025) = -2.0
        assert state.account is not None
        assert state.account.daily_r == -2.0
        assert state.account.positions[0].side == "short"

    def test_no_provider_degrades_with_note(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        state = snapshot_market_state(
            conn,
            "BTCUSDT",
            CardConfig(),
            SizingConfig(),
            now_ms=_NOW_MS,
            account_provider=None,
            brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
            targets_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
        )
        assert state.account is None
        assert any(n.startswith("account:") for n in state.health)

    def test_as_of_determinism(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)

        def _snap() -> str:
            return state_digest(
                snapshot_market_state(
                    conn,
                    "BTCUSDT",
                    CardConfig(),
                    SizingConfig(),
                    now_ms=_NOW_MS,
                    account_provider=None,
                    brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
                    targets_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
                )
            )

        assert _snap() == _snap()
