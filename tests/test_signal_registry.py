"""Tests for signal registry completeness and correctness."""

from analytics.indicators_lib import KNOWN_STRATEGIES
from signals.registry import SIGNAL_REGISTRY


def test_registry_excludes_seasonality() -> None:
    assert "seasonality" not in SIGNAL_REGISTRY


def test_registry_covers_all_non_seasonality_strategies() -> None:
    expected = {s for s in KNOWN_STRATEGIES if s != "seasonality"}
    assert set(SIGNAL_REGISTRY.keys()) == expected


def test_all_plugins_have_callable_detector() -> None:
    for name, plugin in SIGNAL_REGISTRY.items():
        assert callable(plugin["detector"]), f"{name} missing callable detector"


def test_all_plugins_have_boolean_flags() -> None:
    for name, plugin in SIGNAL_REGISTRY.items():
        assert isinstance(plugin["requires_funding"], bool), (
            f"{name}: requires_funding not bool"
        )
        assert isinstance(plugin["requires_secondary"], bool), (
            f"{name}: requires_secondary not bool"
        )


def test_funding_reversion_requires_funding() -> None:
    assert SIGNAL_REGISTRY["funding_reversion"]["requires_funding"] is True


def test_smt_divergence_requires_secondary() -> None:
    assert SIGNAL_REGISTRY["smt_divergence"]["requires_secondary"] is True


def test_no_strategy_requires_both_funding_and_secondary() -> None:
    for name, plugin in SIGNAL_REGISTRY.items():
        assert not (plugin["requires_funding"] and plugin["requires_secondary"]), (
            f"{name} claims both funding and secondary"
        )
