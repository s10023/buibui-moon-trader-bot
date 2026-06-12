"""Tests for tools/select_universe.py — pure selection/ranking logic."""

from typing import Any

from tools.select_universe import (
    eligible_perps,
    format_universe_toml,
    rank_by_median_volume,
)

_DAY_MS = 86_400_000
_AS_OF = 1_000 * _DAY_MS  # arbitrary "today"


def _sym(
    symbol: str, *, base: str, onboard_days_ago: int, **over: Any
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "symbol": symbol,
        "baseAsset": base,
        "quoteAsset": "USDT",
        "contractType": "PERPETUAL",
        "status": "TRADING",
        "onboardDate": _AS_OF - onboard_days_ago * _DAY_MS,
    }
    d.update(over)
    return d


class TestEligiblePerps:
    def test_filters_young_stable_nonperp_nontrading(self) -> None:
        info = {
            "symbols": [
                _sym("BTCUSDT", base="BTC", onboard_days_ago=900),
                _sym("NEWUSDT", base="NEW", onboard_days_ago=100),  # too young
                _sym("USDCUSDT", base="USDC", onboard_days_ago=900),  # stable base
                _sym(
                    "ETHUSDT_2606",
                    base="ETH",
                    onboard_days_ago=900,
                    contractType="CURRENT_QUARTER",
                ),  # not a perp
                _sym(
                    "OLDUSDT",
                    base="OLD",
                    onboard_days_ago=900,
                    status="SETTLING",
                ),  # not trading
                _sym(
                    "ETHBTC",
                    base="ETH",
                    onboard_days_ago=900,
                    quoteAsset="BTC",
                ),  # wrong quote
            ]
        }
        out = eligible_perps(info, as_of_ms=_AS_OF, min_age_days=365)
        assert out == ["BTCUSDT"]


class TestRankByMedianVolume:
    def test_ranks_by_median_and_truncates(self) -> None:
        vols = {
            "AUSDT": [100.0, 100.0, 100.0],
            "BUSDT": [300.0, 300.0, 300.0],
            "CUSDT": [200.0, 1_000_000.0, 200.0],  # spike doesn't move the median
        }
        assert rank_by_median_volume(vols, top_n=2) == ["BUSDT", "CUSDT"]

    def test_empty_series_ranks_last(self) -> None:
        vols: dict[str, list[float]] = {"AUSDT": [100.0], "BUSDT": []}
        assert rank_by_median_volume(vols, top_n=2) == ["AUSDT", "BUSDT"]


class TestFormatUniverseToml:
    def test_emits_universe_block(self) -> None:
        out = format_universe_toml(
            ["BTCUSDT", "ETHUSDT"], selected_at="2026-06-12", criterion="test crit"
        )
        assert "[universe]" in out
        assert 'selected_at = "2026-06-12"' in out
        assert '"BTCUSDT",' in out
