"""Deterministic markdown renderer for the BriefBundle (no wall-clock)."""

from __future__ import annotations

import pandas as pd

from analytics.brief._common import TF_MS
from analytics.brief.types import (
    BriefBundle,
    LevelRow,
    PunditAuthorPrior,
    PunditBoard,
    PunditCallRow,
    PunditFamilyPrior,
    SeasonalityStrip,
    SymbolPanel,
    ZoneRow,
)


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
        priors_bit = f"priors: {board.priors_status} — run make buibui-pundit-score"
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
        "",
    ]
    for panel in bundle.panels:
        lines.extend(_panel_lines(panel))
        lines.append("")
    lines.extend(_pundit_lines(bundle.pundit))
    lines.append("")
    lines.extend(_health_lines(bundle))
    return "\n".join(lines)
