"""MarketState dataclasses, to_dict determinism, digest."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from analytics.brief.types import (
    BriefBundle,
    HealthReport,
    PunditAuthorPrior,
    PunditBoard,
    PunditCallRow,
    PunditFamilyPrior,
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
    _fires_block,
    _xs_block,
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
                    live_n=7,
                    live_avg_r=-0.31,
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


def _prior(author: str) -> PunditAuthorPrior:
    return PunditAuthorPrior(
        author=author,
        n=35,
        hit_rate=0.11,
        avg_r=-1.0,
        avg_atr_r=-0.334,
        flagged=False,
        r_coverage=0.778,
    )


def _board_with_stats() -> PunditBoard:
    """A board carrying `avg_r` in all THREE places it can hide."""
    return PunditBoard(
        priors_status="ok",
        priors_age_days=0,
        min_n_marker=30,
        ledger_status="ok",
        ledger_total=235,
        ledger_skipped=0,
        recent_calls=[
            PunditCallRow(
                author="traderfengge",
                symbol="BTCUSDT",
                direction="short",
                entry="64000",
                target="62000",
                horizon="intraday",
                age_days=1,
                on_panel=True,
                prior=_prior("traderfengge"),
            )
        ],
        authors=[_prior("traderfengge")],
        families=[
            PunditFamilyPrior(
                family="sweep_reclaim",
                direction="long",
                n=18,
                hit_rate=0.857,
                avg_r=2.367,
                avg_atr_r=1.104,
                flagged=False,
                r_coverage=0.5,
            )
        ],
    )


class TestPunditAvgRStrippedFromCardPayload:
    """The card must not see the winner-censored per-author `avg_r`.

    Three authors compressed to an identical -1.0 on 2026-08-12 while their
    complete-sample `avg_atr_r` read -0.334 / -0.502 / -1.331, and all three
    cards in that batch cited the censored number.
    """

    def _payload(self) -> dict:
        return _minimal_state(
            pundit=_board_with_stats(),
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
                    live_n=7,
                    live_avg_r=-0.31,
                )
            ],
        ).to_dict()

    def test_avg_r_gone_from_every_pundit_cell(self) -> None:
        board = self._payload()["pundit"]
        cells = [
            board["authors"][0],
            board["families"][0],
            board["recent_calls"][0]["prior"],
        ]
        for cell in cells:
            assert "avg_r" not in cell
            # r_coverage exists ONLY to qualify avg_r, so it goes too.
            assert "r_coverage" not in cell

    def test_avg_atr_r_survives_in_every_pundit_cell(self) -> None:
        """Stripping must not take the clean metric with it."""
        board = self._payload()["pundit"]
        assert board["authors"][0]["avg_atr_r"] == pytest.approx(-0.334)
        assert board["families"][0]["avg_atr_r"] == pytest.approx(1.104)
        assert board["recent_calls"][0]["prior"]["avg_atr_r"] == pytest.approx(-0.334)
        assert board["authors"][0]["n"] == 35
        assert board["authors"][0]["hit_rate"] == pytest.approx(0.11)

    def test_recent_fires_avg_r_is_untouched(self) -> None:
        """THE discrimination test — without it the strip could be global.

        `recent_fires.avg_r` is a DIFFERENT metric that shares the name: the
        backtest star figure over simulated trades that all have stops. It is
        not censored, and rubric 3a depends on it.
        """
        payload = self._payload()
        assert payload["recent_fires"][0]["avg_r"] == pytest.approx(0.12)
        assert payload["recent_fires"][0]["live_avg_r"] == pytest.approx(-0.31)

    def test_absent_board_is_not_an_error(self) -> None:
        assert _minimal_state(pundit=None).to_dict()["pundit"] is None


_NOW_MS = 1_760_000_000_000
# A 1h bar that has CLOSED at _NOW_MS. Fires are admitted on their bar's close,
# so a fixture seeded inside the still-forming bar (_NOW_MS - 1_000_000) is
# invisible to _fires_block BY DESIGN — name it rather than open-code the
# offset, so a future fixture cannot reintroduce the look-ahead by accident.
_CLOSED_1H_OPEN_MS = _NOW_MS - 3_600_000 - 1_000_000


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
            f"VALUES ('BTCUSDT', '1h', 'fvg', {_CLOSED_1H_OPEN_MS}, 'long', "
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
        # account: daily_r = -50 / (9_000 * 0.0025) = -2.2222, resolved off the
        # provider's live equity rather than the 10_000.0 config constant.
        assert state.account is not None
        assert state.account.daily_r == pytest.approx(-2.2222222222, abs=1e-9)
        assert state.account.positions[0].side == "short"

    def test_daily_r_scales_off_live_equity_not_the_config_constant(self) -> None:
        """Positive control: this pnl breaches the breaker at real equity only.

        -7.0 USD against equity 1201.33 (R unit 3.0033) is -2.331R and
        genuinely breaches daily_loss_limit_r -2.0; against the 10_000.0
        constant (R unit 25.00) the same day is -0.28R and passes. Asserting
        BOTH halves is what proves the stimulus is live rather than that an
        invariant happened to hold anyway.
        """

        class TinyEquityProvider:
            def positions(self) -> list:
                return []

            def daily_pnl_usd(self, start_ms: int, end_ms: int) -> float:
                return -7.0

            def equity_usd(self) -> float | None:
                return 1201.33

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        state = snapshot_market_state(
            conn,
            "BTCUSDT",
            CardConfig(),
            SizingConfig(),
            now_ms=_NOW_MS,
            account_provider=TinyEquityProvider(),
            brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
            targets_fn=lambda *a, **k: None,
        )

        assert state.account is not None
        assert state.account.daily_r == pytest.approx(-7.0 / (1201.33 * 0.0025))
        assert state.account.daily_r <= -2.0
        # the shipped behaviour would have been nowhere near the breaker:
        # -7.0 / (10_000.0 * 0.0025) = -0.28R, well clear of -2.0. This is
        # arithmetic on literals, not the system under test — it documents
        # the contrast, not a check of anything. The two asserts above are
        # the real positive control.

    def test_daily_r_falls_back_to_config_capital_without_equity(self) -> None:
        class NoEquityProvider:
            def positions(self) -> list:
                return []

            def daily_pnl_usd(self, start_ms: int, end_ms: int) -> float:
                return -7.0

            def equity_usd(self) -> float | None:
                return None

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        state = snapshot_market_state(
            conn,
            "BTCUSDT",
            CardConfig(),
            SizingConfig(),
            now_ms=_NOW_MS,
            account_provider=NoEquityProvider(),
            brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
            targets_fn=lambda *a, **k: None,
        )

        assert state.account is not None
        assert state.account.daily_r == pytest.approx(-0.28)

    def test_daily_r_degenerate_risk_unit_surfaces_a_health_note(self) -> None:
        """A non-positive risk unit fails open on the VALUE but not the SIGNAL.

        daily_r=0.0 reads as "no loss" to the breaker at card.py:249, so a
        silent 0.0 would be indistinguishable from a genuinely flat day. This
        pins that the health note exists — but the note only reaches the
        state JSON, the LLM prompt, and `--dry-run` output: `render_card`
        never prints `state.health` and `FinalCard` has no `health` field, so
        a misconfigured [portfolio] capital/r_base looking safe on the
        RENDERED card or the ledger is not something this note prevents
        (filed as a follow-up, not yet done).

        The fixture is a denormal, not the `capital=0.0` it used to be, and
        the swap is the whole point: `SizingConfig.__post_init__` now rejects
        a zero capital at construction, so the old fixture could no longer
        reach this branch. A denormal still can — 5e-324 is positive and
        finite, clears the guard, and `capital * r_base` UNDERFLOWS to exactly
        0.0. So the guard narrows this path without closing it, and this note
        is still load-bearing rather than dead defence.
        """

        class NoEquityProvider:
            def positions(self) -> list:
                return []

            def daily_pnl_usd(self, start_ms: int, end_ms: int) -> float:
                return -7.0

            def equity_usd(self) -> float | None:
                return None

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        state = snapshot_market_state(
            conn,
            "BTCUSDT",
            CardConfig(),
            SizingConfig(capital=5e-324),
            now_ms=_NOW_MS,
            account_provider=NoEquityProvider(),
            brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
            targets_fn=lambda *a, **k: None,
        )

        assert state.account is not None
        assert state.account.daily_r == 0.0
        assert any(
            n.startswith("daily_r unavailable: non-positive risk unit")
            for n in state.health
        )

    def _snapshot(self, conn: duckdb.DuckDBPyConnection) -> MarketState:
        return snapshot_market_state(
            conn,
            "BTCUSDT",
            CardConfig(),
            SizingConfig(),
            now_ms=_NOW_MS,
            account_provider=None,
            brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
            targets_fn=lambda *a, **k: None,
        )

    def test_account_skip_reason_surfaces_in_health(self) -> None:
        """A caller that deliberately withholds the provider says WHY.

        `--as-of` cannot pin the live account, so a pinned run omits it — and
        the state has to record that, or a reader cannot tell an intentional
        omission from a credentials failure.
        """
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        state = snapshot_market_state(
            conn,
            "BTCUSDT",
            CardConfig(),
            SizingConfig(),
            now_ms=_NOW_MS,
            account_provider=None,
            account_skip_reason="omitted under --as-of",
            brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
            targets_fn=lambda *a, **k: None,
        )
        assert state.account is None
        assert "account: omitted under --as-of" in state.health

    def test_account_skip_reason_defaults_to_the_degraded_note(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        state = self._snapshot(conn)
        assert "account: no provider (degraded)" in state.health

    def test_fires_exclude_a_bar_that_closes_after_the_anchor(self) -> None:
        """A fire is admitted on its bar's CLOSE, not its open — else look-ahead.

        Filtering by open_time lets in a bar that was still forming at the
        anchor, so a card dated T cites a signal only knowable after T.
        """
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        rows = [
            # closed at _NOW_MS - 1_000_000: legitimately visible.
            ("closed_bar", _CLOSED_1H_OPEN_MS),
            # opened 16.7 min before the anchor, closes 43.3 min AFTER it.
            ("open_bar", _NOW_MS - 1_000_000),
        ]
        for strategy, open_time in rows:
            conn.execute(
                "INSERT INTO signals (symbol, timeframe, strategy, open_time, "
                "direction, entry_price, sl_price, reason, confidence, "
                f"fired_at) VALUES ('BTCUSDT', '1h', '{strategy}', {open_time}, "
                "'long', 100.0, 99.0, 'r', 3, 0)"
            )
        state = self._snapshot(conn)
        assert [f.strategy for f in state.recent_fires] == ["closed_bar"]

    def test_state_digest_stable_across_a_candle_close(self) -> None:
        """A FIXED anchor survives the bar closing and its signal landing.

        The live defect was exactly this: the row was absent at 12:58Z and
        present at 13:15Z at one pinned anchor, because the daemon writes the
        signal only after the bar closes.
        """
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        conn.execute(
            "INSERT INTO signals (symbol, timeframe, strategy, open_time, "
            "direction, entry_price, sl_price, reason, confidence, fired_at) "
            f"VALUES ('BTCUSDT', '1h', 'fvg', {_CLOSED_1H_OPEN_MS}, "
            "'long', 100.0, 99.0, 'r', 3, 0)"
        )
        before = state_digest(self._snapshot(conn))

        # The daemon closes the 16.7-min-old bar and persists its signal.
        conn.execute(
            "INSERT INTO signals (symbol, timeframe, strategy, open_time, "
            "direction, entry_price, sl_price, reason, confidence, fired_at) "
            f"VALUES ('BTCUSDT', '1h', 'eqh_eql', {_NOW_MS - 1_000_000}, "
            f"'short', 100.0, 101.0, 'r', 3, {_NOW_MS + 2_600_000})"
        )
        assert state_digest(self._snapshot(conn)) == before

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


class TestXsBlock:
    def test_prefers_todays_snapshot_over_fresh_replay(self, tmp_path: Path) -> None:
        # When today's target snapshot exists, the card must read it (matching
        # what the executor saw) and never re-run replay_targets.
        date = pd.Timestamp(_NOW_MS, unit="ms", tz="UTC").date().isoformat()
        snap = tmp_path / f"{date}.json"
        snap.write_text(
            json.dumps(
                {
                    "governor": 1.1,
                    "as_of_date": date,
                    "positions": [
                        {"symbol": "BTCUSDT", "side": "long", "leverage": 0.5},
                        {"symbol": "ETHUSDT", "side": "flat"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        def _must_not_call(*_a: object, **_k: object) -> object:
            raise AssertionError("targets_fn called despite snapshot present")

        conn = duckdb.connect(":memory:")  # unused on the snapshot branch
        out = _xs_block(
            conn, "BTCUSDT", 10_000.0, _NOW_MS, str(tmp_path), _must_not_call
        )
        assert out is not None
        assert out["side"] == "long"
        assert out["governor"] == 1.1
        assert out["as_of_date"] == date

    def test_symbol_absent_from_snapshot_returns_none(self, tmp_path: Path) -> None:
        date = pd.Timestamp(_NOW_MS, unit="ms", tz="UTC").date().isoformat()
        snap = tmp_path / f"{date}.json"
        snap.write_text(
            json.dumps({"governor": 1.0, "positions": []}), encoding="utf-8"
        )
        conn = duckdb.connect(":memory:")  # unused on the snapshot branch
        assert (
            _xs_block(
                conn, "BTCUSDT", 10_000.0, _NOW_MS, str(tmp_path), lambda *a, **k: None
            )
            is None
        )


class TestFiresBlock:
    def test_falls_back_to_combined_rating_when_no_directional(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        conn.execute(
            "INSERT INTO signals (symbol, timeframe, strategy, open_time, "
            "direction, entry_price, sl_price, reason, confidence, fired_at) "
            f"VALUES ('BTCUSDT', '1h', 'fvg', {_CLOSED_1H_OPEN_MS}, 'long', "
            "100.0, 99.0, 'r', 3, 0)"
        )
        # only a 'combined' rating exists — no ('fvg','1h','long') row
        conn.execute(
            "INSERT INTO confidence_ratings "
            "(config_name, strategy, tf, direction, stars, avg_r, win_rate, "
            "updated_at_ms, day_filter, dsr) "
            "VALUES ('signal_watch', 'fvg', '1h', 'combined', 2, 0.05, 0.5, "
            "0, NULL, 0.8)"
        )
        fires = _fires_block(
            conn, "BTCUSDT", CardConfig(fires_timeframes=("1h",)), _NOW_MS
        )
        assert len(fires) == 1
        assert fires[0].direction == "long"
        assert fires[0].stars == 2  # from the combined fallback
        assert fires[0].avg_r == pytest.approx(0.05)


def _insert_fire(conn: duckdb.DuckDBPyConnection) -> None:
    """One 'fvg'/'1h'/'long' signal inside the card's lookback window."""
    conn.execute(
        "INSERT INTO signals (symbol, timeframe, strategy, open_time, "
        "direction, entry_price, sl_price, reason, confidence, fired_at) "
        f"VALUES ('BTCUSDT', '1h', 'fvg', {_CLOSED_1H_OPEN_MS}, 'long', "
        "100.0, 99.0, 'r', 3, 0)"
    )


def _insert_resolved(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_id: str,
    outcome: str,
    outcome_r: float,
    symbol: str = "BTCUSDT",
    strategy: str = "fvg",
    tf: str = "1h",
    direction: str = "long",
    fired_at_ms: int | None = None,
) -> None:
    """One resolved live-ledger row."""
    fired = _NOW_MS - 2_000_000 if fired_at_ms is None else fired_at_ms
    conn.execute(
        "INSERT INTO signal_alert_outcomes (signal_id, symbol, tf, strategy, "
        "direction, fired_at_ms, candle_ts_ms, entry_price, sl_price, "
        "tp_price, outcome, outcome_r, outcome_filled_at_ms) VALUES "
        f"('{signal_id}', '{symbol}', '{tf}', '{strategy}', '{direction}', "
        f"{fired}, {fired}, 100.0, 99.0, 103.0, "
        f"'{outcome}', {outcome_r}, {fired + 500_000})"
    )


class TestFiresLiveOutcomes:
    """The live ledger is a second, independent quality channel on each fire.

    Backtest ratings come from `backtest_trades` via recalibrate; they can be
    strongly positive on a cell the live ledger says loses money. The card
    must carry both so the rubric can see the contradiction.
    """

    def test_live_record_annotated_alongside_backtest_rating(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_fire(conn)
        conn.execute(
            "INSERT INTO confidence_ratings "
            "(config_name, strategy, tf, direction, stars, avg_r, win_rate, "
            "updated_at_ms, day_filter, dsr) "
            "VALUES ('signal_watch', 'fvg', '1h', 'long', 5, 0.95, 0.6, "
            "0, NULL, 0.99)"
        )
        # live ledger disagrees: two losses on the same cell
        _insert_resolved(conn, signal_id="a", outcome="loss", outcome_r=-1.0)
        _insert_resolved(conn, signal_id="b", outcome="loss", outcome_r=-0.6)

        fires = _fires_block(
            conn, "BTCUSDT", CardConfig(fires_timeframes=("1h",)), _NOW_MS
        )

        assert len(fires) == 1
        assert fires[0].avg_r == pytest.approx(0.95)  # backtest, unchanged
        assert fires[0].live_n == 2
        assert fires[0].live_avg_r == pytest.approx(-0.8)

    def test_live_window_is_anchored_to_the_cards_clock(self) -> None:
        """`--as-of` must not cite outcomes recorded after its own date.

        End-to-end guard for the wall-clock coupling that the non-zero
        `live_window_days` default introduced: the card composes its panel as
        of `now_ms`, so its live annotation has to obey the same clock. Here
        the only in-window row is the pre-as-of one; the row 30 days later is
        real, resolved, on the same cell, and must still be invisible.
        """
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_fire(conn)
        _insert_resolved(
            conn,
            signal_id="past",
            outcome="loss",
            outcome_r=-1.0,
            fired_at_ms=_NOW_MS - 2_000_000,
        )
        _insert_resolved(
            conn,
            signal_id="after",
            outcome="win",
            outcome_r=3.0,
            fired_at_ms=_NOW_MS + 30 * 86_400_000,
        )

        fires = _fires_block(
            conn,
            "BTCUSDT",
            CardConfig(fires_timeframes=("1h",), live_window_days=60),
            _NOW_MS,
        )

        assert fires[0].live_n == 1, "post-as-of outcome leaked into the card"
        assert fires[0].live_avg_r == pytest.approx(-1.0)

    def test_live_record_is_cross_symbol(self) -> None:
        """Parity with the star: recalibrate pools symbols, so this must too."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_fire(conn)
        _insert_resolved(conn, signal_id="a", outcome="loss", outcome_r=-1.0)
        _insert_resolved(
            conn, signal_id="b", outcome="win", outcome_r=3.0, symbol="ETHUSDT"
        )

        fires = _fires_block(
            conn, "BTCUSDT", CardConfig(fires_timeframes=("1h",)), _NOW_MS
        )

        assert fires[0].live_n == 2
        assert fires[0].live_avg_r == pytest.approx(1.0)

    def test_no_live_history_leaves_fields_none(self) -> None:
        """An unfired-live cell must read as absent, never as zero."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_fire(conn)

        fires = _fires_block(
            conn, "BTCUSDT", CardConfig(fires_timeframes=("1h",)), _NOW_MS
        )

        assert fires[0].live_n is None
        assert fires[0].live_avg_r is None

    def test_live_lookup_is_direction_scoped(self) -> None:
        """The short record must not leak onto a long fire."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_fire(conn)
        _insert_resolved(
            conn, signal_id="a", outcome="loss", outcome_r=-1.0, direction="short"
        )

        fires = _fires_block(
            conn, "BTCUSDT", CardConfig(fires_timeframes=("1h",)), _NOW_MS
        )

        assert fires[0].direction == "long"
        assert fires[0].live_n is None
