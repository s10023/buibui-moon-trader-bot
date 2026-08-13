"""Two-layer position sizing (P1 spec §2) — pure math, no I/O.

Layer A: per-trade risk unit = (r_eff × equity) / |entry − stop|.
Layer B: r_eff = r_base × g_vol × g_regime × g_location × g_conviction,
then clipped by concurrent-risk and majors-cluster caps.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

_MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def _require_number(name: str, value: object, *, allow_zero: bool) -> None:
    """Reject a degenerate operator-set number, naming the field and the value.

    `bool` is excluded before the numeric check on purpose: it is an `int`
    subclass, so `capital = true` in TOML clears both `isfinite` and `> 0` as
    1.0 and would size the whole book against one dollar.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"[portfolio] {name} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"[portfolio] {name} must be finite, got {value!r}")
    if value < 0.0 or (value == 0.0 and not allow_zero):
        bound = "non-negative" if allow_zero else "positive"
        raise ValueError(f"[portfolio] {name} must be {bound}, got {value!r}")


@dataclass(frozen=True)
class SizingConfig:
    capital: float = 10_000.0
    r_base: float = 0.0025
    vol_target_annual: float = 0.20
    vol_window_days: int = 30
    g_vol_min: float = 0.5
    g_vol_max: float = 1.5
    r_open_max: float = 0.02
    r_cluster_max: float = 0.01
    high_vol_risk_mult: float = 0.5
    apply_high_vol_halving: bool = True
    skip_floor_frac: float = 0.1
    annualization_days: float = 365.0
    clusters: tuple[tuple[str, ...], ...] = (_MAJORS,)

    def __post_init__(self) -> None:
        """Fail loudly at construction on any degenerate numeric field.

        Every field below is operator-set — the `[portfolio]` TOML table, or
        `cli/portfolio.py`'s `--capital` / `--vol-target` flags — and each fails
        SILENTLY rather than loudly downstream. `capital = -5.0` is the worked
        example: `round_down_to_step` returns the magnitude by contract and the
        sign is never re-applied, so a negative capital produces a positive,
        entirely plausible TRADE card. A `nan` propagates through `risk_usd`,
        `risk_frac` and `notional_usd` without raising, and only surfaces
        *after* the paid-for LLM call.

        `dataclasses.replace` re-runs this, so the CLI override path and
        `from_toml` are both covered by construction rather than by their own
        checks. Smallness is never degeneracy — a $50 account is a real
        account, the same contract `resolve_capital` keeps for live equity.
        """
        for name in ("capital", "r_base", "vol_target_annual", "annualization_days"):
            _require_number(name, getattr(self, name), allow_zero=False)
        _require_number("vol_window_days", self.vol_window_days, allow_zero=False)
        for name in (
            "g_vol_min",
            "g_vol_max",
            "r_open_max",
            "r_cluster_max",
            "high_vol_risk_mult",
            "skip_floor_frac",
        ):
            # Zero is a real setting here: no allowance, no halving, no floor.
            _require_number(name, getattr(self, name), allow_zero=True)
        if self.g_vol_min > self.g_vol_max:
            # `vol_governor`'s clamp absorbs an inverted pair without raising:
            # min(max(g, g_vol_min), g_vol_max) returns g_vol_max for EVERY
            # input, so the governor silently stops governing.
            raise ValueError(
                f"[portfolio] g_vol_min ({self.g_vol_min}) must not exceed "
                f"g_vol_max ({self.g_vol_max})"
            )

    @classmethod
    def from_toml(cls, path: str | Path) -> SizingConfig:
        """Build a config from a TOML file's optional `[portfolio]` table.

        Missing keys keep dataclass defaults; `clusters` accepts a TOML array
        of arrays. An absent `[portfolio]` block yields plain defaults.
        """
        with open(Path(path), "rb") as f:
            data: dict[str, Any] = tomllib.load(f)
        block = data.get("portfolio", {})
        if not isinstance(block, dict):
            raise ValueError("[portfolio] must be a TOML table")
        cfg = cls()
        kwargs: dict[str, Any] = {}
        for field_name in (
            "capital",
            "r_base",
            "vol_target_annual",
            "vol_window_days",
            "g_vol_min",
            "g_vol_max",
            "r_open_max",
            "r_cluster_max",
            "high_vol_risk_mult",
            "apply_high_vol_halving",
            "skip_floor_frac",
            "annualization_days",
        ):
            if field_name in block:
                kwargs[field_name] = block[field_name]
        if "clusters" in block:
            kwargs["clusters"] = tuple(
                tuple(str(s) for s in c) for c in block["clusters"]
            )
        return replace(cfg, **kwargs)


def risk_per_unit(entry: float, stop: float) -> float:
    """Per-unit risk in price terms = |entry − stop|."""
    return abs(entry - stop)


def position_size(risk_capital: float, entry: float, stop: float) -> float:
    """Units = risk_capital / |entry − stop| (0.0 when risk is undefined)."""
    rpu = risk_per_unit(entry, stop)
    return risk_capital / rpu if rpu > 0.0 else 0.0


def resolve_capital(cfg: SizingConfig, equity_usd: float | None) -> tuple[float, bool]:
    """Capital to size against, plus whether it came from live equity.

    Live account equity wins whenever it is a usable number; `cfg.capital` is the
    fallback for every other case. Returns the source as a flag rather than
    logging, so this stays pure and each caller decides how to surface it.

    The fallback covers `None` (no account: a pinned `--as-of` run omits it by
    design, and a credentials or network failure leaves it absent too) and every
    degenerate float. Non-finite and non-positive values must NOT propagate: a
    `0.0` sizes every card to zero and reads downstream as a lot-size veto, and a
    `NaN` poisons `risk_usd`, `risk_frac` and `notional_usd` without raising.
    Smallness is not degeneracy — a genuinely tiny account is honoured.
    """
    if equity_usd is not None and math.isfinite(equity_usd) and equity_usd > 0.0:
        return float(equity_usd), True
    return cfg.capital, False


_STEP_SNAP_REL_TOL = 1e-9
"""Quotient-space window for treating a float as an exact step multiple.

~1e6x the few-ULP error a division or subtraction actually accumulates, and
capped below at a half step by `_STEP_SNAP_MAX_TOL`, so the snap can only ever
recover float noise — never a real remainder.
"""

_STEP_SNAP_MAX_TOL = 1e-6
"""Hard cap on the snap window, in quotient units.

Without it the relative tolerance grows with the quotient and would reach a half
step around `qty / step ~ 5e8`, silently flipping this helper from fail-safe to
fail-open on very large positions.
"""


def round_down_to_step(qty: float, step: float) -> float:
    """Floor |qty| to a multiple of an exchange LOT_SIZE step.

    Shared by the XS executor's order router and the card post-pass so both
    round identically. Returns the magnitude — callers re-apply any sign. A
    non-positive step means "unknown filter" and passes through unchanged.

    A plain `floor(abs(qty) / step)` is float-fragile: `0.29 / 0.01` computes as
    `28.999999999999996`, floors to 28, and returns `0.28` — a FULL step lost on
    a mathematically exact multiple. That bites hardest in `trade/routing.py`,
    whose `target_qty - current` is a difference of two step multiples and so is
    an exact multiple every time. So snap to the nearest multiple when the
    quotient is within float noise of one, and floor otherwise.
    """
    if step <= 0:
        return qty
    quotient = abs(qty) / step
    nearest = round(quotient)
    tolerance = min(_STEP_SNAP_REL_TOL * max(1.0, quotient), _STEP_SNAP_MAX_TOL)
    if abs(quotient - nearest) <= tolerance:
        return nearest * step
    return math.floor(quotient) * step


def _tick_decimals(tick: float) -> int:
    """Decimal places implied by a PRICE_FILTER tick.

    Via `Decimal(str(tick))` because the float's own repr is unreliable at
    small ticks — `str(0.00001)` is `'1e-05'`; `Decimal` reads the exponent
    exactly. `.normalize()` strips the trailing-zero artifact of `str()` on a
    whole-number tick (`str(1.0)` is `'1.0'`, not `'1'`), so a 1.0 tick reads
    0 decimals, not 1.
    """
    exponent = Decimal(str(tick)).normalize().as_tuple().exponent
    return max(0, -int(exponent))


def round_to_tick(price: float, tick: float, side: str) -> float:
    """Round a limit price to the symbol's PRICE_FILTER tick, passively.

    Side-dependent by necessity: a BUY resting above the tick it asked for, or a
    SELL below it, CROSSES the spread — and a post-only (GTX) order that would
    cross is rejected outright by the exchange, so the leg silently does not
    trade. BUY therefore floors, SELL ceils.

    Shares `round_down_to_step`'s snap-before-round for the same reason: the
    exchange's own bid/ask is already an exact tick multiple, and a plain
    `floor(price / tick)` loses a full tick on exactly that input
    (`0.29 / 0.01` -> `28.999999999999996`). A non-positive tick means "unknown
    filter" and passes through unchanged.

    The return value is then quantised to the tick's own decimal precision
    (`_tick_decimals`) — not just the arithmetic direction. `nearest * tick` /
    `floor(quotient) * tick` / `ceil(quotient) * tick` all carry float error
    (`floor(45817.6 / 0.1) * 0.1 == 45817.600000000006`), and the adapter puts
    that float straight into `params["price"]`, which python-binance
    serialises with a bare `str()` — so the error goes on the wire and Binance
    rejects it (-1111, precision beyond PRICE_FILTER).

    Quantising cannot itself cross the touch: in exact decimal arithmetic,
    `floor(quotient) * tick` (or `ceil`/`nearest`) is an integer times a
    `_tick_decimals(tick)`-place number, so it is ALREADY representable in
    that many decimal places — its only float error is the ~1e-10-relative
    noise of one multiplication, many orders of magnitude below the
    half-a-unit-at-that-decimal-place threshold `round()` rounds against.
    `round(value, decimals)` therefore recovers the exact intended decimal,
    never the next tick over, so a BUY that floored still floors and a SELL
    that ceiled still ceils.
    """
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
    if tick <= 0:
        return price
    quotient = price / tick
    nearest = round(quotient)
    tolerance = min(_STEP_SNAP_REL_TOL * max(1.0, quotient), _STEP_SNAP_MAX_TOL)
    decimals = _tick_decimals(tick)
    if abs(quotient - nearest) <= tolerance:
        return round(nearest * tick, decimals)
    if side == "BUY":
        return round(math.floor(quotient) * tick, decimals)
    return round(math.ceil(quotient) * tick, decimals)


def vol_governor(realized_vol_annual: float, cfg: SizingConfig) -> float:
    """g_vol = clamp(target / realized, [g_vol_min, g_vol_max]).

    Non-finite or non-positive realized vol (cold start) → neutral 1.0.
    """
    if not math.isfinite(realized_vol_annual) or realized_vol_annual <= 0.0:
        return 1.0
    g = cfg.vol_target_annual / realized_vol_annual
    return float(min(max(g, cfg.g_vol_min), cfg.g_vol_max))


def regime_multiplier(regime_label: str | None, cfg: SizingConfig) -> float:
    """high_vol → high_vol_risk_mult (when enabled); everything else → 1.0."""
    if cfg.apply_high_vol_halving and regime_label == "high_vol":
        return cfg.high_vol_risk_mult
    return 1.0


def effective_risk_fraction(
    cfg: SizingConfig,
    *,
    g_vol: float,
    g_regime: float,
    g_location: float = 1.0,
    g_conviction: float = 1.0,
) -> float:
    """r_eff = r_base × g_vol × g_regime × g_location × g_conviction (pre-cap)."""
    return cfg.r_base * g_vol * g_regime * g_location * g_conviction


def cluster_of(symbol: str, cfg: SizingConfig) -> str:
    """Cluster id for a symbol: the joined members for a configured cluster it
    belongs to, else the symbol itself (its own singleton cluster)."""
    for members in cfg.clusters:
        if symbol in members:
            return "|".join(members)
    return symbol


def apply_caps(
    r_eff: float,
    *,
    symbol: str,
    open_risk_total: float,
    open_risk_cluster: float,
    cfg: SizingConfig,
) -> float:
    """Clip r_eff by concurrent-risk + cluster headroom; scale-down-to-fit.

    Returns the admissible r_eff, or 0.0 when the remaining headroom is below
    `skip_floor_frac × r_base` (skip rather than open a dust position).
    """
    headroom_total = max(cfg.r_open_max - open_risk_total, 0.0)
    headroom_cluster = max(cfg.r_cluster_max - open_risk_cluster, 0.0)
    allowed = min(r_eff, headroom_total, headroom_cluster)
    if allowed < cfg.skip_floor_frac * cfg.r_base:
        return 0.0
    return allowed
