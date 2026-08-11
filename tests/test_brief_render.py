"""Renderer: formatting rules + end-to-end byte-stability."""

import json
from pathlib import Path

import duckdb

from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig
from analytics.brief.render import (
    _clock_line,
    _external_lines,
    _external_snapshot_bit,
    _indicator_lines,
    _monthly_lines,
    _recap_bit,
    _session_lines,
    _weekly_lines,
    fmt_dist,
    fmt_frac,
    fmt_price,
    render_markdown,
)
from analytics.brief.types import (
    BbState,
    CandleHit,
    EmaState,
    ExternalClusterRow,
    ExternalSnapshot,
    ExternalState,
    IndicatorState,
    MondayState,
    MonthlyContext,
    PaState,
    ProfileState,
    RangeState,
    SessionClock,
    SessionRecapRow,
    SessionState,
    VwapState,
    WeeklyState,
)
from tests._brief_fixtures import DAY_MS, START_MS, brief_cfg, make_conn, seed_symbol

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
    cfg = brief_cfg(
        ("BTCUSDT",),
        AS_OF,
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
    cfg = brief_cfg(
        ("BTCUSDT",),
        AS_OF,
        ledger_path=ledger,
        priors_path=priors,
    )
    return cfg, conn


_PRIORS_TWO_EACH = json.dumps(
    {
        "generated_at": "2024-02-01T00:00:00Z",
        "policy": {"min_n_marker": 5},
        "authors": {
            # alice carries r_coverage, bob does NOT — deliberately asymmetric so
            # the suffix and the pre-2026-08-11 fallback are both exercised. A
            # fixture where every author had coverage would assert nothing about
            # a priors file written before the field existed.
            "alice": {
                "n": 12,
                "hit_rate": 0.58,
                "avg_r": 0.34,
                "avg_atr_r": 1.2,
                "r_coverage": 0.5,
            },
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
    # ATR-R leads avg R (2026-08-11): ATR-R is the COMPLETE sample, where avg R
    # is computed only over calls that stated a stop. alice's avg R carries its
    # coverage; bob's priors predate the field, so his renders bare.
    assert "  alice  n=12 · 58% · +1.2 ATR-R · +0.34R (50% cov)" in out
    assert "  bob  n=9 · 44% · +0.8 ATR-R · -0.11R" in out
    # Sorted by n desc: alice (12) before bob (9).
    assert out.index("  alice  n=12") < out.index("  bob  n=9")
    # Families now show avg_r too.
    assert "  breakout/long  n=20 · 55% · +1.1 ATR-R · +0.22R" in out
    # None family avg_r renders the placeholder, never the literal "None".
    assert "  reclaim/short  n=14 · 50% · +0.9 ATR-R · —R" in out
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
    cfg2 = brief_cfg(
        ("NODATAUSDT",),
        AS_OF,
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


def _ext_snapshot(**overrides: object) -> ExternalSnapshot:
    base: dict[str, object] = {
        "source": "coinglass",
        "venue": None,
        "panel": "liq_heatmap",
        "window": "24h",
        "scope": "pair",
        "captured_at_ms": 0,
        "age_hours": 14.4,
        "spot_price_hint": None,
        "spot_hint_deviation": False,
        "clusters_above": [
            ExternalClusterRow(
                price_lo=66_000.0,
                price_hi=66_200.0,
                kind="liq",
                intensity="high",
                label="",
                dist_atr=1.83,
            )
        ],
        "clusters_below": [
            ExternalClusterRow(
                price_lo=61_200.0,
                price_hi=61_500.0,
                kind="liq",
                intensity="med",
                label="100x-heavy",
                dist_atr=-1.62,
            )
        ],
    }
    base.update(overrides)
    return ExternalSnapshot(**base)  # type: ignore[arg-type]


def test_external_lines_format() -> None:
    lines = _external_lines(ExternalState(snapshots=[_ext_snapshot()]))
    assert lines == [
        "External coinglass liq (24h) · 14h · "
        "above 66,000–66,200 HIGH (+1.83) · "
        "below 61,200–61,500 med 100x-heavy (-1.62)"
    ]


def test_external_lines_variants() -> None:
    assert _external_lines(None) == []
    agg = _ext_snapshot(
        source="mmt",
        panel="liq_map",
        window=None,
        scope="agg",
        spot_hint_deviation=True,
        clusters_above=[],
    )
    lines = _external_lines(ExternalState(snapshots=[_ext_snapshot(), agg]))
    assert len(lines) == 2
    assert lines[1].startswith(" " * 9 + "mmt map agg · 14h ⚠spot · above none")


def _ext_row(lo: float, hi: float, dist: float, label: str = "") -> ExternalClusterRow:
    return ExternalClusterRow(
        price_lo=lo,
        price_hi=hi,
        kind="liq",
        intensity="med",
        label=label,
        dist_atr=dist,
    )


def test_external_snapshot_bit_joins_multiple_clusters() -> None:
    snap = ExternalSnapshot(
        source="coinglass",
        venue=None,
        panel="liq_map",
        window="1d",
        scope="pair",
        captured_at_ms=1,
        age_hours=14.0,
        spot_price_hint=None,
        spot_hint_deviation=False,
        clusters_above=[_ext_row(101, 102, 0.5), _ext_row(105, 106, 1.5, "top")],
        clusters_below=[],
    )
    bit = _external_snapshot_bit(snap)
    assert ", " in bit.split("above ")[1].split(" · below")[0]
    assert bit.endswith("below none")


def test_external_snapshot_bit_both_sides_none() -> None:
    snap = ExternalSnapshot(
        source="coinglass",
        venue=None,
        panel="liq_heatmap",
        window=None,
        scope="agg",
        captured_at_ms=1,
        age_hours=3.0,
        spot_price_hint=None,
        spot_hint_deviation=False,
        clusters_above=[],
        clusters_below=[],
    )
    bit = _external_snapshot_bit(snap)
    assert "above none" in bit
    assert "below none" in bit
    assert " agg " in bit


def test_external_snapshot_bit_shows_venue() -> None:
    snap = ExternalSnapshot(
        source="coinglass",
        venue="hyperliquid",
        panel="liq_map",
        window="1d",
        scope="pair",
        captured_at_ms=1,
        age_hours=2.0,
        spot_price_hint=None,
        spot_hint_deviation=False,
        clusters_above=[_ext_row(101, 102, 0.5)],
        clusters_below=[],
    )
    assert _external_snapshot_bit(snap).startswith("coinglass/hyperliquid map (1d)")


def _weekly_state(**overrides: object) -> WeeklyState:
    base: dict[str, object] = {
        "path_direction": "bull",
        "elapsed_h": 40,
        "total_bars": 168,
        "norm_now": 0.35,
        "pct_conditional": 62.0,
        "pct_unconditional": 58.0,
        "n_conditional": 172,
        "n_unconditional": 344,
        "low_hour": 3,
        "high_hour": 39,
        "low_in_by_now": 0.71,
        "conditional_is_fallback": False,
    }
    base.update(overrides)
    return WeeklyState(**base)  # type: ignore[arg-type]


def test_weekly_lines_none_is_empty() -> None:
    assert _weekly_lines(None) == []


def test_weekly_lines_bull_shows_conditional_clause() -> None:
    lines = _weekly_lines(_weekly_state())
    assert len(lines) == 3
    assert "of weeks that closed bull (n=172)" in lines[1]
    assert "p58 unconditional (n=344)" in lines[1]
    assert "low close so far h3" in lines[2]
    assert "high close so far h39" in lines[2]
    assert "of those weeks had set their low by now" in lines[2]


def test_weekly_lines_head_uses_elapsed_moment_convention() -> None:
    """N1 fix: `h` is hours elapsed since the Monday 00:00 UTC weekly open
    (day = h // 24, hour = h % 24) — matching WeeklyCone.svelte's hourLabel()
    exactly, not the open time of the last completed bar (h - 1). h=168 is
    the right edge of the week and is rendered as "Sun 24:00 UTC" rather than
    wrapping to "Mon 00:00" via modulo.

    MYT (UTC+8) rides alongside UTC in the same parenthetical, matching the
    chart's dual-timezone hourLabel(). The MYT half is NOT derived from the
    UTC day index — h=40 is the asymmetric case: Tue 16:00 UTC but Wed 00:00
    MYT, i.e. the two axes disagree about which weekday it is. That's the
    whole reason the MYT day index has to be computed independently rather
    than reusing the UTC one."""
    assert (
        "h40/168 (Tue 16:00 UTC · Wed 00:00 MYT)"
        in _weekly_lines(_weekly_state(elapsed_h=40))[0]
    )
    assert (
        "h63/168 (Wed 15:00 UTC · Wed 23:00 MYT)"
        in _weekly_lines(_weekly_state(elapsed_h=63))[0]
    )
    assert (
        "h0/168 (Mon 00:00 UTC · Mon 08:00 MYT)"
        in _weekly_lines(_weekly_state(elapsed_h=0))[0]
    )
    assert (
        "h168/168 (Sun 24:00 UTC · Mon 08:00 MYT)"
        in _weekly_lines(_weekly_state(elapsed_h=168))[0]
    )


def test_weekly_lines_flat_omits_conditional_clause() -> None:
    """B2/C1 fix: cone.combos only has all/bull/bear — a "flat" state's
    conditional fields mirror the unconditional ones (adapter fallback), so
    the rendered rank line must not claim a "closed flat" cohort exists, and
    the timing line must attribute to "all weeks", not "those weeks". Driven
    by `conditional_is_fallback`, not `path_direction == "flat"` (C1)."""
    state = _weekly_state(
        path_direction="flat",
        pct_conditional=58.0,
        n_conditional=344,
        conditional_is_fallback=True,
    )
    lines = _weekly_lines(state)
    assert len(lines) == 3
    ranks = lines[1]
    assert "closed flat" not in ranks
    assert "flat" not in ranks  # no direction word leaks into the rank line
    assert ranks == "      p58 unconditional (n=344)"
    assert "of all weeks had set their low by now" in lines[2]
    assert "of those weeks" not in lines[2]


def test_weekly_lines_empty_conditional_combo_omits_clause() -> None:
    """C1: a bear combo that EXISTS in cone.combos but has zero weeks
    (bands=[]) also falls back to the unconditional population in the
    adapter — a non-"flat" direction can still hit the fallback, so the
    renderer must key off `conditional_is_fallback`, not the direction
    string, or it would render a false "closed bear (n=0)" cohort label."""
    state = _weekly_state(
        path_direction="bear",
        pct_conditional=58.0,
        n_conditional=344,
        conditional_is_fallback=True,
    )
    lines = _weekly_lines(state)
    ranks = lines[1]
    assert "closed bear" not in ranks
    assert ranks == "      p58 unconditional (n=344)"
    assert "of all weeks had set their low by now" in lines[2]
    assert "of those weeks" not in lines[2]


def test_weekly_lines_saturated_high_uses_ge_prefix() -> None:
    """`_percentile_of` clamps to 90.0 for any value AT OR ABOVE the p90 band
    edge — nothing beyond p90 is resolvable from the 5-percentile ladder. So a
    rendered 90.0 must read "≥p90", not the exact-rank "p90" its own docstring
    forbids the renderer from claiming. Applies to both the conditional and
    unconditional legs of the non-fallback rank line."""
    ranks = _weekly_lines(_weekly_state(pct_conditional=90.0, pct_unconditional=90.0))[
        1
    ]
    assert ranks == (
        "      ≥p90 of weeks that closed bull (n=172) · ≥p90 unconditional (n=344)"
    )


def test_weekly_lines_saturated_low_uses_le_prefix() -> None:
    """Mirror of the high rail: 10.0 means "at or below p10" and must render
    "≤p10". Exercised on the fallback line so the single unconditional leg is
    checked too."""
    state = _weekly_state(
        path_direction="flat",
        pct_conditional=10.0,
        pct_unconditional=10.0,
        n_conditional=344,
        conditional_is_fallback=True,
    )
    assert _weekly_lines(state)[1] == "      ≤p10 unconditional (n=344)"


def test_weekly_lines_interior_percentile_unprefixed() -> None:
    """A resolvable interior rank (strictly between the rails) keeps the bare
    "p{n}" form — the inequality prefix is reserved for the unresolvable rails,
    so a p58 must not gain a spurious ≤/≥."""
    ranks = _weekly_lines(_weekly_state(pct_conditional=62.0, pct_unconditional=58.0))[
        1
    ]
    assert "≤" not in ranks
    assert "≥" not in ranks
    assert "p62 of weeks that closed bull" in ranks
    assert "p58 unconditional" in ranks


def test_monthly_lines_none_is_empty() -> None:
    assert _monthly_lines(None) == []


def test_monthly_lines_full() -> None:
    ctx = MonthlyContext(
        mtd_return_pct=4.2,
        mtd_elapsed_frac=0.65,
        pct_of_months=70.0,
        n_months=84,
        range_position=0.8,
    )
    assert _monthly_lines(ctx) == [
        "Month +4.2% · p70 of 84 completed months · range position 0.80 (65% elapsed)"
    ]


def test_monthly_lines_range_position_none() -> None:
    ctx = MonthlyContext(
        mtd_return_pct=-1.0,
        mtd_elapsed_frac=0.10,
        pct_of_months=None,
        n_months=0,
        range_position=None,
    )
    assert _monthly_lines(ctx) == [
        "Month -1.0% · — of 0 completed months · range position — (10% elapsed)"
    ]


def test_external_snapshot_bit_no_venue_unchanged() -> None:
    snap = ExternalSnapshot(
        source="coinglass",
        venue=None,
        panel="liq_map",
        window="1d",
        scope="pair",
        captured_at_ms=1,
        age_hours=2.0,
        spot_price_hint=None,
        spot_hint_deviation=False,
        clusters_above=[_ext_row(101, 102, 0.5)],
        clusters_below=[],
    )
    assert _external_snapshot_bit(snap).startswith("coinglass map (1d)")
