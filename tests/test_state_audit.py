"""Shared state-audit machinery: the extraction must be behaviour-preserving."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from analytics.audit_guard import (
    DECISION_CONCENTRATE,
    DECISION_DISABLE,
    DECISION_ENABLE,
    DECISION_INSUFFICIENT,
    AuditCell,
)
from analytics.state_audit import (
    DSR_FLOOR,
    MIN_N,
    VERDICT_AVOID,
    VERDICT_BUILD,
    VERDICT_INSUFFICIENT,
    VERDICT_NO_EDGE,
    cell_sharpe,
    evaluate_states,
    family_dsr,
    map_verdict,
    mintrl_n_ok,
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


# --------------------------------------------------------------------------- #
# The DIRECTIONAL-metric guards. Both legs of CLAUDE.md's directional rule are #
# covered here because both implementations now live here — H8, H14 and H15    #
# share them. `test_family_dsr_is_direction_agnostic` moved from               #
# tests/test_indicator_condition.py with the code it guards; it is the         #
# regression test for the defect PR #546 fixed, and it must not be deleted     #
# just because its module changed.                                             #
# --------------------------------------------------------------------------- #


def test_family_dsr_is_direction_agnostic() -> None:
    """A reliably-NEGATIVE cell must earn the same family DSR as its
    mirror-image positive cell.

    ``deflated_sharpe_ratio`` asks for confidence that the true Sharpe beats a
    POSITIVE expected-max-of-N benchmark, so a raw negative Sharpe deflates to
    ~0 no matter how reliable the negative effect is. Since AVOID requires
    ``dsr >= dsr_floor``, feeding it the signed Sharpe made the AVOID verdict
    structurally near-unreachable: measured 0.0000 for the negative cell
    against 0.9980 for its mirror. Folding every Sharpe to its magnitude asks
    the direction-agnostic question that matters here -- is this cell's
    extremity, whichever way it points, still credible after N trials.
    """
    neg = np.full(80, -0.5)
    neg[::2] = -1.2
    pos = -neg
    family = [neg, pos]
    dsr_neg = family_dsr(neg, family)
    dsr_pos = family_dsr(pos, family)
    assert dsr_neg == pytest.approx(dsr_pos)
    assert dsr_neg >= DSR_FLOOR


def test_mintrl_n_ok_is_direction_agnostic() -> None:
    """The MinTRL leg must be reachable for a NEGATIVE cell.

    ``min_track_record_length`` answers "how long until this POSITIVE Sharpe is
    credible", so a signed negative Sharpe returns ``inf`` — ``n >= inf`` is
    false for every n, which silently makes the AVOID branch unreachable and
    reports "no negative effect found" whatever the data says. Same defect
    family as the DSR one above, second metric. A mirror-image pair must get
    an identical, FINITE MinTRL and an identical pass/fail.
    """
    neg = np.full(80, -0.5)
    neg[::2] = -1.2
    pos = -neg

    mintrl_neg, ok_neg = mintrl_n_ok(neg, neg.shape[0])
    mintrl_pos, ok_pos = mintrl_n_ok(pos, pos.shape[0])

    assert math.isfinite(mintrl_neg)
    assert mintrl_neg == pytest.approx(mintrl_pos)
    assert ok_neg is ok_pos is True

    # and it still discriminates: the same strong effect on a short slice fails
    assert mintrl_n_ok(neg, 2)[1] is False


def test_mintrl_n_ok_uses_the_n_it_is_given() -> None:
    """``n_obs`` is a parameter, not ``arr.shape[0]`` — callers pass
    ``audit_guard``'s ``CellVerdict.n_supp``. The two are equal by
    construction, and this pins that the helper honours the argument rather
    than quietly re-deriving it."""
    arr = np.full(60, 0.4)
    arr[::2] = 0.9
    mintrl, _ = mintrl_n_ok(arr, arr.shape[0])
    assert mintrl_n_ok(arr, int(math.ceil(mintrl)))[1] is True
    assert mintrl_n_ok(arr, int(math.floor(mintrl)) - 1)[1] is False


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
