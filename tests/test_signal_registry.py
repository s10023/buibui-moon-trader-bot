"""Tests for signal registry completeness and correctness."""

from analytics.indicators_lib import KNOWN_STRATEGIES, STRATEGY_REGISTRY
from signals.registry import SIGNAL_REGISTRY


def test_registry_excludes_seasonality() -> None:
    assert "seasonality" not in SIGNAL_REGISTRY


def test_registry_covers_all_non_seasonality_strategies() -> None:
    expected = {s for s in KNOWN_STRATEGIES if s != "seasonality"}
    assert set(SIGNAL_REGISTRY.keys()) == expected


def test_all_plugins_have_callable_detector() -> None:
    for name, plugin in SIGNAL_REGISTRY.items():
        assert callable(plugin["detector"]), f"{name} missing callable detector"


def test_all_strategies_have_boolean_flags_in_strategy_registry() -> None:
    for name in SIGNAL_REGISTRY:
        spec = STRATEGY_REGISTRY[name]
        assert isinstance(spec.requires_funding, bool), (
            f"{name}: requires_funding not bool"
        )
        assert isinstance(spec.requires_secondary, bool), (
            f"{name}: requires_secondary not bool"
        )


def test_funding_reversion_requires_funding() -> None:
    assert STRATEGY_REGISTRY["funding_reversion"].requires_funding is True


def test_smt_divergence_requires_secondary() -> None:
    assert STRATEGY_REGISTRY["smt_divergence"].requires_secondary is True


def test_no_strategy_requires_both_funding_and_secondary() -> None:
    for name in SIGNAL_REGISTRY:
        spec = STRATEGY_REGISTRY[name]
        assert not (spec.requires_funding and spec.requires_secondary), (
            f"{name} claims both funding and secondary"
        )


def test_all_strategies_have_valid_confidence() -> None:
    # Confidence is now per-TF dict or plain int. Validate all resolved values are 1–5.
    sample_tfs = ["15m", "1h", "4h", "1d"]
    for name, spec in STRATEGY_REGISTRY.items():
        for tf in sample_tfs:
            c = spec.get_confidence(tf)
            assert isinstance(c, int) and 1 <= c <= 5, (
                f"{name}/{tf}: confidence must be 1–5, got {c}"
            )
