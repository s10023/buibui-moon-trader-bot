"""Deterministic markdown renderer for the BriefBundle (no wall-clock)."""

from __future__ import annotations

import pandas as pd

from analytics.brief._common import TF_MS
from analytics.brief.types import (
    BbState,
    BriefBundle,
    CandleHit,
    EmaState,
    ExternalClusterRow,
    ExternalSnapshot,
    ExternalState,
    IndicatorState,
    LevelRow,
    MondayState,
    MonthlyContext,
    PaState,
    ProfileState,
    PunditAuthorPrior,
    PunditBoard,
    PunditCallRow,
    PunditFamilyPrior,
    RangeState,
    SeasonalityStrip,
    SessionClock,
    SessionRecapRow,
    SessionState,
    SessionTendencyRow,
    SymbolPanel,
    VwapState,
    WeeklyState,
    ZoneRow,
)

_MYT_OFFSET_MS = 8 * 3_600_000
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def fmt_price(value: float) -> str:
    """>=1000 -> 0dp grouped; >=1 -> 2dp; else 4dp."""
    if value >= 1000:
        return f"{value:,.0f}"
    if value >= 1:
        return f"{value:,.2f}"
    return f"{value:.4f}"


def fmt_dist(value: float) -> str:
    return f"{value:+.2f}"


def fmt_frac(value: float) -> str:
    return f"{value * 100:.0f}%"


def _level_str(row: LevelRow) -> str:
    swept = ", swept✓" if row.swept else ""
    return f"{row.name} {fmt_price(row.price)} ({fmt_dist(row.dist_atr)}{swept})"


def _zone_str(row: ZoneRow) -> str:
    if row.zone_low == row.zone_high:
        span = fmt_price(row.zone_low)
    else:
        span = f"{fmt_price(row.zone_low)}–{fmt_price(row.zone_high)}"
    marker = "inside" if row.inside else fmt_dist(row.dist_atr)
    return f"{row.tf} {row.zone_type.upper()}·{row.direction} {span} ({marker})"


def _strip_lines(strip: SeasonalityStrip | None) -> list[str]:
    if strip is None:
        return ["Day/Week seasonality: n/a"]
    parts: list[str] = []
    if strip.bull_pct is not None and strip.avg_range_pct is not None:
        parts.append(
            f"{strip.dow}: bull {fmt_frac(strip.bull_pct)} · avg range "
            f"{strip.avg_range_pct * 100:.1f}% (n={strip.sample_days})"
        )
    if (
        strip.high_session is not None
        and strip.high_session_pct is not None
        and strip.low_session is not None
        and strip.low_session_pct is not None
    ):
        parts.append(
            f"high most often {strip.high_session} "
            f"{fmt_frac(strip.high_session_pct)} · low {strip.low_session} "
            f"{fmt_frac(strip.low_session_pct)}"
        )
    lines = [f"Day/Week {' · '.join(parts)}" if parts else "Day/Week seasonality: n/a"]
    weekly: list[str] = []
    if strip.weekly_low_still_ahead is not None:
        weekly.append(f"low still ahead {fmt_frac(strip.weekly_low_still_ahead)}")
    if strip.weekly_high_still_ahead is not None:
        weekly.append(f"high still ahead {fmt_frac(strip.weekly_high_still_ahead)}")
    if strip.typical_low_day and strip.typical_high_day:
        weekly.append(
            f"typical low {strip.typical_low_day} / high {strip.typical_high_day}"
        )
    if weekly:
        lines.append(f"         week: {' · '.join(weekly)}")
    return lines


def _ref_price_label(panel: SymbolPanel) -> str:
    """Human tag for the reference-price basis, e.g. "1h close 09:00 UTC"."""
    if panel.ref_price_source == "1d_forming":
        return "1d forming"
    tf = "1h" if panel.ref_price_source == "1h" else "1d"
    close_ts = pd.Timestamp(
        panel.ref_close_ts_ms + TF_MS[tf], unit="ms", tz="UTC"
    ).strftime("%H:%M")
    return f"{tf} close {close_ts} UTC"


def _ema_bit(above: bool | None, span: int) -> str:
    if above is None:
        return f"—{span}"
    return f"{'▲' if above else '▼'}{span}"


def _ema_line(ema: EmaState) -> str:
    spans = " ".join(
        _ema_bit(a, s)
        for a, s in ((ema.above_20, 20), (ema.above_50, 50), (ema.above_200, 200))
    )
    stack = f"stack {ema.stack}" if ema.stack is not None else "stack n/a"
    slope = f"200 {ema.slope_200}" if ema.slope_200 is not None else "200 n/a"
    return f"{'EMA':<9}{spans} · {stack} · {slope}"


def _state_line(rs: RangeState) -> str:
    since = pd.Timestamp(rs.since_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
    head = f"{'State':<9}{rs.label} since {since} ({rs.bars} bars)"
    if rs.range_low is None or rs.range_high is None:
        return head
    bounds = f"{fmt_price(rs.range_low)}–{fmt_price(rs.range_high)}"
    pos = f" · {fmt_frac(rs.pos)}" if rs.pos is not None else ""
    return f"{head} · {bounds}{pos}"


def _monday_line(monday: MondayState) -> str:
    pos = f" ({fmt_frac(monday.pos)})" if monday.pos is not None else ""
    return f"{'Monday':<9}{monday.state}{pos}"


def _candle_line(candles: list[CandleHit]) -> str:
    bits = ", ".join(f"{c.pattern}·{c.direction}" for c in candles) or "none"
    return f"{'Candle':<9}{bits}"


def _pa_line(pa: PaState) -> str:
    return f"{'PA':<9}{pa.label} · ER {pa.er:.2f} · {pa.speed_atr:.2f} ATR/bar"


def _bb_bit(bb: BbState) -> str:
    bits = f"%B {bb.pct_b:.2f} · bw {bb.bandwidth * 100:.1f}%"
    if bb.bw_pctile is not None:
        squeeze = " squeeze" if bb.squeeze else ""
        bits += f" (p{round(bb.bw_pctile * 100)}{squeeze})"
    return bits


def _vwap_bit(vwap: VwapState) -> str:
    parts: list[str] = []
    if vwap.weekly_dist_atr is not None:
        parts.append(f"W {fmt_dist(vwap.weekly_dist_atr)}")
    if vwap.monthly_dist_atr is not None:
        parts.append(f"M {fmt_dist(vwap.monthly_dist_atr)}")
    return " · ".join(parts)


def _profile_line(profile: ProfileState) -> str:
    return (
        f"{'VP60d':<9}POC {fmt_price(profile.poc)} ({fmt_dist(profile.poc_dist_atr)})"
        f" · VA {fmt_price(profile.val)}–{fmt_price(profile.vah)}"
        f" · {profile.vs_value}"
    )


def _indicator_lines(state: IndicatorState | None) -> list[str]:
    """One line per surviving sub-block; failed blocks drop silently."""
    if state is None:
        return []
    lines: list[str] = []
    if state.ema is not None:
        lines.append(_ema_line(state.ema))
    if state.range_state is not None:
        lines.append(_state_line(state.range_state))
    if state.monday is not None:
        lines.append(_monday_line(state.monday))
    if state.candles is not None:
        lines.append(_candle_line(state.candles))
    if state.pa is not None:
        lines.append(_pa_line(state.pa))
    bb_bit = _bb_bit(state.bb) if state.bb is not None else None
    vwap_bit = _vwap_bit(state.vwap) if state.vwap is not None else None
    if vwap_bit == "":
        vwap_bit = None
    if bb_bit is not None and vwap_bit is not None:
        lines.append(f"{'BB':<9}{bb_bit} | AVWAP {vwap_bit}")
    elif bb_bit is not None:
        lines.append(f"{'BB':<9}{bb_bit}")
    elif vwap_bit is not None:
        lines.append(f"{'AVWAP':<9}{vwap_bit}")
    if state.profile is not None:
        lines.append(_profile_line(state.profile))
    return lines


def _myt_hhmm(ms: int) -> str:
    return pd.Timestamp(ms + _MYT_OFFSET_MS, unit="ms", tz="UTC").strftime("%H:%M")


def _fmt_dur(ms: int) -> str:
    minutes = ms // 60_000
    return f"{minutes // 60}h{minutes % 60:02d}m"


def _clock_line(clock: SessionClock | None, as_of_ms: int) -> str | None:
    if clock is None:
        return None
    span = f"{_myt_hhmm(clock.start_ms)}–{_myt_hhmm(clock.end_ms)} MYT"
    nxt = f"next {clock.next_label} {_myt_hhmm(clock.next_start_ms)} MYT"
    if clock.label == "Off":
        return f"Session: between sessions ({span}) · {nxt}"
    overlap = " (NY overlap)" if clock.is_overlap else ""
    elapsed = _fmt_dur(as_of_ms - clock.start_ms)
    left = _fmt_dur(clock.end_ms - as_of_ms)
    return (
        f"Session: {clock.label}{overlap} {span} · {elapsed} in / {left} left · {nxt}"
    )


def _recap_bit(row: SessionRecapRow) -> str:
    start = pd.Timestamp(row.start_ms + _MYT_OFFSET_MS, unit="ms", tz="UTC")
    end = pd.Timestamp(row.end_ms + _MYT_OFFSET_MS, unit="ms", tz="UTC")
    span = (
        f"{_DOW[int(start.weekday())]} {start.strftime('%H')}–{end.strftime('%H')} MYT"
    )
    cov = (
        ""
        if row.n_bars >= row.expected_bars
        else f" ({row.n_bars}/{row.expected_bars} bars)"
    )
    atr_bit = f" ({fmt_dist(row.net_atr)} ATR)" if row.net_atr is not None else ""
    rng = f"range {row.range_atr:.1f} ATR" if row.range_atr is not None else "range n/a"
    marks = (" ·set-high" if row.made_set_high else "") + (
        " ·set-low" if row.made_set_low else ""
    )
    return (
        f"{row.session:<7}{span}{cov} · net {row.net_pct:+.2f}%{atr_bit} · {rng}{marks}"
    )


def _tendency_bit(rows: list[SessionTendencyRow]) -> str:
    hi = " · ".join(f"{r.session} {fmt_frac(r.high_pct)}" for r in rows)
    lo = " · ".join(f"{r.session} {fmt_frac(r.low_pct)}" for r in rows)
    return f"tendency: day-high {hi} | day-low {lo}"


def _session_lines(state: SessionState | None) -> list[str]:
    if state is None:
        return []
    bits: list[str] = []
    if state.recap is not None:
        bits.extend(_recap_bit(r) for r in state.recap)
    if state.tendency is not None:
        bits.append(_tendency_bit(state.tendency))
    if not bits:
        return []
    return [f"{'Sessions':<9}{bits[0]}"] + [f"{'':9}{b}" for b in bits[1:]]


def _monthly_lines(ctx: MonthlyContext | None) -> list[str]:
    """Descriptive monthly context. Not a distribution, not a forecast.

    No ``seasonality`` param: ``SeasonalityStrip`` carries only day-of-week /
    session / weekly-timing fields (see ``analytics/brief/seasonality.py``) —
    it has no calendar-month data to duplicate, and it already renders its
    own line via ``_strip_lines`` at its own call site in ``_panel_lines``.
    """
    if ctx is None:
        return []
    rng = "—" if ctx.range_position is None else f"{ctx.range_position:.2f}"
    # M2: zero completed prior months means no percentile is computable —
    # render "—", not a fabricated p50.
    pct_bit = "—" if ctx.pct_of_months is None else f"p{ctx.pct_of_months:.0f}"
    head = (
        f"Month {ctx.mtd_return_pct:+.1f}% · {pct_bit} of "
        f"{ctx.n_months} completed months · range position {rng} "
        f"({ctx.mtd_elapsed_frac:.0%} elapsed)"
    )
    return [head]


def _weekly_lines(state: WeeklyState | None) -> list[str]:
    """Forming-week position inside the weekly cone. Conditional on outcome."""
    if state is None:
        return []
    # Elapsed-moment convention — matches WeeklyCone.svelte's hourLabel(): day
    # index h // 24, hour h % 24. h counts fully-closed bars since the Monday
    # 00:00 UTC weekly open, so h=63 means 63 hours have elapsed = Wed 15:00
    # UTC (not the open time of the 63rd bar). h == total_bars (168) is the
    # right edge of the week (Sunday 24:00 UTC = next Monday 00:00 UTC) and is
    # rendered explicitly rather than wrapping back to "Mon 00:00" via //24.
    h = state.elapsed_h
    day_hour = (
        "Sun 24:00 UTC"
        if h >= state.total_bars
        else f"{_DOW[h // 24]} {h % 24:02d}:00 UTC"
    )
    head = (
        f"Week  {state.path_direction} path so far · "
        f"h{state.elapsed_h}/{state.total_bars} ({day_hour}) · "
        f"{state.norm_now:+.2f}×AWR"
    )
    # C1: the adapter (analytics/brief/weekly.py::build_weekly_state) falls
    # the conditional pool back to the unconditional one whenever a
    # same-direction cohort can't be resolved distinctly — either "flat" has
    # no cohort at all, or the bull/bear combo exists but is empty (n=0).
    # `conditional_is_fallback` covers BOTH cases; keying on
    # `path_direction == "flat"` alone missed the empty-combo case and
    # rendered the unconditional population under a false "closed bear
    # (n=0)"-style cohort label. Presenting the fallback as a real cohort
    # would misattribute the unconditional population as conditional, so
    # omit the conditional clause and attribute the timing stat to
    # "all weeks" instead of "those weeks".
    if state.conditional_is_fallback:
        ranks = (
            f"      p{state.pct_unconditional:.0f} unconditional "
            f"(n={state.n_unconditional})"
        )
        timing_cohort = "all weeks"
    else:
        ranks = (
            f"      p{state.pct_conditional:.0f} of weeks that closed "
            f"{state.path_direction} (n={state.n_conditional}) · "
            f"p{state.pct_unconditional:.0f} unconditional (n={state.n_unconditional})"
        )
        timing_cohort = "those weeks"
    # I4: low_hour/high_hour are CLOSE-based (see weekly.py comment) while
    # low_in_by_now is drawn from an intrabar low/high distribution — label
    # the former explicitly so the two are not read as the same definition.
    timing = (
        f"      low close so far h{state.low_hour} · "
        f"high close so far h{state.high_hour} · "
        f"{state.low_in_by_now:.0%} of {timing_cohort} had set their low by now"
    )
    return [head, ranks, timing]


_PANEL_SHORT = {"liq_heatmap": "liq", "book_heatmap": "book", "liq_map": "map"}


def _cluster_bit(row: ExternalClusterRow) -> str:
    lo, hi = fmt_price(row.price_lo), fmt_price(row.price_hi)
    band = lo if lo == hi else f"{lo}–{hi}"
    strength = "HIGH" if row.intensity == "high" else row.intensity
    label_bit = f" {row.label}" if row.label else ""
    return f"{band} {strength}{label_bit} ({fmt_dist(row.dist_atr)})"


def _external_snapshot_bit(snap: ExternalSnapshot) -> str:
    src = snap.source if snap.venue is None else f"{snap.source}/{snap.venue}"
    win = f" ({snap.window})" if snap.window else ""
    scope_bit = " agg" if snap.scope == "agg" else ""
    dev = " ⚠spot" if snap.spot_hint_deviation else ""
    above = ", ".join(_cluster_bit(r) for r in snap.clusters_above) or "none"
    below = ", ".join(_cluster_bit(r) for r in snap.clusters_below) or "none"
    return (
        f"{src} {_PANEL_SHORT.get(snap.panel, snap.panel)}{win}{scope_bit}"
        f" · {snap.age_hours:.0f}h{dev} · above {above} · below {below}"
    )


def _external_lines(state: ExternalState | None) -> list[str]:
    if state is None or not state.snapshots:
        return []
    bits = [_external_snapshot_bit(s) for s in state.snapshots]
    return [f"{'External':<9}{bits[0]}"] + [f"{'':9}{b}" for b in bits[1:]]


def _panel_lines(panel: SymbolPanel) -> list[str]:
    lines = [f"── {panel.symbol} " + "─" * 44]
    if panel.error is not None:
        lines.append(f"ERROR: {panel.error}")
        return lines
    adr = f" · ADR {fmt_frac(panel.adr_pct)}" if panel.adr_pct is not None else ""
    lines.append(
        f"Last {fmt_price(panel.ref_close)} ({_ref_price_label(panel)}) · "
        f"Regime 1d {panel.regime_1d} / "
        f"4h {panel.regime_4h} · ATR14(1d) {fmt_price(panel.atr14)}{adr}"
    )
    lines.extend(_indicator_lines(panel.indicators))
    lines.extend(_session_lines(panel.sessions))
    lines.extend(_monthly_lines(panel.monthly))
    lines.extend(_weekly_lines(panel.weekly))
    lines.extend(_external_lines(panel.external))
    above = " · ".join(_level_str(r) for r in panel.levels_above) or "none"
    below = " · ".join(_level_str(r) for r in panel.levels_below) or "none"
    lines.append(f"Levels   above → {above}")
    lines.append(f"         below → {below}")
    zone_bits = " · ".join(
        _zone_str(r) for r in [*panel.zones_above, *panel.zones_below]
    )
    lines.append(f"Zones    {zone_bits or 'none'}")
    lines.extend(_strip_lines(panel.seasonality))
    return lines


def _author_str(prior: PunditAuthorPrior) -> str:
    if prior.flagged:
        return f"(⚠ n={prior.n})"
    bits = [f"n={prior.n}"]
    if prior.hit_rate is not None:
        bits.append(fmt_frac(prior.hit_rate))
    if prior.avg_atr_r is not None:
        bits.append(f"{prior.avg_atr_r:+.1f} ATR-R̄")
    return "(" + " · ".join(bits) + ")"


def _call_line(call: PunditCallRow) -> str:
    marker = "● " if call.on_panel else "  "
    prior = f" {_author_str(call.prior)}" if call.prior is not None else ""
    target = f' → "{call.target}"' if call.target else ""
    horizon = f" · {call.horizon}" if call.horizon else ""
    return (
        f"{marker}{call.author}{prior} {call.symbol} {call.direction} — "
        f'"{call.entry}"{target}{horizon} · {call.age_days}d'
    )


def _hit_str(value: float | None) -> str:
    return fmt_frac(value) if value is not None else "—"


def _avg_r_str(value: float | None) -> str:
    return f"{value:+.2f}R" if value is not None else "—R"


def _atr_r_str(value: float | None) -> str:
    return f"{value:+.1f} ATR-R" if value is not None else "— ATR-R"


def _stats_str(
    hit_rate: float | None, avg_r: float | None, avg_atr_r: float | None
) -> str:
    return f"{_hit_str(hit_rate)} · {_avg_r_str(avg_r)} · {_atr_r_str(avg_atr_r)}"


def _author_board_line(a: PunditAuthorPrior) -> str:
    flag = " ⚠" if a.flagged else ""
    return (
        f"  {a.author}  n={a.n}{flag} · {_stats_str(a.hit_rate, a.avg_r, a.avg_atr_r)}"
    )


def _family_board_line(f: PunditFamilyPrior) -> str:
    flag = " ⚠" if f.flagged else ""
    return (
        f"  {f.family}/{f.direction}  n={f.n}{flag} · "
        f"{_stats_str(f.hit_rate, f.avg_r, f.avg_atr_r)}"
    )


def _priors_age_str(age_days: int | None) -> str:
    """Guard the None age (priors JSON valid but generated_at unparseable)."""
    return f"{age_days}d" if age_days is not None else "?d"


def _pundit_lines(board: PunditBoard) -> list[str]:
    if board.priors_status == "ok":
        priors_bit = f"priors {_priors_age_str(board.priors_age_days)} old"
    else:
        priors_bit = f"priors {board.priors_status}; run make buibui-pundit-score"
    if board.ledger_status == "ok":
        ledger_bit = (
            f"ledger {board.ledger_total} calls · {len(board.recent_calls)} recent"
        )
    else:
        ledger_bit = "ledger: not found"
    lines = [f"── PUNDIT BOARD ({priors_bit} · {ledger_bit}) " + "─" * 8]
    for call in board.recent_calls:
        lines.append(_call_line(call))
    if not board.recent_calls:
        lines.append("no recent calls")
    if board.authors:
        lines.append("Top authors (by n):")
        for a in sorted(board.authors, key=lambda a: (-a.n, a.author)):
            lines.append(_author_board_line(a))
    if board.families:
        lines.append("Top families (by n):")
        for f in sorted(board.families, key=lambda f: (-f.n, f.family, f.direction)):
            lines.append(_family_board_line(f))
    return lines


def _health_lines(bundle: BriefBundle) -> list[str]:
    parts: list[str] = []
    for symbol in dict.fromkeys(r.symbol for r in bundle.health.rows):
        tf_bits: list[str] = []
        for row in bundle.health.rows:
            if row.symbol != symbol:
                continue
            if row.status == "ok":
                tf_bits.append(f"{row.tf}✓")
            elif row.status == "stale":
                tf_bits.append(f"{row.tf}⚠ stale ({row.bars_behind} bars)")
            else:
                tf_bits.append(f"{row.tf}✗ no data")
        parts.append(f"{symbol} " + " ".join(tf_bits))
    board = bundle.pundit
    if board.priors_status == "ok":
        parts.append(f"priors {_priors_age_str(board.priors_age_days)}")
    else:
        parts.append(f"priors {board.priors_status}")
    if board.ledger_status == "ok":
        parts.append(f"ledger {board.ledger_total} ({board.ledger_skipped} skipped)")
    else:
        parts.append("ledger absent")
    lines = ["── HEALTH ── " + " · ".join(parts)]
    lines.extend(f"note: {n}" for n in bundle.health.notes)
    return lines


def render_markdown(bundle: BriefBundle) -> str:
    as_of = pd.Timestamp(bundle.as_of_ms, unit="ms", tz="UTC").strftime(
        "%Y-%m-%d %H:%M"
    )
    data = "OK" if bundle.health.data_ok else "⚠ (see health)"
    lines = [
        f"BUIBUI DAILY BRIEF — {bundle.day_ahead} · as-of {as_of} UTC · data {data}",
    ]
    clock = _clock_line(bundle.session_clock, bundle.as_of_ms)
    if clock is not None:
        lines.append(clock)
    lines.append("")
    for panel in bundle.panels:
        lines.extend(_panel_lines(panel))
        lines.append("")
    lines.extend(_pundit_lines(bundle.pundit))
    lines.append("")
    lines.extend(_health_lines(bundle))
    return "\n".join(lines)
