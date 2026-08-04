"""Shared state-audit machinery: the extraction must be behaviour-preserving."""

from __future__ import annotations

from typing import Any

import numpy as np

from analytics.audit_guard import (
    DECISION_CONCENTRATE,
    DECISION_DISABLE,
    DECISION_ENABLE,
    DECISION_INSUFFICIENT,
    AuditCell,
)
from analytics.state_audit import (
    MIN_N,
    VERDICT_AVOID,
    VERDICT_BUILD,
    VERDICT_INSUFFICIENT,
    VERDICT_NO_EDGE,
    cell_sharpe,
    evaluate_states,
    map_verdict,
    sign_agrees_early_late,
)


def _family_key(label: str) -> tuple[str, str]:
    state, direction = label.rsplit("|", 1)
    return ("axis_a" if state.startswith("a_") else "axis_b"), direction


def test_map_verdict_inverts_audit_guard_sign() -> None:
    """DISABLE means the slice is reliably POSITIVE -> BUILD. Do not 'fix' this."""
    kw: dict[str, Any] = {
        "n_supp": 100,
        "n_days_ok": True,
        "dsr": 0.99,
        "pbo": 0.1,
        "stable": True,
    }
    assert map_verdict(DECISION_DISABLE, **kw) == VERDICT_BUILD
    assert map_verdict(DECISION_ENABLE, **kw) == VERDICT_AVOID
    assert map_verdict(DECISION_CONCENTRATE, **kw) == VERDICT_NO_EDGE


def test_map_verdict_splits_insufficient_on_n() -> None:
    """Underpowered is INSUFFICIENT; powered-but-null is NO-EDGE."""
    kw: dict[str, Any] = {
        "n_days_ok": False,
        "dsr": None,
        "pbo": None,
        "stable": False,
    }
    assert (
        map_verdict(DECISION_INSUFFICIENT, n_supp=MIN_N - 1, **kw)
        == VERDICT_INSUFFICIENT
    )
    assert map_verdict(DECISION_INSUFFICIENT, n_supp=MIN_N + 1, **kw) == VERDICT_NO_EDGE


def test_sign_agrees_early_late() -> None:
    assert sign_agrees_early_late([1.0, 2.0, 3.0, 4.0]) is True
    assert sign_agrees_early_late([-1.0, -2.0, -3.0, -4.0]) is True
    assert sign_agrees_early_late([5.0, 5.0, -5.0, -5.0]) is False
    assert sign_agrees_early_late([1.0]) is False


def test_cell_sharpe_zero_dispersion_is_zero() -> None:
    assert cell_sharpe(np.array([0.3, 0.3, 0.3])) == 0.0
    assert cell_sharpe(np.array([1.0])) == 0.0


def test_evaluate_states_accepts_a_family_key() -> None:
    """The parameterised family_key is what lets H15 reuse this unchanged."""
    rng = np.random.default_rng(0)
    cells = [
        AuditCell(
            label="a_hi|long", supp_r=list(rng.normal(0.4, 0.2, 80)), kept_r=[0.0] * 80
        ),
        AuditCell(
            label="a_lo|long", supp_r=list(rng.normal(-0.4, 0.2, 80)), kept_r=[0.0] * 80
        ),
    ]
    out = evaluate_states(cells, _family_key)
    assert [label for label, _ in out] == ["a_hi|long", "a_lo|long"]
    assert all(
        v in {VERDICT_BUILD, VERDICT_AVOID, VERDICT_NO_EDGE, VERDICT_INSUFFICIENT}
        for _, v in out
    )
