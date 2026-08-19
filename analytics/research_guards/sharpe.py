"""The Sharpe primitive every sleeve feeds into the gate — one definition.

``per_period_sharpe`` was copied byte-identically into all five sleeve report
modules (xsmom, forecast, combine, carry, cvd) with no shared import and no
cross-reference comment. That is not a cosmetic duplication: its output is the
``sr_d`` that goes straight into :func:`deflated_sharpe_ratio` and the
bootstrap statistic behind ``boot_lo``, i.e. two of the three legs
:func:`analytics.research_guards.passes_gate` compares ACROSS sleeves. Five
copies means a numeric change to one — loosening the ``1e-12`` degenerate-sd
guard, or switching ``ddof`` — silently desynchronises the very numbers the
gate exists to compare, and neither mypy nor the suite can see it.

**The two annualisation conventions are NOT interchangeable, and they already
collide by name.** ``ann_sharpe`` here takes an ``ann_factor`` that is ALREADY
``sqrt(periods_per_year)`` — the five report modules compute
``ann = math.sqrt(cfg.annualization_days)`` and pass that.
``analytics.xsmom.diagnostics`` has a private ``_ann_sharpe`` whose second
argument is raw ``ann_days`` and which square-roots internally, so the two
functions share a name and a shape while meaning different things. Importing
the wrong one changes every number it touches by a ``sqrt`` factor and nothing
would fail. Diagnostics keeps its wrapper for that reason; it now delegates the
per-period half here and applies its own ``sqrt`` visibly at the boundary.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt


def per_period_sharpe(r: npt.NDArray[np.float64]) -> float:
    """Mean / sd of ``r``, in the units ``r`` is sampled at. No annualisation.

    Returns ``0.0`` rather than raising or emitting NaN for the two degenerate
    inputs — fewer than two observations, and a standard deviation below
    ``1e-12``. That floor runs in the opposite direction to the one people
    expect: it rejects only a (near-)exactly flat series, so a set of returns
    that is merely tightly clustered still produces a finite, enormous Sharpe.
    There is no dispersion floor here, and adding one would be a behaviour
    change needing its own evidence.

    ⚠ **The ``bos/1d/long`` Sharpe -461 case in CLAUDE.md is NOT this
    function.** That belongs to ``analytics.recalibrate_lib._sharpe``, which
    guards on ``sd == 0.0`` exactly and returns ``None``; it feeds star ratings,
    not the sleeve gate. The two share a weakness and share no code — do not
    "fix" one by citing the other's numbers.
    """
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r) / sd)


def ann_sharpe(r: npt.NDArray[np.float64], ann_factor: float) -> float:
    """Annualised Sharpe. ``ann_factor`` is ALREADY ``sqrt(periods_per_year)``.

    Pass ``math.sqrt(cfg.annualization_days)``, never ``annualization_days``.
    """
    return per_period_sharpe(r) * ann_factor
