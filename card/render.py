"""Deterministic terminal renderer (brief render.py pattern)."""

from __future__ import annotations

from card.card import FinalCard

_BANNERS = {"TRADE": "▲ TRADE", "NO_TRADE": "─ NO TRADE", "VETOED": "✕ VETOED"}


def render_card(final: FinalCard) -> str:
    card = final.card
    lines = [f"BUIBUI TRADE CARD — {final.symbol} · {_BANNERS[final.verdict]}"]
    for reason in final.veto_reasons:
        lines.append(f"  veto: {reason}")
    if final.verdict == "NO_TRADE" and card.no_trade_reason:
        lines.append(f"  gate: {card.no_trade_reason}")
    if card.verdict == "TRADE":
        rr = f"{final.rr_tp1:.2f}" if final.rr_tp1 is not None else "?"
        # The net figure is the one the floor gates on, so it has to be read
        # beside the gross one rather than only reaching the JSONL ledger.
        if final.rr_tp1_net is not None:
            rr = f"{rr} (net {final.rr_tp1_net:.2f})"
        lines.append(
            f"{card.direction} · entry {card.entry} · SL {card.sl} · RR(tp1) {rr}"
        )
        lines.append(f"TP1 {card.tp1} · TP2 {card.tp2} · TP3 {card.tp3}")
    if (
        final.size_units is not None
        and final.notional_usd is not None
        and final.risk_usd is not None
    ):
        capital_note = (
            f" of {final.capital_used:,.2f} {final.capital_source}"
            if final.capital_used is not None and final.capital_source is not None
            else ""
        )
        if final.sizing_regime is not None:
            capital_note = f"{capital_note}, {final.sizing_regime} size"
        lines.append(
            f"size: {final.size_units} units · notional "
            f"${final.notional_usd:.2f} · risk ${final.risk_usd:.2f} "
            f"({(final.risk_frac or 0.0) * 100:.2f}%{capital_note})"
        )
    lines.append(f"confluence {card.confluence_score}/9")
    lines.extend(f"  + {i.input}: {i.evidence}" for i in card.confluence_inputs)
    lines.append("reasoning:")
    lines.extend(f"  - {b}" for b in card.reasoning)
    if card.steelman:
        lines.append("steelman:")
        lines.extend(f"  - {b}" for b in card.steelman)
    if card.invalidation:
        lines.append(f"invalidation: {card.invalidation}")
    if card.expected_hold or card.valid_until_utc:
        lines.append(
            f"hold {card.expected_hold or '?'} · valid until "
            f"{card.valid_until_utc or '?'}"
        )
    lines.extend(f"⚠ {w}" for w in final.warnings)
    cost = (
        f"${final.cost_usd_notional:.4f} notional"
        if final.cost_usd_notional is not None
        else "n/a"
    )
    lines.append(
        f"[{final.model} · {final.prompt_version} · "
        f"state {final.state_digest[:8]} · cost {cost}]"
    )
    return "\n".join(lines)
