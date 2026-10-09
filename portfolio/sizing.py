"""Position sizing — pure math, no I/O.

Two models live here. The P1 two-layer model (spec §2), which the paper
portfolio replay uses:

Layer A: per-trade risk unit = (r_eff × equity) / |entry − stop|.
Layer B: r_eff = r_base × g_vol × g_regime × g_location × g_conviction,
then clipped by concurrent-risk and majors-cluster caps.

And the #915 bet-sizing rule both trading books adopt (#980), which the AI
card sizes under: a fixed R per bet = basis at the last re-base × f, one R per
cluster entry split across its legs, sub-lot legs skipped with their share
unused, a 1R daily loss cap, and f = 1% until a book unlocks on evidence.
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from statistics import NormalDist
from typing import Any

_MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

# --- #915 bet-sizing rule: constants ---------------------------------------

MEASUREMENT_F = 0.01
"""#915 rule 5: f before a book unlocks — 1% of the re-base basis per bet."""

DAILY_LOSS_CAP_R = 1.0
"""#915 rule 2: the daily loss cap, in bet R, that backstops baskets fired one
after another on the same move. `card.config.CardConfig.daily_loss_limit_r`
defaults to its negative."""

P_WIN_CAP = 0.45
"""#915 rule 4: p = min(lower Wilson bound, this) — stops a small, possibly
selective journal from sizing above ~20%."""

STREAK_ALPHA = 0.05
"""#915 rule 4: k is the smallest losing streak with P(run >= k in N) <= this."""

STREAK_TABLE_N = (100, 250, 500, 1000, 2000)
"""The N rows of #914's streak table (docs/research/2026-10-07-streak-sizing.md).
#915 rounds a book's expected 12-month bet count UP to the next row."""

MANUAL_BOOK_N = 100
"""#915 rule 4: the manual book's N. A bot construction's N is written into
its pre-registration instead."""

WILSON_Z = NormalDist().inv_cdf(0.975)
"""The z of the lower end of a two-sided 95% Wilson interval (~1.95996)."""

REGIME_MEASUREMENT = "measurement"
REGIME_UNLOCKED = "unlocked"


def _require_int(name: str, value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"[bet_sizing] {name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True)
class BookUnlock:
    """The evidence that unlocked a book (#915 Unlock), named rather than inferred.

    Present only when the operator has written it into config: the manual book
    needs 60+ journaled episodes, a completeness declaration and a positive
    bootstrap lower bound; a bot construction needs the three-leg gate at its
    round's trial count. Nothing here re-checks that evidence — `evidence`
    records where it is, and `wins` / `n` / `rr_gross` / `stop_pct` are the
    construction's own numbers that rule 4 sizes from.
    """

    evidence: str
    wins: int
    n: int
    rr_gross: float
    """The construction's planned RR, gross; rule 4 nets cost out of it."""
    stop_pct: float
    """Typical stop width as a fraction of entry — what the cost drag scales by."""
    bets_per_year: int = MANUAL_BOOK_N

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("[bet_sizing.unlock] evidence must name the evidence")
        _require_int("unlock.n", self.n, minimum=1)
        _require_int("unlock.wins", self.wins, minimum=0)
        if self.wins > self.n:
            raise ValueError(
                f"[bet_sizing.unlock] wins ({self.wins}) exceeds n ({self.n})"
            )
        _require_int("unlock.bets_per_year", self.bets_per_year, minimum=1)
        _require_number("unlock.rr_gross", self.rr_gross, allow_zero=False)
        _require_number("unlock.stop_pct", self.stop_pct, allow_zero=False)
        if self.stop_pct >= 1.0:
            raise ValueError(
                f"[bet_sizing.unlock] stop_pct is a fraction, got {self.stop_pct!r}"
            )

    @property
    def p(self) -> float:
        return sizing_p(self.wins, self.n)

    @property
    def rr_net(self) -> float:
        return rr_net_of_cost(self.rr_gross, self.stop_pct)

    @property
    def f(self) -> float:
        return unlocked_f(self.p, self.rr_net, self.bets_per_year)


@dataclass(frozen=True)
class BetSizingRule:
    """The #915 rule's configured state: the re-base basis and the unlock.

    `basis_usd` is equity at the last scheduled re-base (each monthly top-up,
    any withdrawal) and `rebased_at` its date. Both are stored inputs on
    purpose: reading live equity at each card would re-base after every loss,
    which is the fixed-fraction behaviour rule 1 forbids. Unset, the card falls
    back to `resolve_capital` and says so in a warning.

    `unlock` is None by default, so the default is the 1% measurement size.
    """

    basis_usd: float | None = None
    rebased_at: str | None = None
    unlock: BookUnlock | None = None
    open_risk_max_r: float | None = None
    """Ceiling on open risk, in bet R, that a new bet may not push past.

    None derives it from `[portfolio] r_open_max` (see `open_risk_ceiling_r`).
    """

    def __post_init__(self) -> None:
        if self.open_risk_max_r is not None:
            _require_number(
                "bet_sizing.open_risk_max_r", self.open_risk_max_r, allow_zero=True
            )
        if (self.basis_usd is None) != (self.rebased_at is None):
            raise ValueError(
                "[bet_sizing] basis_usd and rebased_at are set together: a basis "
                "without its re-base date cannot be audited against the schedule"
            )
        if self.basis_usd is not None:
            _require_number("bet_sizing.basis_usd", self.basis_usd, allow_zero=False)
        if self.rebased_at is not None:
            try:
                date.fromisoformat(self.rebased_at)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"[bet_sizing] rebased_at must be an ISO date, got "
                    f"{self.rebased_at!r}"
                ) from exc

    @property
    def regime(self) -> str:
        return REGIME_UNLOCKED if self.unlock is not None else REGIME_MEASUREMENT

    @property
    def f(self) -> float:
        """Risk per bet as a fraction of the basis (rules 4 and 5)."""
        return self.unlock.f if self.unlock is not None else MEASUREMENT_F

    @classmethod
    def from_block(cls, block: object) -> BetSizingRule:
        """Build from a TOML `[bet_sizing]` table; unknown keys raise."""
        if not isinstance(block, dict):
            raise ValueError("[bet_sizing] must be a TOML table")
        unknown = set(block) - {"basis_usd", "rebased_at", "unlock", "open_risk_max_r"}
        if unknown:
            raise ValueError(f"[bet_sizing] unknown keys: {sorted(unknown)}")
        unlock_block = block.get("unlock")
        unlock: BookUnlock | None = None
        if unlock_block is not None:
            if not isinstance(unlock_block, dict):
                raise ValueError("[bet_sizing.unlock] must be a TOML table")
            known = {
                "evidence",
                "wins",
                "n",
                "rr_gross",
                "stop_pct",
                "bets_per_year",
            }
            bad = set(unlock_block) - known
            if bad:
                raise ValueError(f"[bet_sizing.unlock] unknown keys: {sorted(bad)}")
            missing = known - {"bets_per_year"} - set(unlock_block)
            if missing:
                raise ValueError(f"[bet_sizing.unlock] missing keys: {sorted(missing)}")
            unlock = BookUnlock(**unlock_block)
        rebased_at = block.get("rebased_at")
        if isinstance(rebased_at, date):
            rebased_at = rebased_at.isoformat()  # TOML parses a bare date
        return cls(
            basis_usd=block.get("basis_usd"),
            rebased_at=rebased_at,
            unlock=unlock,
            open_risk_max_r=block.get("open_risk_max_r"),
        )


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
    bet_rule: BetSizingRule = field(default_factory=BetSizingRule)
    """The #915 rule the card sizes under (`[bet_sizing]`); the P1 replay
    ignores it."""

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
        of arrays. An absent `[portfolio]` block yields plain defaults. An
        optional top-level `[bet_sizing]` table (with `[bet_sizing.unlock]`)
        sets `bet_rule`; absent, the rule sizes at the measurement f.
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
        if "bet_sizing" in data:
            kwargs["bet_rule"] = BetSizingRule.from_block(data["bet_sizing"])
        return replace(cfg, **kwargs)


def risk_per_unit(entry: float, stop: float) -> float:
    """Per-unit risk in price terms = |entry − stop|."""
    return abs(entry - stop)


# Round-trip cost constants. Measured and cross-verified two disjoint ways on
# 2026-08-14: back-solving from all 940 post-parity loss rows returns exactly
# 7.000 bps on 663 of them (mean 6.929, sd 0.561), and `config/strategy_params.toml`
# carries `fee_pct = 0.0005` plus `slippage_bps = 2.0` — the same 7 bps.
DEFAULT_FEE_PCT = 0.000_5
DEFAULT_SLIPPAGE_PCT = 0.000_2


def round_trip_drag_r(
    entry: float,
    stop: float,
    *,
    fee_pct: float = DEFAULT_FEE_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
) -> float:
    """Round-trip fee + slippage cost of one trade, in R.

    ``2 × (fee + slippage) × entry / |entry − stop|`` — the drag that
    `analytics.backtest.engine.Trade.pnl_r` has subtracted since cost parity, and
    that `analytics.signal.outcome_backfill._net_outcome_r` and
    `portfolio.replay.restate_gross_r` each re-spell. It lives here so a fourth
    consumer imports it instead of adding a fourth spelling — the failure mode the
    powered-null family reached six sites by repeating.

    Because the drag carries ``entry / risk`` it is *inversely* proportional to
    stop width, so a narrow stop pays far more of it in R. That is what makes a
    GROSS RR floor pass exactly the trades it should reject.

    Funding is deliberately excluded: it depends on hold time, which a planned
    trade does not have yet. A zero-risk trade returns 0.0 — costs in R are
    undefined when nothing is risked, the resolver's own convention.
    """
    risk = abs(entry - stop)
    if risk <= 0.0:
        return 0.0
    return 2.0 * (fee_pct + slippage_pct) * entry / risk


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


# --- #915 bet-sizing rule: functions ---------------------------------------


def p_losing_run_at_least(n_bets: int, k: int, q: float) -> float:
    """P(at least one run of >= k straight losses in n independent bets).

    Exact, by the classical recurrence for "no run of k" that #914 cross-checked
    against its Markov chain to 1e-10: a(m) = 1 for m < k, a(k) = 1 - q^k, and
    a(m) = a(m-1) - p·q^k·a(m-k-1) beyond. `q` is the loss probability.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be a probability, got {q}")
    if n_bets < k:
        return 0.0
    p = 1.0 - q
    qk = q**k
    a = [1.0] * k
    a.append(1.0 - qk)
    for m in range(k + 1, n_bets + 1):
        a.append(a[m - 1] - p * qk * a[m - k - 1])
    return 1.0 - a[n_bets]


def streak_table_n(bets_per_year: int) -> int:
    """#915: N rounded UP to the next row of #914's table.

    Past the last row there is no next row, so N is used as is — the exact
    computation the table was built from, and the conservative reading.
    """
    if bets_per_year < 1:
        raise ValueError(f"bets_per_year must be >= 1, got {bets_per_year}")
    for row in STREAK_TABLE_N:
        if bets_per_year <= row:
            return row
    return bets_per_year


def streak_k(bets_per_year: int, p_win: float, alpha: float = STREAK_ALPHA) -> int:
    """Smallest k with P(run >= k in N) <= alpha, N from `streak_table_n`.

    Reproduces every cell of #914's alpha = 5% table (pinned in tests).
    """
    if not 0.0 < p_win <= 1.0:
        raise ValueError(f"p_win must be in (0, 1], got {p_win}")
    n = streak_table_n(bets_per_year)
    q = 1.0 - p_win
    k = 1
    while p_losing_run_at_least(n, k, q) > alpha:
        k += 1
    return k


def wilson_lower_bound(wins: int, n: int, z: float = WILSON_Z) -> float:
    """Lower end of the Wilson score interval for a win rate of wins / n."""
    if n < 1 or not 0 <= wins <= n:
        raise ValueError(f"need 0 <= wins <= n and n >= 1, got {wins}/{n}")
    if wins == 0:
        return 0.0  # exact; the formula leaves ~1e-17 behind
    phat = wins / n
    z2 = z * z
    centre = phat + z2 / (2 * n)
    spread = z * math.sqrt(phat * (1.0 - phat) / n + z2 / (4 * n * n))
    return max(0.0, (centre - spread) / (1.0 + z2 / n))


def sizing_p(wins: int, n: int) -> float:
    """#915 rule 4: p = min(lower 95% Wilson bound, `P_WIN_CAP`).

    Every losing bet counts as a full 1R loss, time-stops included — so `wins`
    must count wins only, never a scratch or a small time-stop gain as a loss
    avoided.
    """
    return min(wilson_lower_bound(wins, n), P_WIN_CAP)


def rr_net_of_cost(rr_gross: float, stop_pct: float) -> float:
    """RR net of the modelled round-trip cost at a stop `stop_pct` from entry.

    Delegates to `round_trip_drag_r` on a unit entry, so the drag is the one
    spelling everything else uses.
    """
    return rr_gross - round_trip_drag_r(1.0, 1.0 - stop_pct)


def half_kelly(p: float, rr_net: float) -> float:
    """Half the Kelly fraction for a bet winning `rr_net` R with probability p.

    Kelly = p - (1 - p) / RR, floored at zero: a construction with no net edge
    sizes to zero, so the rule refuses negative expectancy by itself.
    """
    if rr_net <= 0.0:
        return 0.0
    kelly = p - (1.0 - p) / rr_net
    # Break-even is exactly zero in exact arithmetic but float division leaves
    # ~1e-17 behind (p 0.40 at RR 1.5); that residue is not an edge.
    if kelly <= _KELLY_ZERO_TOL:
        return 0.0
    return kelly / 2.0


_KELLY_ZERO_TOL = 1e-12


def unlocked_f(p: float, rr_net: float, bets_per_year: int) -> float:
    """#915 rule 4: f = min(1/k, half-Kelly) for an unlocked book."""
    hk = half_kelly(p, rr_net)
    if hk <= 0.0:
        return 0.0  # no net edge; k is moot
    return min(1.0 / streak_k(bets_per_year, p), hk)


@dataclass(frozen=True)
class BetUnit:
    """One bet's R, and where its two factors came from."""

    r_usd: float
    f: float
    regime: str
    basis_usd: float
    basis_source: str
    """"rebase" (the stored basis), else `resolve_capital`'s "live_equity" /
    "config" fallback while no re-base basis is configured."""


def resolve_bet_unit(cfg: SizingConfig, equity_usd: float | None) -> BetUnit:
    """#915 rule 1: R = basis at the last re-base × f.

    The stored basis wins. Without one, `resolve_capital` supplies a fallback
    and `basis_source` names it, so a caller can say R is floating.
    """
    rule = cfg.bet_rule
    if rule.basis_usd is not None:
        basis, source = float(rule.basis_usd), "rebase"
    else:
        basis, used_live = resolve_capital(cfg, equity_usd)
        source = "live_equity" if used_live else "config"
    f = rule.f
    return BetUnit(
        r_usd=basis * f,
        f=f,
        regime=rule.regime,
        basis_usd=basis,
        basis_source=source,
    )


def basket_legs(symbol: str, cfg: SizingConfig) -> int:
    """#915 rule 2: how many legs one cluster entry splits its R across.

    A member of a configured cluster is one leg of the whole cluster's basket;
    any other symbol is a basket of one.
    """
    for members in cfg.clusters:
        if symbol in members:
            return len(members)
    return 1


def leg_share_usd(r_usd: float, n_legs: int) -> float:
    """#915 rule 2: one R per cluster entry, split evenly across its legs."""
    if n_legs < 1:
        raise ValueError(f"n_legs must be >= 1, got {n_legs}")
    return r_usd / n_legs


def open_risk_ceiling_r(cfg: SizingConfig) -> float:
    """The cap on open risk across all positions, in bet R (#980 follow-up).

    `r_open_max` is a fraction of equity, and the P1 replay still reads it as
    one, so it is NOT redefined. The card converts it at the measurement size:
    `r_open_max / MEASUREMENT_F`, i.e. 2R at the shipped 2%, which is the same
    dollar ceiling the old card enforced. `[bet_sizing] open_risk_max_r` sets
    the R count directly and wins; the R count holds when a book unlocks,
    where a fraction of equity would fall below one bet.
    """
    rule = cfg.bet_rule
    if rule.open_risk_max_r is not None:
        return float(rule.open_risk_max_r)
    return cfg.r_open_max / MEASUREMENT_F


def open_risk_r(open_symbols: Sequence[str], new_symbol: str, cfg: SizingConfig) -> int:
    """Open risk in bet R once a bet on `new_symbol` is added.

    One R per distinct cluster entry: the open legs of one BTC/ETH/SOL basket
    are one bet, and a card on a cluster that is already open is a leg of that
    entry, not a second R (sequential baskets on one move are what the 1R
    daily cap backstops). Account rows carry no stop, so each entry is counted
    at its full R, never at its actual distance to stop.
    """
    clusters = {cluster_of(s, cfg) for s in open_symbols}
    clusters.add(cluster_of(new_symbol, cfg))
    return len(clusters)


def size_leg(
    share_usd: float, entry: float, stop: float, qty_step: float | None
) -> float:
    """Units for one leg's share of R, floored to the LOT_SIZE step.

    0.0 means sub-lot: #915 rule 3 skips the leg and never sizes it up.
    """
    units = position_size(share_usd, entry, stop)
    if qty_step is not None and qty_step > 0.0:
        units = round_down_to_step(units, qty_step)
    return units


@dataclass(frozen=True)
class BasketLeg:
    symbol: str
    entry: float
    stop: float
    qty_step: float | None


def size_basket(legs: Sequence[BasketLeg], r_usd: float) -> list[float]:
    """#915 rules 2-3: one R across the basket; a sub-lot leg is skipped.

    Every leg's share is fixed at R / len(legs) BEFORE any leg is sized, so a
    skipped leg's share stays unused rather than flowing to the others, and a
    skipped leg never skips the rest.
    """
    share = leg_share_usd(r_usd, len(legs))
    return [size_leg(share, leg.entry, leg.stop, leg.qty_step) for leg in legs]
