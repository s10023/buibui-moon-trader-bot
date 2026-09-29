"""Tests for portfolio.sizing — SizingConfig defaults/from_toml + pure sizing math."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from portfolio.sizing import (
    SizingConfig,
    resolve_capital,
    round_down_to_step,
    round_to_tick,
)


def test_sizing_config_defaults() -> None:
    cfg = SizingConfig()
    assert cfg.capital == 10_000.0
    assert cfg.r_base == pytest.approx(0.0025)
    assert cfg.vol_target_annual == pytest.approx(0.20)
    assert cfg.vol_window_days == 30
    assert cfg.g_vol_min == 0.5 and cfg.g_vol_max == 1.5
    assert cfg.r_open_max == pytest.approx(0.02)
    assert cfg.r_cluster_max == pytest.approx(0.01)
    assert cfg.high_vol_risk_mult == 0.5
    assert cfg.apply_high_vol_halving is True
    assert cfg.annualization_days == pytest.approx(365.0)
    assert ("BTCUSDT", "ETHUSDT", "SOLUSDT") in cfg.clusters


def test_sizing_config_from_toml_overrides(tmp_path: Path) -> None:
    p = tmp_path / "p.toml"
    p.write_text(
        "[portfolio]\n"
        "capital = 25000\n"
        "r_base = 0.005\n"
        "vol_target_annual = 0.15\n"
        'clusters = [["BTCUSDT", "ETHUSDT"]]\n',
        encoding="utf-8",
    )
    cfg = SizingConfig.from_toml(p)
    assert cfg.capital == 25_000.0
    assert cfg.r_base == pytest.approx(0.005)
    assert cfg.vol_target_annual == pytest.approx(0.15)
    assert cfg.clusters == (("BTCUSDT", "ETHUSDT"),)
    # unspecified keys keep defaults
    assert cfg.r_open_max == pytest.approx(0.02)


def test_sizing_config_from_toml_missing_block_is_defaults(
    tmp_path: Path,
) -> None:
    p = tmp_path / "empty.toml"
    p.write_text("[other]\nx = 1\n", encoding="utf-8")
    cfg = SizingConfig.from_toml(p)
    assert cfg == SizingConfig()


from portfolio.sizing import (  # noqa: E402
    apply_caps,
    cluster_of,
    effective_risk_fraction,
    position_size,
    regime_multiplier,
    risk_per_unit,
    vol_governor,
)


def test_risk_per_unit_and_position_size() -> None:
    assert risk_per_unit(100.0, 95.0) == pytest.approx(5.0)
    assert risk_per_unit(95.0, 100.0) == pytest.approx(5.0)
    # risk_capital 25 at 5/unit => 5 units
    assert position_size(25.0, 100.0, 95.0) == pytest.approx(5.0)
    assert position_size(25.0, 100.0, 100.0) == 0.0  # zero risk => no position


def test_vol_governor_clamps_and_cold_start() -> None:
    cfg = SizingConfig()
    # realized vol == target => g_vol 1.0
    assert vol_governor(0.20, cfg) == pytest.approx(1.0)
    # hot book (realized 0.40 vs target 0.20) => shrink, clamped at floor 0.5
    assert vol_governor(0.40, cfg) == pytest.approx(0.5)
    # cold book (realized 0.05) => expand, clamped at ceiling 1.5
    assert vol_governor(0.05, cfg) == pytest.approx(1.5)
    # undefined / non-positive vol => neutral 1.0 (cold start)
    assert vol_governor(0.0, cfg) == pytest.approx(1.0)
    assert vol_governor(float("nan"), cfg) == pytest.approx(1.0)


def test_regime_multiplier() -> None:
    cfg = SizingConfig()
    assert regime_multiplier("high_vol", cfg) == pytest.approx(0.5)
    assert regime_multiplier("trend", cfg) == pytest.approx(1.0)
    assert regime_multiplier(None, cfg) == pytest.approx(1.0)
    off = SizingConfig(apply_high_vol_halving=False)
    assert regime_multiplier("high_vol", off) == pytest.approx(1.0)


def test_effective_risk_fraction() -> None:
    cfg = SizingConfig()  # r_base 0.0025
    assert effective_risk_fraction(cfg, g_vol=1.0, g_regime=1.0) == pytest.approx(
        0.0025
    )
    assert effective_risk_fraction(cfg, g_vol=1.5, g_regime=0.5) == pytest.approx(
        0.0025 * 1.5 * 0.5
    )


def test_cluster_of() -> None:
    cfg = SizingConfig()
    # majors share one cluster id; non-majors get their own singleton
    assert cluster_of("BTCUSDT", cfg) == cluster_of("ETHUSDT", cfg)
    assert cluster_of("DOGEUSDT", cfg) == "DOGEUSDT"
    assert cluster_of("BTCUSDT", cfg) != "BTCUSDT"


def test_apply_caps_scales_down_to_fit() -> None:
    cfg = SizingConfig()  # r_open_max 0.02, r_cluster_max 0.01
    # plenty of headroom => unchanged
    assert apply_caps(
        0.0025, symbol="BTCUSDT", open_risk_total=0.0, open_risk_cluster=0.0, cfg=cfg
    ) == pytest.approx(0.0025)
    # cluster nearly full => scaled to remaining headroom
    assert apply_caps(
        0.0025,
        symbol="BTCUSDT",
        open_risk_total=0.005,
        open_risk_cluster=0.009,
        cfg=cfg,
    ) == pytest.approx(0.001)
    # total cap binds before cluster
    assert apply_caps(
        0.0025,
        symbol="DOGEUSDT",
        open_risk_total=0.0195,
        open_risk_cluster=0.0,
        cfg=cfg,
    ) == pytest.approx(0.0005)


def test_apply_caps_skip_floor() -> None:
    cfg = SizingConfig()  # skip_floor_frac 0.1 => floor 0.00025
    # headroom below floor => skip (0.0)
    assert (
        apply_caps(
            0.0025,
            symbol="BTCUSDT",
            open_risk_total=0.0199,
            open_risk_cluster=0.0,
            cfg=cfg,
        )
        == 0.0
    )


class TestRoundDownToStep:
    """Shared LOT_SIZE helper — trade/routing.py and the card post-pass must
    round identically, so there is exactly one implementation."""

    def test_floors_to_step_multiple(self) -> None:
        assert round_down_to_step(0.0571951498512928, 0.001) == pytest.approx(0.057)

    def test_exact_multiple_is_unchanged(self) -> None:
        assert round_down_to_step(12.5, 0.5) == pytest.approx(12.5)

    def test_below_one_step_floors_to_zero(self) -> None:
        assert round_down_to_step(0.4, 1.0) == 0.0

    def test_non_positive_step_is_passthrough(self) -> None:
        assert round_down_to_step(1.234, 0.0) == 1.234


class TestRoundDownToStepFloatShaving:
    """`floor(q / step) * step` is float-fragile: `0.29 / 0.01` computes as
    `28.999999999999996`, floors to 28, and returns `0.28` — a FULL step lost on
    a mathematically exact multiple.

    These enumerate their input class on purpose. The spot-checks above
    (`0.0571951498512928`, `12.5`) all passed while the defect stood, because the
    failures are scattered — a hand-picked value proves nothing here.
    """

    STEPS = (0.001, 0.01, 0.1, 1.0)

    def test_exact_multiples_are_never_shaved(self) -> None:
        """i * step must round-trip to itself, for every i and every step."""
        shaved: list[tuple[float, int, float]] = []
        for step in self.STEPS:
            for i in range(1, 2001):
                q = i * step
                got = round_down_to_step(q, step)
                if got < q - step / 2:
                    shaved.append((step, i, got))
        assert not shaved, f"{len(shaved)} exact multiples shaved, e.g. {shaved[:5]}"

    def test_difference_of_two_multiples_is_never_shaved(self) -> None:
        """The input class `trade/routing.py:102` actually receives.

        `delta_qty = round_down_to_step(target_qty - current, step)` where BOTH
        operands are already step multiples — so the difference is exactly a
        multiple mathematically, but the float subtraction lands just under it far
        more often than a direct `i * step` does.
        """
        shaved: list[tuple[float, int, int]] = []
        for step in self.STEPS:
            for t in range(200):
                for c in range(200):
                    if t == c:
                        continue
                    raw = t * step - c * step
                    if round_down_to_step(raw, step) < abs(raw) - step / 2:
                        shaved.append((step, t, c))
        assert not shaved, f"{len(shaved)} router deltas shaved, e.g. {shaved[:5]}"

    def test_one_step_delta_never_collapses_to_a_noop(self) -> None:
        """A delta of exactly one lot must survive rounding.

        If it floors to 0.0, `routing.py:117` routes it to `skip:noop` and the
        order is never sent — the position never converges on its target. That is
        a stall, not a one-step shave.
        """
        stalled: list[tuple[float, int]] = []
        for step in self.STEPS:
            for i in range(1, 2001):
                raw = i * step - (i - 1) * step
                if round_down_to_step(raw, step) == 0.0:
                    stalled.append((step, i))
        assert not stalled, (
            f"{len(stalled)} one-step deltas floored to 0, e.g. {stalled[:5]}"
        )

    def test_is_idempotent(self) -> None:
        """`routing.py:102` re-rounds an already-rounded target when current == 0,
        so a non-idempotent floor shaves even a flat book."""
        shaved: list[tuple[float, float]] = []
        for step in self.STEPS:
            for i in range(1, 1001):
                once = round_down_to_step(i * step + step / 3, step)
                if round_down_to_step(once, step) < once - step / 2:
                    shaved.append((step, once))
        assert not shaved, f"{len(shaved)} values shaved on re-round, e.g. {shaved[:5]}"

    def test_genuine_remainder_still_floors_down(self) -> None:
        """The mirror risk of any tolerance: it must NOT round a real sub-step
        remainder UP past the exchange filter, which would flip this helper from
        fail-safe to fail-open."""
        for step in self.STEPS:
            for i in range(500):
                for frac in (0.01, 0.5, 0.99):
                    q = (i + frac) * step
                    got = round_down_to_step(q, step)
                    assert got <= q + step * 1e-6, (
                        f"rounded UP: step={step} i={i} frac={frac} q={q!r} -> {got!r}"
                    )
                    assert got == pytest.approx(i * step, abs=step * 1e-6), (
                        f"wrong floor: step={step} i={i} frac={frac} q={q!r} -> {got!r}"
                    )


class TestResolveCapital:
    def test_live_equity_wins_over_config(self) -> None:
        cfg = SizingConfig(capital=10_000.0)
        capital, used_live = resolve_capital(cfg, 1201.33)
        assert capital == pytest.approx(1201.33)
        assert used_live is True

    def test_none_equity_falls_back_to_config(self) -> None:
        cfg = SizingConfig(capital=10_000.0)
        capital, used_live = resolve_capital(cfg, None)
        assert capital == pytest.approx(10_000.0)
        assert used_live is False

    @pytest.mark.parametrize(
        "equity",
        [0.0, -1.0, -1201.33, float("nan"), float("inf"), float("-inf")],
    )
    def test_degenerate_equity_falls_back_to_config(self, equity: float) -> None:
        # Enumerate the input class rather than spot-check it: a zero would size
        # every card to nothing and read as a lot-size veto, and a NaN would
        # poison risk_usd / risk_frac / notional_usd without ever raising.
        cfg = SizingConfig(capital=10_000.0)
        capital, used_live = resolve_capital(cfg, equity)
        assert capital == pytest.approx(10_000.0)
        assert used_live is False

    def test_tiny_positive_equity_is_honoured(self) -> None:
        # The fallback triggers on invalid, never on merely small: a $50 account
        # is a real account and must not silently size as if it held $10,000.
        cfg = SizingConfig(capital=10_000.0)
        capital, used_live = resolve_capital(cfg, 50.0)
        assert capital == pytest.approx(50.0)
        assert used_live is True


class TestSizingConfigGuard:
    """`SizingConfig` rejects degenerate operator input at CONSTRUCTION.

    Every numeric field here is operator-set — via the `[portfolio]` TOML table
    or `cli/portfolio.py`'s `--capital` / `--vol-target` flags — and each has a
    silent failure mode rather than a loud one. `capital = -5.0` is the worked
    example: `round_down_to_step` returns the magnitude by contract and the
    sign is never re-applied, so a negative capital yields a POSITIVE, entirely
    plausible TRADE card. A `nan` is worse in a different way: it propagates
    through `risk_usd` / `risk_frac` / `notional_usd` without raising, and the
    one place it does raise is *after* the paid-for LLM call.

    Enumerate the input class rather than spot-checking it — the same rule the
    LOT_SIZE shaving defect was missed by.
    """

    # Strictly positive: a zero is as wrong as a negative. `capital = 0` sizes
    # every trade to nothing and reads downstream as a lot-size veto; `r_base`
    # and `vol_target_annual` are divisors or scale factors whose zero silently
    # nulls the whole sizing layer instead of failing.
    POSITIVE = ("capital", "r_base", "vol_target_annual", "annualization_days")
    # Zero is a meaningful setting: "no allowance" / "no halving" / "no floor".
    NON_NEGATIVE = (
        "g_vol_min",
        "g_vol_max",
        "r_open_max",
        "r_cluster_max",
        "high_vol_risk_mult",
        "skip_floor_frac",
    )
    DEGENERATE = (float("nan"), float("inf"), float("-inf"))

    @pytest.mark.parametrize("field", POSITIVE)
    @pytest.mark.parametrize("value", (0.0, -1.0, *DEGENERATE))
    def test_positive_field_rejects_degenerate_value(
        self, field: str, value: float
    ) -> None:
        kwargs: dict[str, Any] = {field: value}
        with pytest.raises(ValueError, match=field):
            SizingConfig(**kwargs)

    @pytest.mark.parametrize("field", NON_NEGATIVE)
    @pytest.mark.parametrize("value", (-1.0, *DEGENERATE))
    def test_non_negative_field_rejects_degenerate_value(
        self, field: str, value: float
    ) -> None:
        kwargs: dict[str, Any] = {field: value}
        with pytest.raises(ValueError, match=field):
            SizingConfig(**kwargs)

    @pytest.mark.parametrize("field", NON_NEGATIVE)
    def test_non_negative_field_accepts_zero(self, field: str) -> None:
        # Positive control for the class above: without this, a guard that
        # rejected EVERYTHING would pass every rejection test.
        #
        # `g_vol_max=0.0` needs `g_vol_min` lowered with it — against the
        # default 0.5 it is a genuinely inverted pair and SHOULD be rejected,
        # which is the ordering invariant doing its job, not a zero being
        # refused.
        kwargs: dict[str, Any] = {field: 0.0}
        if field == "g_vol_max":
            kwargs["g_vol_min"] = 0.0
        assert getattr(SizingConfig(**kwargs), field) == 0.0

    @pytest.mark.parametrize("value", (0, -1, float("nan"), float("inf")))
    def test_vol_window_days_rejects_degenerate_value(self, value: float) -> None:
        # Typed `int`, but TOML is not type-checked at runtime, so a float
        # reaches the field unchallenged unless the guard rejects it.
        kwargs: dict[str, Any] = {"vol_window_days": value}
        with pytest.raises(ValueError, match="vol_window_days"):
            SizingConfig(**kwargs)

    def test_vol_governor_bounds_must_be_ordered(self) -> None:
        # min > max is silently absorbed by `vol_governor`'s clamp:
        # min(max(g, g_vol_min), g_vol_max) returns g_vol_max for EVERY input,
        # so the governor stops governing and nothing raises.
        with pytest.raises(ValueError, match="g_vol_min"):
            SizingConfig(g_vol_min=1.5, g_vol_max=0.5)

    def test_equal_vol_governor_bounds_are_allowed(self) -> None:
        # A pinned governor is a legitimate setting, not a misconfiguration.
        assert SizingConfig(g_vol_min=1.0, g_vol_max=1.0).g_vol_min == 1.0

    @pytest.mark.parametrize("field", (*POSITIVE, *NON_NEGATIVE))
    def test_numeric_field_rejects_bool(self, field: str) -> None:
        # `capital = true` in TOML passes both `isfinite` and `> 0` as 1.0 and
        # would size the whole book against $1. Same defect family as a bare
        # threshold whose units silently change: it type-checks and is wrong.
        kwargs: dict[str, Any] = {field: True}
        with pytest.raises(ValueError, match=field):
            SizingConfig(**kwargs)

    def test_tiny_positive_capital_is_honoured(self) -> None:
        # Smallness is not degeneracy — the same contract `resolve_capital`
        # already keeps for live equity. A $50 account is a real account.
        assert SizingConfig(capital=50.0).capital == 50.0

    def test_defaults_construct(self) -> None:
        # The guard must not reject the shipped defaults.
        assert SizingConfig().capital == 10_000.0

    def test_dataclasses_replace_is_guarded(self) -> None:
        # This is the live CLI path: `cli/portfolio.py:20` builds the override
        # with `replace(cfg, capital=float(args.capital))`, so `--capital -5`
        # must fail loudly at the flag rather than print a plausible report.
        with pytest.raises(ValueError, match="capital"):
            replace(SizingConfig(), capital=-5.0)

    def test_from_toml_is_guarded(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.toml"
        p.write_text("[portfolio]\ncapital = -5000\n", encoding="utf-8")
        with pytest.raises(ValueError, match="capital"):
            SizingConfig.from_toml(p)


def test_round_to_tick_enumerated_never_crosses() -> None:
    """BUY must never round up, SELL must never round down — at any tick.

    The one-sided bounds alone are not enough: a degenerate `return price`
    (or a BUY branch hardcoded to `0.0`) satisfies `buy <= price` /
    `sell >= price` on every iteration without ever rounding anything. The
    equality checks below pin the actual floor/ceil DIRECTION for every
    non-zero offset, where the expected value is unambiguous
    (`exact` for BUY, `exact + tick` for SELL). The zero-offset case is left
    to the one-sided bounds only, because there `buy == sell == exact` (the
    snap branch fires before `side` is even read) and the `exact + tick`
    expectation would not hold.
    """
    for tick in (0.0001, 0.01, 0.1, 1.0, 2.5):
        for mult in range(1, 400):
            exact = mult * tick
            for offset in (0.0, tick * 0.3, tick * 0.7, tick * 0.999):
                price = exact + offset
                buy = round_to_tick(price, tick, "BUY")
                sell = round_to_tick(price, tick, "SELL")
                assert buy <= price + 1e-12, f"BUY crossed: {price} {tick} -> {buy}"
                assert sell >= price - 1e-12, f"SELL crossed: {price} {tick} -> {sell}"
                if offset != 0.0:
                    assert buy == pytest.approx(exact), (
                        f"BUY did not floor to the tick: {price} {tick} -> {buy}, expected {exact}"
                    )
                    assert sell == pytest.approx(exact + tick), (
                        f"SELL did not ceil to the tick: {price} {tick} -> {sell}, "
                        f"expected {exact + tick}"
                    )


def test_round_to_tick_exact_multiple_is_returned_unchanged() -> None:
    """The case that matters most: an exchange bid/ask is ALREADY a tick multiple.

    A naive floor(price / tick) * tick loses a full tick here — 0.29 / 0.01
    computes as 28.999999999999996. That is the same defect round_down_to_step
    documents, and at the touch it would push the order a tick away from the
    queue position we asked for.
    """
    assert round_to_tick(0.29, 0.01, "BUY") == pytest.approx(0.29)
    assert round_to_tick(0.29, 0.01, "SELL") == pytest.approx(0.29)
    assert round_to_tick(3000.0, 0.01, "BUY") == pytest.approx(3000.0)
    assert round_to_tick(117.3, 0.1, "SELL") == pytest.approx(117.3)


def test_round_to_tick_non_positive_tick_passes_through() -> None:
    assert round_to_tick(123.456, 0.0, "BUY") == 123.456
    assert round_to_tick(123.456, -1.0, "SELL") == 123.456


def test_round_to_tick_invalid_side_raises() -> None:
    """A side that is neither BUY nor SELL must fail loudly, not fall through
    to the SELL (ceil) branch silently — the pre-fix behaviour."""
    with pytest.raises(ValueError, match="BUY.*SELL"):
        round_to_tick(100.0, 0.1, "HOLD")


@pytest.mark.parametrize(
    ("price_str", "tick", "decimals"),
    [
        ("45817.6", 0.1, 1),
        ("0.29", 0.01, 2),
        ("2412.75", 0.01, 2),
        ("0.014623", 0.00001, 5),
    ],
)
def test_round_to_tick_realistic_quote_is_not_over_precise(
    price_str: str, tick: float, decimals: int
) -> None:
    """Regression for the wire-precision defect. `trade/binance_futures.py`
    puts `round_to_tick`'s return value straight into `params["price"]`, and
    python-binance serialises it with a bare `str()` — so ANY float error
    beyond the tick's own decimal places goes onto the wire and Binance
    rejects it (-1111, precision beyond PRICE_FILTER).

    THE INPUT IS THE POINT: it is parsed from a decimal string literal —
    `float("45817.6")` — the same construction `get_book_tops` uses on a real
    quote (`float(r["bidPrice"])`). `test_round_to_tick_enumerated_never_crosses`
    above builds its fixture as `mult * tick`, the implementation's own
    arithmetic, so that fixture's input and the (pre-fix, broken)
    implementation's output are the same wrong float bit for bit and its
    crossing assertion is trivially satisfied — it cannot fail. `float("45817.6")`
    is a genuinely different float from any `mult * 0.1` product, so a
    regression to the bare `nearest * tick` / `floor(quotient) * tick` return
    is actually caught here: pre-fix, `round_to_tick(float("45817.6"), 0.1,
    "BUY")` returns `45817.600000000006` (17 significant digits on the wire,
    and itself ABOVE the input — a BUY that crosses); post-fix it returns
    exactly `45817.6`.
    """
    price = float(price_str)
    for side in ("BUY", "SELL"):
        result = round_to_tick(price, tick, side)
        if side == "BUY":
            assert result <= price + 1e-9, f"BUY crossed: {price} -> {result}"
        else:
            assert result >= price - 1e-9, f"SELL crossed: {price} -> {result}"
        text = str(result)
        got_decimals = len(text.split(".")[1]) if "." in text else 0
        assert got_decimals <= decimals, (
            f"{side} {price} @ tick {tick} -> {result!r} carries "
            f"{got_decimals} decimal places, more than the tick's {decimals} — "
            "Binance would reject this as -1111"
        )


from portfolio.sizing import (  # noqa: E402
    DEFAULT_FEE_PCT,
    DEFAULT_SLIPPAGE_PCT,
    round_trip_drag_r,
)


def test_round_trip_drag_r_matches_the_engine_arithmetic() -> None:
    """2 × (fee + slippage) × entry / risk — one spelling for every cost site."""
    # 2% stop at the repo's measured 7 bps round trip (fee 5 + slippage 2)
    assert round_trip_drag_r(100.0, 98.0) == pytest.approx(0.07)
    assert round_trip_drag_r(
        100.0, 98.0, fee_pct=DEFAULT_FEE_PCT, slippage_pct=DEFAULT_SLIPPAGE_PCT
    ) == pytest.approx(0.07)


def test_round_trip_drag_r_is_inverse_in_stop_width() -> None:
    """The drag concentrates on tight stops — halving the stop doubles the cost in R.

    This is the whole reason a GROSS RR floor passes exactly the trades it
    should reject, and why the bias runs one way.
    """
    wide = round_trip_drag_r(100.0, 98.0)  # 2.0% stop
    tight = round_trip_drag_r(100.0, 99.0)  # 1.0% stop
    assert tight == pytest.approx(2.0 * wide)


def test_round_trip_drag_r_zero_risk_is_zero() -> None:
    """Costs in R are undefined when nothing is risked — the resolver's convention."""
    assert round_trip_drag_r(100.0, 100.0) == 0.0
