"""Orchestrator: panel assembly, per-symbol isolation, determinism."""

from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig
from tests._brief_fixtures import DAY_MS, START_MS, make_conn, seed_symbol

AS_OF = START_MS + 60 * DAY_MS


def _cfg(symbols: tuple[str, ...]) -> BriefConfig:
    return BriefConfig(symbols=symbols, as_of_ms=AS_OF, stats_days=60)


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
    assert bundle.health.notes == ["fallback"]
