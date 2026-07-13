"""Renderer: formatting rules + end-to-end byte-stability."""

import json
from pathlib import Path

import duckdb

from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig
from analytics.brief.render import (
    _clock_line,
    _indicator_lines,
    _recap_bit,
    _session_lines,
    fmt_dist,
    fmt_frac,
    fmt_price,
    render_markdown,
)
from analytics.brief.types import (
    BbState,
    CandleHit,
    EmaState,
    IndicatorState,
    MondayState,
    PaState,
    ProfileState,
    RangeState,
    SessionClock,
    SessionRecapRow,
    SessionState,
    VwapState,
)
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


def test_render_last_price_label(tmp_path: Path) -> None:
    cfg, conn = _seeded_cfg(tmp_path)
    out = render_markdown(compute_brief(conn, cfg))
    assert "Last " in out
    assert "(1h close 00:00 UTC)" in out
    assert "Close " not in out  # old label gone


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


def _full_state() -> IndicatorState:
    return IndicatorState(
        ema=EmaState(
            above_20=True,
            above_50=True,
            above_200=False,
            stack="mixed",
            slope_200="falling",
        ),
        range_state=RangeState(
            label="range",
            since_ms=1_708_300_800_000,  # 2024-02-19 UTC
            bars=18,
            range_low=105200.0,
            range_high=112800.0,
            pos=0.62,
        ),
        monday=MondayState(state="inside", pos=0.43),
        candles=[
            CandleHit(pattern="doji", direction="short"),
            CandleHit(pattern="engulfing", direction="long"),
        ],
        pa=PaState(label="grind_up", er=0.55, speed_atr=0.4),
        bb=BbState(pct_b=0.71, bandwidth=0.083, bw_pctile=0.23, squeeze=False),
        vwap=VwapState(
            weekly_price=101.0,
            weekly_dist_atr=0.4,
            monthly_price=110.0,
            monthly_dist_atr=-1.2,
        ),
        profile=ProfileState(
            poc=108400.0,
            vah=113900.0,
            val=104100.0,
            vs_value="inside",
            poc_dist_atr=-0.3,
        ),
    )


class TestIndicatorLines:
    def test_full_block(self) -> None:
        lines = _indicator_lines(_full_state())
        assert lines == [
            "EMA      ▲20 ▲50 ▼200 · stack mixed · 200 falling",
            "State    range since 2024-02-19 (18 bars) · 105,200–112,800 · 62%",
            "Monday   inside (43%)",
            "Candle   doji·short, engulfing·long",
            "PA       grind_up · ER 0.55 · 0.40 ATR/bar",
            "BB       %B 0.71 · bw 8.3% (p23) | AVWAP W +0.40 · M -1.20",
            "VP60d    POC 108,400 (-0.30) · VA 104,100–113,900 · inside",
        ]

    def test_none_state_is_empty(self) -> None:
        assert _indicator_lines(None) == []

    def test_failed_blocks_drop_lines(self) -> None:
        state = IndicatorState(
            ema=None,
            range_state=None,
            monday=None,
            candles=[],
            pa=None,
            bb=None,
            vwap=None,
            profile=None,
        )
        assert _indicator_lines(state) == ["Candle   none"]

    def test_bb_half_survives_alone(self) -> None:
        state = IndicatorState(
            ema=None,
            range_state=None,
            monday=None,
            candles=None,
            pa=None,
            bb=BbState(pct_b=0.5, bandwidth=0.02, bw_pctile=None, squeeze=None),
            vwap=None,
            profile=None,
        )
        assert _indicator_lines(state) == ["BB       %B 0.50 · bw 2.0%"]

    def test_vwap_half_survives_alone_with_squeeze_variants(self) -> None:
        state = IndicatorState(
            ema=None,
            range_state=None,
            monday=None,
            candles=None,
            pa=None,
            bb=None,
            vwap=VwapState(
                weekly_price=None,
                weekly_dist_atr=None,
                monthly_price=100.0,
                monthly_dist_atr=0.8,
            ),
            profile=None,
        )
        assert _indicator_lines(state) == ["AVWAP    M +0.80"]

    def test_bb_survives_when_vwap_bit_empty(self) -> None:
        # vwap present but every distance None -> _vwap_bit "" -> dropped;
        # bb present alone renders "BB" (not the "BB | AVWAP" combined form).
        state = IndicatorState(
            ema=None,
            range_state=None,
            monday=None,
            candles=None,
            pa=None,
            bb=BbState(pct_b=0.5, bandwidth=0.02, bw_pctile=None, squeeze=None),
            vwap=VwapState(
                weekly_price=None,
                weekly_dist_atr=None,
                monthly_price=None,
                monthly_dist_atr=None,
            ),
            profile=None,
        )
        assert _indicator_lines(state) == ["BB       %B 0.50 · bw 2.0%"]

    def test_ema_warmup_and_trend_state(self) -> None:
        state = IndicatorState(
            ema=EmaState(
                above_20=True,
                above_50=None,
                above_200=None,
                stack=None,
                slope_200=None,
            ),
            range_state=RangeState(
                label="trend",
                since_ms=1_708_300_800_000,
                bars=5,
                range_low=None,
                range_high=None,
                pos=None,
            ),
            monday=MondayState(state="forming", pos=None),
            candles=None,
            pa=None,
            bb=None,
            vwap=None,
            profile=None,
        )
        assert _indicator_lines(state) == [
            "EMA      ▲20 —50 —200 · stack n/a · 200 n/a",
            "State    trend since 2024-02-19 (5 bars)",
            "Monday   forming",
        ]


H1 = 3_600_000


def test_clock_line_active_session() -> None:
    clock = SessionClock(
        label="London",
        start_ms=START_MS + 6 * H1,
        end_ms=START_MS + 14 * H1,
        is_overlap=False,
        next_label="NY",
        next_start_ms=START_MS + 14 * H1,
    )
    line = _clock_line(clock, START_MS + 9 * H1)  # 17:00 MYT
    assert line == (
        "Session: London 14:00–22:00 MYT · 3h00m in / 5h00m left · next NY 22:00 MYT"
    )


def test_clock_line_overlap_and_off() -> None:
    overlap = SessionClock(
        label="London",
        start_ms=START_MS + 6 * H1,
        end_ms=START_MS + 14 * H1,
        is_overlap=True,
        next_label="NY",
        next_start_ms=START_MS + 14 * H1,
    )
    assert _clock_line(overlap, START_MS + 13 * H1) is not None
    assert "London (NY overlap)" in str(_clock_line(overlap, START_MS + 13 * H1))
    off = SessionClock(
        label="Off",
        start_ms=START_MS + 20 * H1,
        end_ms=START_MS + 24 * H1,
        is_overlap=False,
        next_label="Asia",
        next_start_ms=START_MS + 24 * H1,
    )
    assert _clock_line(off, START_MS + 21 * H1) == (
        "Session: between sessions (04:00–08:00 MYT) · next Asia 08:00 MYT"
    )
    assert _clock_line(None, START_MS) is None


def test_recap_bit_formats() -> None:
    row = SessionRecapRow(
        session="Asia",
        start_ms=START_MS,  # Mon 08:00 MYT
        end_ms=START_MS + 6 * H1,
        open=100.0,
        high=103.0,
        low=99.0,
        close=101.0,
        net_pct=1.0,
        net_atr=0.5,
        range_atr=2.0,
        n_bars=6,
        expected_bars=6,
        made_set_high=True,
        made_set_low=False,
    )
    assert _recap_bit(row) == (
        "Asia   Mon 08–14 MYT · net +1.00% (+0.50 ATR) · range 2.0 ATR ·set-high"
    )
    partial = SessionRecapRow(
        session="NY",
        start_ms=START_MS + 14 * H1,
        end_ms=START_MS + 20 * H1,
        open=100.0,
        high=103.0,
        low=99.0,
        close=101.0,
        net_pct=1.0,
        net_atr=None,
        range_atr=None,
        n_bars=4,
        expected_bars=6,
        made_set_high=False,
        made_set_low=False,
    )
    assert _recap_bit(partial) == (
        "NY     Mon 22–04 MYT (4/6 bars) · net +1.00% · range n/a"
    )


def test_recap_bit_set_low_alone() -> None:
    row = SessionRecapRow(
        session="London",
        start_ms=START_MS + 6 * H1,
        end_ms=START_MS + 14 * H1,
        open=100.0,
        high=101.0,
        low=95.0,
        close=99.0,
        net_pct=-1.0,
        net_atr=-0.5,
        range_atr=3.0,
        n_bars=8,
        expected_bars=8,
        made_set_high=False,
        made_set_low=True,
    )
    bit = _recap_bit(row)
    assert bit.endswith("·set-low")
    assert "·set-high" not in bit


def test_session_lines_none_and_empty_are_empty() -> None:
    assert _session_lines(None) == []
    # State present but both halves None (defensive; the adapter collapses
    # this to None, but the renderer must not emit a bare header).
    assert _session_lines(SessionState(recap=None, tendency=None)) == []


def test_markdown_carries_session_lines(tmp_path: Path) -> None:
    cfg, conn = _seeded_cfg(tmp_path)  # as_of = Fri 08:00 MYT, Asia open
    out = render_markdown(compute_brief(conn, cfg))
    assert "\nSession: Asia 08:00–14:00 MYT · 0h00m in / 6h00m left" in out
    assert "\nSessions " in out
    assert "tendency: day-high " in out
