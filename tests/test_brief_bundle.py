"""Orchestrator: panel assembly, per-symbol isolation, determinism."""

from pathlib import Path

import pandas as pd
import pytest

from analytics.brief._common import TF_MS
from analytics.brief.bundle import _resolve_ref_price, compute_brief
from analytics.brief.config import BriefConfig
from tests._brief_fixtures import DAY_MS, START_MS, brief_cfg, make_conn, seed_symbol

AS_OF = START_MS + 60 * DAY_MS
H1_MS = TF_MS["1h"]


def _cfg(symbols: tuple[str, ...]) -> BriefConfig:
    return brief_cfg(symbols, AS_OF)


def test_compute_brief_happy_path() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    assert bundle.as_of_ms == AS_OF
    panel = bundle.panels[0]
    assert panel.error is None
    assert panel.symbol == "BTCUSDT"
    assert panel.ref_close > 0 and panel.atr14 > 0
    assert panel.regime_1d in ("trend", "range", "high_vol", "unknown")
    assert panel.levels_above or panel.levels_below
    assert panel.seasonality is not None
    assert bundle.health.data_ok is True


def test_compute_brief_symbol_isolation() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT", "NODATAUSDT")))
    ok, bad = bundle.panels
    assert ok.error is None
    assert bad.symbol == "NODATAUSDT" and bad.error is not None
    assert "insufficient 1d history" in bad.error


def test_compute_brief_deterministic() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    assert compute_brief(conn, _cfg(("BTCUSDT",))) == compute_brief(
        conn, _cfg(("BTCUSDT",))
    )


def test_compute_brief_extra_notes_flow_to_health() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)), extra_notes=["fallback"])
    # 60 seeded days is short of the weekly cone's 14-complete-prior-weeks
    # floor, so the M5 weekly block degrades to its own health note (added
    # after "fallback" in this fixture — see analytics/brief/weekly.py).
    # AS_OF (START_MS + 60d) also lands exactly on the 2024-03-01 month
    # boundary, so the fixture's last completed daily bar (Feb 29) falls in
    # the PRIOR month — the current month has zero completed bars, and the
    # M5 monthly block (Task 5) degrades to its own note too, appended after
    # the weekly one — see analytics/brief/monthly.py.
    # The ST54 bear score needs 50 CLOSED weeks; 60 seeded days cannot reach
    # any of the six averages, so it degrades to a note naming every one of
    # them rather than reporting a score computed from a subset. A partial
    # score would be a different statistic wearing the same label.
    assert bundle.health.notes == [
        "fallback",
        "BTCUSDT: weekly cone: no forming-week path (short history?)",
        "BTCUSDT: monthly context: no bars in the current month",
        "cycle: not enough history for "
        "50W SMA, 50W EMA, 200D EMA, 200D SMA, 20W SMA, 21W EMA",
    ]


def _h1_frame(last_open_ms: int, n: int = 5, close: float = 101.0) -> pd.DataFrame:
    rows = [
        {
            "open_time": last_open_ms - i * H1_MS,
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": close,
        }
        for i in reversed(range(n))
    ]
    return pd.DataFrame(rows)


def _d1_frame(as_of_ms: int, forming: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(daily, completed_1d): 3 completed days, optional forming bar at as_of."""
    day = 86_400_000
    day_start = (as_of_ms // day) * day
    rows = [
        {
            "open_time": day_start - i * day,
            "open": 100.0,
            "high": 103.0,
            "low": 97.0,
            "close": 100.5,
        }
        for i in reversed(range(1, 4))
    ]
    completed = pd.DataFrame(rows)
    if not forming:
        return completed.copy(), completed
    f = {
        "open_time": day_start,
        "open": 100.5,
        "high": 104.0,
        "low": 100.0,
        "close": 103.5,
    }
    return pd.concat([completed, pd.DataFrame([f])], ignore_index=True), completed


def test_resolve_ref_price_fresh_1h() -> None:
    as_of = AS_OF + 12 * H1_MS
    h1 = _h1_frame(last_open_ms=as_of - H1_MS)  # closes exactly at as_of
    daily, completed = _d1_frame(as_of, forming=True)
    price, ts, source = _resolve_ref_price(h1, daily, completed, as_of)
    assert source == "1h"
    assert price == 101.0
    assert ts == as_of - H1_MS


def test_resolve_ref_price_stale_1h_falls_to_forming() -> None:
    as_of = AS_OF + 12 * H1_MS
    h1 = _h1_frame(last_open_ms=as_of - 4 * H1_MS)  # closed 3h ago → stale
    daily, completed = _d1_frame(as_of, forming=True)
    price, ts, source = _resolve_ref_price(h1, daily, completed, as_of)
    assert source == "1d_forming"
    assert price == 103.5


def test_resolve_ref_price_no_1h_no_forming() -> None:
    as_of = AS_OF + 12 * H1_MS
    daily, completed = _d1_frame(as_of, forming=False)
    price, ts, source = _resolve_ref_price(pd.DataFrame(), daily, completed, as_of)
    assert source == "1d_close"
    assert price == 100.5


def test_bundle_uses_1h_ref_price_and_flags_source() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT")
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    panel = bundle.panels[0]
    assert panel.ref_price_source == "1h"
    assert not any("ref price" in n for n in bundle.health.notes)


def test_bundle_falls_back_without_1h_and_notes_it() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT")
    conn.execute("DELETE FROM ohlcv_all WHERE venue = 'binance' AND timeframe = '1h'")
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    panel = bundle.panels[0]
    assert panel.ref_price_source == "1d_close"
    assert any("ref price" in n for n in bundle.health.notes)


def test_panel_has_indicator_state() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    panel = bundle.panels[0]
    assert panel.error is None
    assert panel.indicators is not None
    # 60 seeded days: EMA20/50 available, EMA200 not.
    assert panel.indicators.ema is not None
    assert panel.indicators.ema.above_20 is not None
    assert panel.indicators.ema.above_200 is None
    assert panel.indicators.range_state is not None
    assert panel.indicators.candles is not None
    assert panel.indicators.vwap is not None
    assert panel.indicators.profile is not None


def test_indicators_deterministic() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    first = compute_brief(conn, _cfg(("BTCUSDT",)))
    second = compute_brief(conn, _cfg(("BTCUSDT",)))
    assert first.panels[0].indicators == second.panels[0].indicators


def test_bundle_session_clock() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    clock = bundle.session_clock
    assert clock is not None
    assert clock.label == "Asia"  # 08:00 MYT — Asia open boundary
    assert clock.start_ms == AS_OF
    assert clock.end_ms == AS_OF + 6 * H1_MS
    assert clock.next_label == "London"
    assert clock.is_overlap is False


def test_panel_has_session_state() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    panel = bundle.panels[0]
    assert panel.error is None
    assert panel.sessions is not None
    recap = panel.sessions.recap
    assert recap is not None
    assert [r.session for r in recap] == ["Asia", "London", "NY"]
    assert [r.n_bars for r in recap] == [r.expected_bars for r in recap]
    assert sum(r.made_set_high for r in recap) == 1
    assert sum(r.made_set_low for r in recap) == 1
    tendency = panel.sessions.tendency
    assert tendency is not None and len(tendency) == 3


def test_error_panel_has_no_sessions() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT", "NODATAUSDT")))
    assert bundle.panels[1].sessions is None


def test_tendency_failure_keeps_recap_and_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # compute_session_breakdown raising must not sink the panel: the recap
    # half still renders, tendency drops to None, and a health note lands.
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)

    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("tendency exploded")

    monkeypatch.setattr("analytics.brief.bundle.compute_session_breakdown", boom)
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    panel = bundle.panels[0]
    assert panel.error is None
    assert panel.sessions is not None
    assert panel.sessions.recap is not None  # recap survives
    assert panel.sessions.tendency is None  # tendency dropped
    assert any("session tendency failed" in n for n in bundle.health.notes)


def test_external_notes_flow_prefixed_through_bundle(tmp_path: Path) -> None:
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "junk.json").write_text("{not json")
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, brief_cfg(("BTCUSDT",), AS_OF, external_dir=ext))
    assert any(
        n.startswith("BTCUSDT: external: unreadable junk.json")
        for n in bundle.health.notes
    )
