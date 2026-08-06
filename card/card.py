"""TradeCard schema validation + (Task 7) the deterministic post-pass."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from card.config import CardConfig
from card.errors import CardValidationError
from card.prompt import PROMPT_VERSION
from card.state import MarketState, RecentFire
from portfolio.sizing import (
    SizingConfig,
    apply_caps,
    cluster_of,
    effective_risk_fraction,
    position_size,
    regime_multiplier,
    resolve_capital,
    risk_per_unit,
    round_down_to_step,
)

_VERDICTS = ("TRADE", "NO_TRADE")
_DIRECTIONS = ("long", "short")
_PRICE_KEYS = ("entry", "sl", "tp1", "tp2", "tp3")

# Live-vs-backtest contradiction thresholds (a-priori, mirrored in the card-v3
# rubric so code and prompt cannot disagree). MIN_N is an evidence floor, not
# the n>=30 promotion gate — this warning only displays. NOISE_R is the
# round-trip cost drift between pre/post-2026-06-11 ledger rows (~0.06R),
# rounded up: smaller gaps are not readable.
_LIVE_MIN_N = 10
_LIVE_NOISE_R = 0.15


@dataclass(frozen=True)
class TradeCard:
    verdict: str
    direction: str | None
    entry: float | None
    sl: float | None
    tp1: float | None
    tp2: float | None
    tp3: float | None
    confluence_score: int
    reasoning: list[str]
    invalidation: str | None
    expected_hold: str | None
    valid_until_utc: str | None
    no_trade_reason: str | None


def _live_negative_fires(fires: list[RecentFire]) -> list[RecentFire]:
    """Cells whose live record is negative AND readably worse than backtest.

    Deduped per (strategy, tf, direction) — one cell firing three times in the
    lookback is one contradiction, not three warnings.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[RecentFire] = []
    for f in fires:
        key = (f.strategy, f.tf, f.direction)
        if key in seen:
            continue
        if f.live_n is None or f.live_n < _LIVE_MIN_N:
            continue
        if f.live_avg_r is None or f.live_avg_r >= 0.0:
            continue
        if f.avg_r is None or f.avg_r - f.live_avg_r <= _LIVE_NOISE_R:
            continue
        seen.add(key)
        out.append(f)
    return out


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_card_obj(obj: object) -> list[str]:
    """Schema errors for the raw LLM JSON object; [] when valid."""
    if not isinstance(obj, dict):
        return ["card is not a JSON object"]
    errors: list[str] = []
    verdict = obj.get("verdict")
    if verdict not in _VERDICTS:
        errors.append(f"verdict must be one of {list(_VERDICTS)}")
    reasoning = obj.get("reasoning")
    if (
        not isinstance(reasoning, list)
        or not 5 <= len(reasoning) <= 8
        or not all(isinstance(b, str) for b in reasoning)
    ):
        errors.append("reasoning must be a list of 5-8 strings")
    score = obj.get("confluence_score")
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 9:
        errors.append("confluence_score must be an integer in [0, 9]")
    if verdict == "TRADE":
        if obj.get("direction") not in _DIRECTIONS:
            errors.append("TRADE requires direction 'long' or 'short'")
        for key in _PRICE_KEYS:
            v = obj.get(key)
            if not _is_num(v) or float(v) <= 0.0:  # type: ignore[arg-type]
                errors.append(f"TRADE requires positive numeric {key}")
        if not isinstance(obj.get("valid_until_utc"), str):
            errors.append("TRADE requires valid_until_utc (ISO-8601 string)")
    if verdict == "NO_TRADE" and not isinstance(obj.get("no_trade_reason"), str):
        errors.append("NO_TRADE requires no_trade_reason")
    return errors


def parse_trade_card(text: str) -> TradeCard:
    """JSON-parse + validate the LLM output; CardValidationError on failure."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CardValidationError([f"not valid JSON: {exc}"]) from exc
    errors = validate_card_obj(obj)
    if errors:
        raise CardValidationError(errors)

    def _f(key: str) -> float | None:
        v = obj.get(key)
        return float(v) if _is_num(v) else None

    def _s(key: str) -> str | None:
        v = obj.get(key)
        return v if isinstance(v, str) else None

    return TradeCard(
        verdict=str(obj["verdict"]),
        direction=_s("direction"),
        entry=_f("entry"),
        sl=_f("sl"),
        tp1=_f("tp1"),
        tp2=_f("tp2"),
        tp3=_f("tp3"),
        confluence_score=int(obj["confluence_score"]),
        reasoning=[str(b) for b in obj["reasoning"]],
        invalidation=_s("invalidation"),
        expected_hold=_s("expected_hold"),
        valid_until_utc=_s("valid_until_utc"),
        no_trade_reason=_s("no_trade_reason"),
    )


@dataclass(frozen=True)
class FinalCard:
    symbol: str
    as_of_ms: int
    verdict: str  # "TRADE" | "NO_TRADE" | "VETOED"
    card: TradeCard
    size_units: float | None
    notional_usd: float | None
    risk_usd: float | None
    risk_frac: float | None
    capital_used: float | None
    capital_source: str | None
    rr_tp1: float | None
    warnings: list[str]
    veto_reasons: list[str]
    state_digest: str
    prompt_version: str
    model: str
    generated_at_ms: int
    cost_usd_notional: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_iso_ms(text: str) -> int | None:
    """ISO-8601 (incl. trailing 'Z') to epoch ms; None when unparseable."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def post_pass(
    card: TradeCard,
    state: MarketState,
    sizing: SizingConfig,
    cfg: CardConfig,
    *,
    digest: str,
    model: str,
    generated_at_ms: int,
    cost_usd_notional: float | None = None,
    qty_step: float | None = None,
) -> FinalCard:
    """The LLM proposes prices; this code decides money and rules (D6).

    Sizing notes: g_vol is neutral 1.0 (no live equity curve exists at card
    time); open risk is approximated as one r_base per open position (the
    account rows carry no SL, so true open risk is unknowable) — surfaced as
    a warning, never silent.

    `qty_step` is the symbol's exchange LOT_SIZE step. When supplied the
    quantity is floored to it and risk is restated from the ROUNDED size, so
    the printed risk is the risk actually taken. When absent (no exchange
    reachable) the raw quantity is kept and a warning says so — the card must
    still render degraded rather than fail.
    """
    warnings: list[str] = []
    veto: list[str] = []
    size_units: float | None = None
    notional_usd: float | None = None
    risk_usd: float | None = None
    risk_frac: float | None = None
    capital_used: float | None = None
    capital_source: str | None = None
    rr_tp1: float | None = None

    if card.verdict == "TRADE":
        # validation guarantees these are positive floats for TRADE
        entry = float(card.entry or 0.0)
        sl = float(card.sl or 0.0)
        direction = card.direction or ""
        tps = [float(t) for t in (card.tp1, card.tp2, card.tp3) if t is not None]

        # (a) SL side + TP ordering
        if direction == "long" and sl >= entry:
            veto.append("SL must be below entry for a long")
        if direction == "short" and sl <= entry:
            veto.append("SL must be above entry for a short")
        expected = sorted(tps) if direction == "long" else sorted(tps, reverse=True)
        if tps != expected:
            veto.append("TPs must be ordered away from entry")
        if tps and direction == "long" and tps[0] <= entry:
            veto.append("tp1 must be above entry for a long")
        if tps and direction == "short" and tps[0] >= entry:
            veto.append("tp1 must be below entry for a short")

        # (b) planned RR floor
        rpu = risk_per_unit(entry, sl)
        if rpu > 0.0 and card.tp1 is not None:
            rr_tp1 = abs(float(card.tp1) - entry) / rpu
            if rr_tp1 < cfg.min_rr:
                veto.append(f"rr_tp1 {rr_tp1:.2f} breaches min_rr {cfg.min_rr}")

        # (c) conflicting open position + (d) circuit breaker
        if state.account is not None:
            for pos in state.account.positions:
                if pos.symbol == state.symbol and pos.side != direction:
                    veto.append(f"conflicting open {pos.side} position on {pos.symbol}")
            if state.account.daily_r <= cfg.daily_loss_limit_r:
                veto.append(
                    f"daily loss {state.account.daily_r:.2f}R breaches "
                    f"circuit breaker {cfg.daily_loss_limit_r}R"
                )
        else:
            warnings.append("account state unavailable — hard rules unverified")

        # (e) entry sanity band vs ref_close
        panel = state.panel
        if panel is not None and panel.error is None and panel.ref_close > 0.0:
            band = cfg.entry_band_pct / 100.0
            if abs(entry - panel.ref_close) / panel.ref_close > band:
                veto.append(
                    f"entry {entry} outside ±{cfg.entry_band_pct}% of "
                    f"ref_close {panel.ref_close}"
                )
        else:
            warnings.append("ref price unavailable — entry sanity unverified")

        # (f) live ledger contradicts the backtest star on a cited cell.
        # Display only: a veto here would be a promotion mechanism and needs
        # the n>=30 gate the golden-signal loop is blocked on.
        for f in _live_negative_fires(state.recent_fires):
            warnings.append(
                f"live record contradicts backtest: {f.strategy}/{f.tf}/"
                f"{f.direction} is {f.live_avg_r:.3f}R over n={f.live_n} live "
                f"but rated {f.stars}★ / {f.avg_r:.3f}R in backtest"
            )

        # (g) valid_until_utc must postdate the card's own generation time.
        # The field is model-emitted and was unchecked: a SOL card generated
        # 2026-08-04T14:13Z carried "valid until 13:20Z" — expired on arrival.
        # Compared against generated_at_ms, not wall-clock now, so the rule is
        # a property of the card rather than of when it is re-read.
        if card.valid_until_utc is not None:
            expiry_ms = _parse_iso_ms(card.valid_until_utc)
            if expiry_ms is None:
                veto.append(f"valid_until_utc {card.valid_until_utc!r} is unparseable")
            elif expiry_ms <= generated_at_ms:
                veto.append(
                    f"valid_until_utc {card.valid_until_utc} is not after the "
                    "card's own generation time (expired on arrival)"
                )

        # sizing (P1 reuse) — only when nothing vetoed
        if not veto:
            regime = panel.regime_1d if panel is not None else None
            r_eff = effective_risk_fraction(
                sizing,
                g_vol=1.0,
                g_regime=regime_multiplier(regime, sizing),
            )
            open_risk_total = 0.0
            open_risk_cluster = 0.0
            if state.account is not None and state.account.positions:
                cluster = cluster_of(state.symbol, sizing)
                open_risk_total = len(state.account.positions) * sizing.r_base
                open_risk_cluster = sum(
                    sizing.r_base
                    for p in state.account.positions
                    if cluster_of(p.symbol, sizing) == cluster
                )
                warnings.append(
                    "open risk approximated as one r_base per open position"
                )
            r_adm = apply_caps(
                r_eff,
                symbol=state.symbol,
                open_risk_total=open_risk_total,
                open_risk_cluster=open_risk_cluster,
                cfg=sizing,
            )
            if r_adm <= 0.0:
                veto.append("no risk headroom under concurrent/cluster caps")
            else:
                equity = state.account.equity_usd if state.account is not None else None
                capital, used_live = resolve_capital(sizing, equity)
                capital_used = capital
                capital_source = "live_equity" if used_live else "config"
                if not used_live:
                    warnings.append(
                        f"sized off configured capital ${capital:,.2f} — account "
                        "equity unavailable, so the risk fraction is against a "
                        "constant, not the account"
                    )
                risk_frac = r_adm
                risk_usd = capital * r_adm
                size_units = position_size(risk_usd, entry, sl)
                if qty_step is not None and qty_step > 0.0:
                    size_units = round_down_to_step(size_units, qty_step)
                    if size_units <= 0.0:
                        veto.append(
                            f"size floors to zero at qty_step {qty_step} — "
                            "risk budget is below one lot"
                        )
                    else:
                        # Restate risk from the rounded size: the pre-rounding
                        # figure overstates what is actually being risked.
                        risk_usd = size_units * risk_per_unit(entry, sl)
                        risk_frac = risk_usd / capital if capital > 0 else r_adm
                else:
                    warnings.append(
                        "quantity is not LOT_SIZE-rounded (exchange filters "
                        "unavailable) — size and risk are pre-rounding"
                    )
                notional_usd = size_units * entry

    verdict = "VETOED" if veto else card.verdict
    if veto:
        size_units = notional_usd = risk_usd = risk_frac = rr_tp1 = None
        capital_used = capital_source = None
    return FinalCard(
        symbol=state.symbol,
        as_of_ms=state.now_ms,
        verdict=verdict,
        card=card,
        size_units=size_units,
        notional_usd=notional_usd,
        risk_usd=risk_usd,
        risk_frac=risk_frac,
        capital_used=capital_used,
        capital_source=capital_source,
        rr_tp1=rr_tp1,
        warnings=warnings,
        veto_reasons=veto,
        state_digest=digest,
        prompt_version=PROMPT_VERSION,
        model=model,
        generated_at_ms=generated_at_ms,
        cost_usd_notional=cost_usd_notional,
    )
