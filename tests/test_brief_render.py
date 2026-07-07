"""Renderer: formatting rules + end-to-end byte-stability."""

import json
from pathlib import Path

import duckdb

from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig
from analytics.brief.render import fmt_dist, fmt_frac, fmt_price, render_markdown
from tests._brief_fixtures import DAY_MS, START_MS, make_conn, seed_symbol

AS_OF = START_MS + 60 * DAY_MS


def test_fmt_price_tiers() -> None:
    assert fmt_price(61420.4) == "61,420"
    assert fmt_price(101.234) == "101.23"
    assert fmt_price(0.12345) == "0.1235"


def test_fmt_dist_and_frac() -> None:
    assert fmt_dist(0.256) == "+0.26"
    assert fmt_dist(-1.164) == "-1.16"
    assert fmt_frac(0.58) == "58%"


def _seeded_cfg(tmp_path: Path) -> tuple[BriefConfig, duckdb.DuckDBPyConnection]:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    ledger = tmp_path / "calls.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "author": "alice",
                "symbol": "BTCUSDT",
                "direction": "long",
                "call_ts_utc": "2024-02-28T00:00:00Z",
                "entry": "zone",
                "target": "moon",
                "horizon": "swing",
            }
        )
    )
    cfg = BriefConfig(
        symbols=("BTCUSDT",),
        as_of_ms=AS_OF,
        stats_days=60,
        ledger_path=ledger,
        priors_path=tmp_path / "missing-priors.json",
    )
    return cfg, conn


def test_render_end_to_end_byte_stable(tmp_path: Path) -> None:
    cfg, conn = _seeded_cfg(tmp_path)
    out1 = render_markdown(compute_brief(conn, cfg))
    out2 = render_markdown(compute_brief(conn, cfg))
    assert out1 == out2
    assert "BUIBUI DAILY BRIEF" in out1
    assert "── BTCUSDT" in out1
    assert "Levels   above →" in out1
    assert "── PUNDIT BOARD" in out1
    assert "run make buibui-pundit-score" in out1  # absent priors note
    assert "── HEALTH ──" in out1
    assert "alice" in out1


def _seeded_cfg_with_priors(
    tmp_path: Path, priors_text: str
) -> tuple[BriefConfig, duckdb.DuckDBPyConnection]:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    ledger = tmp_path / "calls.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "author": "alice",
                "symbol": "BTCUSDT",
                "direction": "long",
                "call_ts_utc": "2024-02-28T00:00:00Z",
                "entry": "zone",
                "target": "moon",
                "horizon": "swing",
            }
        )
    )
    priors = tmp_path / "priors.json"
    priors.write_text(priors_text)
    cfg = BriefConfig(
        symbols=("BTCUSDT",),
        as_of_ms=AS_OF,
        stats_days=60,
        ledger_path=ledger,
        priors_path=priors,
    )
    return cfg, conn


_PRIORS_TWO_EACH = json.dumps(
    {
        "generated_at": "2024-02-01T00:00:00Z",
        "policy": {"min_n_marker": 5},
        "authors": {
            "alice": {"n": 12, "hit_rate": 0.58, "avg_r": 0.34, "avg_atr_r": 1.2},
            "bob": {"n": 9, "hit_rate": 0.44, "avg_r": -0.11, "avg_atr_r": 0.8},
        },
        "families": {
            "breakout": {
                "long": {"n": 20, "hit_rate": 0.55, "avg_r": 0.22, "avg_atr_r": 1.1}
            },
            # avg_r null (no resolved calls) — must render a placeholder, not "None".
            "reclaim": {
                "short": {"n": 14, "hit_rate": 0.5, "avg_r": None, "avg_atr_r": 0.9}
            },
        },
    }
)


def test_render_authors_and_families_blocks(tmp_path: Path) -> None:
    cfg, conn = _seeded_cfg_with_priors(tmp_path, _PRIORS_TWO_EACH)
    out = render_markdown(compute_brief(conn, cfg))
    # Both blocks render.
    assert "Top authors (by n):" in out
    assert "Top families (by n):" in out
    # Authors: full n / hit_rate / avg_r / avg_atr_r set, signed avg_r + atr_r.
    assert "  alice  n=12 · 58% · +0.34R · +1.2 ATR-R" in out
    assert "  bob  n=9 · 44% · -0.11R · +0.8 ATR-R" in out
    # Sorted by n desc: alice (12) before bob (9).
    assert out.index("  alice  n=12") < out.index("  bob  n=9")
    # Families now show avg_r too.
    assert "  breakout/long  n=20 · 55% · +0.22R · +1.1 ATR-R" in out
    # None family avg_r renders the placeholder, never the literal "None".
    assert "  reclaim/short  n=14 · 50% · —R · +0.9 ATR-R" in out
    assert "None" not in out


def test_render_authors_block_byte_stable(tmp_path: Path) -> None:
    cfg, conn = _seeded_cfg_with_priors(tmp_path, _PRIORS_TWO_EACH)
    out1 = render_markdown(compute_brief(conn, cfg))
    out2 = render_markdown(compute_brief(conn, cfg))
    assert out1 == out2
    assert "Top authors (by n):" in out1


def test_render_priors_age_none_guard(tmp_path: Path) -> None:
    # Valid priors JSON but no generated_at → status "ok", priors_age_days None.
    priors_text = json.dumps(
        {"authors": {"alice": {"n": 6}}, "policy": {"min_n_marker": 5}}
    )
    cfg, conn = _seeded_cfg_with_priors(tmp_path, priors_text)
    out = render_markdown(compute_brief(conn, cfg))
    assert "priors ?d old" in out  # header fallback, not "priors Noned"
    assert "priors ?d" in out  # health-footer fallback
    assert "Noned" not in out
    assert "None" not in out


def test_render_error_panel(tmp_path: Path) -> None:
    cfg, conn = _seeded_cfg(tmp_path)
    cfg2 = BriefConfig(
        symbols=("NODATAUSDT",),
        as_of_ms=AS_OF,
        stats_days=60,
        ledger_path=cfg.ledger_path,
        priors_path=cfg.priors_path,
    )
    out = render_markdown(compute_brief(conn, cfg2))
    assert "ERROR: " in out
    assert "data ⚠" in out
