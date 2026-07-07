"""Deterministic markdown renderer for the BriefBundle (no wall-clock)."""

from __future__ import annotations

import pandas as pd

from analytics.brief.types import (
    BriefBundle,
    LevelRow,
    PunditAuthorPrior,
    PunditBoard,
    PunditCallRow,
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


def _panel_lines(panel: SymbolPanel) -> list[str]:
    lines = [f"── {panel.symbol} " + "─" * 44]
    if panel.error is not None:
        lines.append(f"ERROR: {panel.error}")
        return lines
    adr = f" · ADR {fmt_frac(panel.adr_pct)}" if panel.adr_pct is not None else ""
    lines.append(
        f"Close {fmt_price(panel.ref_close)} · Regime 1d {panel.regime_1d} / "
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


def _family_str(board: PunditBoard) -> str:
    bits: list[str] = []
    for f in board.families:
        cell = f"{f.family}/{f.direction} n={f.n}"
        if f.flagged:
            cell += " ⚠"
        else:
            if f.hit_rate is not None:
                cell += f" · {fmt_frac(f.hit_rate)}"
            if f.avg_atr_r is not None:
                cell += f" · {f.avg_atr_r:+.1f} ATR-R̄"
        bits.append(cell)
    return " | ".join(bits)


def _pundit_lines(board: PunditBoard) -> list[str]:
    if board.priors_status == "ok":
        priors_bit = f"priors {board.priors_age_days}d old"
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
    if board.families:
        lines.append(f"FAMILIES  {_family_str(board)}")
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
        parts.append(f"priors {board.priors_age_days}d")
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
