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
    basket_legs,
    leg_share_usd,
    open_risk_ceiling_r,
    open_risk_r,
    resolve_bet_unit,
    risk_per_unit,
    round_trip_drag_r,
    size_leg,
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

# The card-v5 steelman is a FIXED four (htf counter, underweighted confluence,
# catalyst risk, the other trader). The count is pinned rather than bounded so
# a silently-skipped angle fails validation instead of reading as a card that
# argued all four.
_STEELMAN_ANGLES = 4

# card-v8: the agreeing inputs behind `confluence_score`, one entry per input,
# drawn from the kinds rubric step 3 names. Emitted so the ONE-external-input
# cap is checkable from the artifact: a scalar score cannot tell a cluster
# cited in two bullets from one input counted twice (6/6 cards in the
# 2026-08-25 batch were ambiguous on exactly that). Only external liquidity
# is capped — two indicators can be two independent agreeing inputs.
CONFLUENCE_KINDS = (
    "zone_level",
    "indicator",
    "session",
    "recent_fire",
    "pundit",
    "external_liquidity",
    "xs",
)
_EXTERNAL_KIND = "external_liquidity"
_EXTERNAL_CAP = 1


@dataclass(frozen=True)
class ConfluenceInput:
    input: str
    """One of `CONFLUENCE_KINDS`."""
    evidence: str
    """The number from the state JSON that makes this input agree."""


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
    confluence_inputs: list[ConfluenceInput]
    """One entry per agreeing input; its length IS `confluence_score`.

    Required and without a default, as `steelman` is: a field the code can
    forget to populate is the defect this list exists to close.
    """
    reasoning: list[str]
    steelman: list[str]
    """The four-angle counter-case, empty on a NO_TRADE card.

    Required and without a default for the same reason `FinalCard.horizon`
    is: ST35 was approved, filed to ride the next version bump, and then
    dropped on the floor when that version shipped. A field the code can
    forget to populate is the same failure with a schema.
    """
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


def _confluence_input_errors(inputs: object, score: object) -> list[str]:
    """Shape, count-matches-score and the external-liquidity cap."""
    if not isinstance(inputs, list) or not all(
        isinstance(i, dict)
        and i.get("input") in CONFLUENCE_KINDS
        and isinstance(i.get("evidence"), str)
        and i["evidence"].strip()
        for i in inputs
    ):
        return [
            "confluence_inputs must be a list of {input, evidence} objects, "
            f"input one of {list(CONFLUENCE_KINDS)} and evidence non-empty"
        ]
    errors: list[str] = []
    if isinstance(score, int) and not isinstance(score, bool) and len(inputs) != score:
        errors.append(
            f"confluence_inputs lists {len(inputs)} inputs but confluence_score "
            f"is {score}; they must match"
        )
    n_external = sum(1 for i in inputs if i["input"] == _EXTERNAL_KIND)
    if n_external > _EXTERNAL_CAP:
        errors.append(
            f"confluence_inputs counts {_EXTERNAL_KIND} {n_external} times; all "
            f"external snapshots together are at most {_EXTERNAL_CAP} input"
        )
    return errors


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
    errors.extend(_confluence_input_errors(obj.get("confluence_inputs"), score))
    if verdict == "TRADE":
        if obj.get("direction") not in _DIRECTIONS:
            errors.append("TRADE requires direction 'long' or 'short'")
        for key in _PRICE_KEYS:
            v = obj.get(key)
            if not _is_num(v) or float(v) <= 0.0:  # type: ignore[arg-type]
                errors.append(f"TRADE requires positive numeric {key}")
        if not isinstance(obj.get("valid_until_utc"), str):
            errors.append("TRADE requires valid_until_utc (ISO-8601 string)")
        steelman = obj.get("steelman")
        if (
            not isinstance(steelman, list)
            or len(steelman) != _STEELMAN_ANGLES
            or not all(isinstance(b, str) and b.strip() for b in steelman)
        ):
            errors.append(
                f"TRADE requires steelman: exactly {_STEELMAN_ANGLES} non-empty strings"
            )
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

    def _bullets(key: str) -> list[str]:
        v = obj.get(key)
        return [str(b) for b in v] if isinstance(v, list) else []

    return TradeCard(
        verdict=str(obj["verdict"]),
        direction=_s("direction"),
        entry=_f("entry"),
        sl=_f("sl"),
        tp1=_f("tp1"),
        tp2=_f("tp2"),
        tp3=_f("tp3"),
        confluence_score=int(obj["confluence_score"]),
        confluence_inputs=[
            ConfluenceInput(input=str(i["input"]), evidence=str(i["evidence"]))
            for i in obj["confluence_inputs"]
        ],
        reasoning=[str(b) for b in obj["reasoning"]],
        steelman=_bullets("steelman"),
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
    """The #915 re-base basis R was taken from (`None` on a veto)."""
    capital_source: str | None
    """"rebase", or the "live_equity" / "config" fallback while no basis is set."""
    sizing_regime: str | None
    """#915 regime this card sized under: "measurement" or "unlocked".

    Required with no default, like `horizon`: rows written before #980 lack
    it, and `card-place` reads neither, so older shapes stay readable.
    """
    rr_tp1: float | None
    rr_tp1_net: float | None
    """RR at tp1 after round-trip fee + slippage — the number the floor gates on.

    Gross is kept beside it so historical `ai-cards.jsonl` rows stay comparable
    with new ones; `None` whenever gross is, since a net figure derived from an
    invalid stop would read as computed rather than absent.
    """
    warnings: list[str]
    veto_reasons: list[str]
    state_digest: str
    prompt_version: str
    model: str
    generated_at_ms: int
    cost_usd_notional: float | None
    horizon: str
    """Scoring window this card is booked against ("intraday" | "swing").

    Stamped here rather than re-read from the config at each ledger write:
    `ai-cards.jsonl` and `pundit-calls.jsonl` then agree by construction. It
    is required and has no default for the same reason `pundit_row`'s used to
    be — a default is how the hardcoded "intraday" survived unnoticed.
    """

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

    Sizing follows the #915 rule (#980). R = basis at the last re-base × f,
    from `portfolio.sizing.resolve_bet_unit`: the stored `[bet_sizing]` basis
    when one is set, else `resolve_capital`'s live-equity / config fallback
    with a warning that R is floating. f is 1% (measurement) until the config
    names an unlock. The same resolver gives the daily-loss breaker its R unit
    upstream in `state.py`, so one figure drives both.

    A card on a cluster member is one leg of a cluster entry: it takes
    R / len(cluster), whether or not the siblings trade, so a sibling's
    sub-lot skip is never reallocated here. A leg whose share floors to zero
    at `qty_step` is skipped (VETOED, naming rule 3), never sized up. Open
    risk is counted as one R per open cluster entry plus this bet and vetoes
    past `open_risk_ceiling_r` (the account rows carry no stop, so each entry
    counts its full R).

    `capital_used` / `capital_source` / `sizing_regime` are recorded on the
    returned `FinalCard` (`None` on a VETO) because `risk_frac` is only
    interpretable alongside the basis and regime that produced it.

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
    sizing_regime: str | None = None
    rr_tp1: float | None = None
    rr_tp1_net: float | None = None

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

        # (b) planned RR floor, gated on the NET number (ST93).
        # `min_rr` was a GROSS floor with no cost term, and the round-trip drag
        # carries `entry / risk` — so it is *inversely* proportional to stop
        # width and a tight-stop card cleared the hard rule while being
        # negative-EV at its own first target. The bias ran ONE way: the floor
        # only ever passed trades that should fail, hardest where stops were
        # tightest. From the 2026-08-25 batch: RR 1.10 on a 0.85% stop pays
        # 0.1647R and reads 0.94 net, where a 2% stop pays only 0.07. (The SoT
        # row filed that card at ~0.96, which is ~6 bps — the rounded prose in
        # `config.py`, not the constants.) Gating on net strictly tightens the
        # old rule — net < gross
        # always — so nothing that failed before starts passing.
        rpu = risk_per_unit(entry, sl)
        if rpu > 0.0 and card.tp1 is not None:
            rr_tp1 = abs(float(card.tp1) - entry) / rpu
            rr_tp1_net = rr_tp1 - round_trip_drag_r(entry, sl)
            if rr_tp1_net < cfg.min_rr:
                veto.append(
                    f"rr_tp1 {rr_tp1_net:.2f} net of cost ({rr_tp1:.2f} gross) "
                    f"breaches min_rr {cfg.min_rr}"
                )

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
            # (c2) cross-symbol open-risk ceiling (operator ruling on #980):
            # one R per open cluster entry plus this bet, never one per leg.
            open_r = open_risk_r(
                [p.symbol for p in state.account.positions], state.symbol, sizing
            )
            ceiling_r = open_risk_ceiling_r(sizing)
            if open_r > ceiling_r:
                veto.append(
                    f"open risk {open_r}R (open cluster entries plus this bet) "
                    f"exceeds the {ceiling_r:g}R ceiling (r_open_max)"
                )
            if state.account.positions:
                warnings.append(
                    "open risk counted as one R per open cluster entry — "
                    "account rows carry no stop"
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

        # sizing (#915 rule, #980) — only when nothing vetoed
        if not veto:
            equity = state.account.equity_usd if state.account is not None else None
            unit = resolve_bet_unit(sizing, equity)
            capital = unit.basis_usd
            capital_used = capital
            capital_source = unit.basis_source
            sizing_regime = unit.regime
            if unit.basis_source == "config":
                warnings.append(
                    f"sized off configured capital ${capital:,.2f} — no re-base "
                    "basis is set and account equity is unavailable, so R is "
                    "against a constant, not the account"
                )
            elif unit.basis_source == "live_equity":
                warnings.append(
                    "no re-base basis configured ([bet_sizing] basis_usd) — R "
                    "floats with live equity on every card instead of holding "
                    "constant between scheduled re-bases"
                )
            # One cluster entry is ONE bet: this leg takes 1/n of R, fixed
            # before any leg is sized, so a sibling's sub-lot skip never
            # flows back into this leg.
            legs = basket_legs(state.symbol, sizing)
            share = leg_share_usd(unit.r_usd, legs)
            if share <= 0.0:
                veto.append(
                    f"{unit.regime} book sizes to f = 0 — no net edge under half-Kelly"
                )
            else:
                rounded = qty_step is not None and qty_step > 0.0
                size_units = size_leg(share, entry, sl, qty_step)
                if rounded and size_units <= 0.0:
                    veto.append(
                        f"sub-lot leg skipped: its 1/{legs} share of R "
                        f"(${share:,.2f}) floors to zero at qty_step {qty_step}; "
                        "the share is left unused, never sized up or reallocated"
                    )
                else:
                    # Risk is restated from the (rounded) size, so the printed
                    # risk is the risk actually taken.
                    risk_usd = size_units * risk_per_unit(entry, sl)
                    risk_frac = risk_usd / capital
                    notional_usd = size_units * entry
                    if not rounded:
                        warnings.append(
                            "quantity is not LOT_SIZE-rounded (exchange filters "
                            "unavailable) — size and risk are pre-rounding"
                        )

    verdict = "VETOED" if veto else card.verdict
    if veto:
        size_units = notional_usd = risk_usd = risk_frac = rr_tp1 = None
        rr_tp1_net = None
        capital_used = capital_source = sizing_regime = None
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
        sizing_regime=sizing_regime,
        rr_tp1=rr_tp1,
        rr_tp1_net=rr_tp1_net,
        warnings=warnings,
        veto_reasons=veto,
        state_digest=digest,
        prompt_version=PROMPT_VERSION,
        model=model,
        generated_at_ms=generated_at_ms,
        cost_usd_notional=cost_usd_notional,
        horizon=cfg.horizon,
    )
