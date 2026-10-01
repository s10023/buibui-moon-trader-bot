"""Worker-pool sizing for every sweep, scan and backtest pool, with one override.

Before #868 each pool site restated its own formula: three pinned the pool at
``cpu_count - 1`` (15 on the Windows laptop) with no way to lower it, while the
two ``backtest_runner`` pools capped at ``min(4, cpu_count - 1)``. ``pool_size``
reproduces both formulas exactly by default, and ``BUIBUI_MAX_WORKERS`` lets an
operator change the ceiling at every site at once — for example while a second
heavy process shares the machine.
"""

from __future__ import annotations

import os

ENV_VAR = "BUIBUI_MAX_WORKERS"


def env_max_workers() -> int | None:
    """The ``BUIBUI_MAX_WORKERS`` override, or None when unset or blank.

    Raises ``ValueError`` on anything but a positive integer: a typo must not
    fall back silently to the default pool, which is the very load the override
    exists to avoid.
    """
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{ENV_VAR} must be a positive integer, got {raw!r}") from None
    if value < 1:
        raise ValueError(f"{ENV_VAR} must be a positive integer, got {raw!r}")
    return value


def pool_size(n_tasks: int | None = None, *, cap: int | None = None) -> int:
    """Workers for a pool over ``n_tasks`` independent jobs (never below 1).

    The ceiling is ``cpu_count - 1``, lowered to ``cap`` when one is given.
    ``BUIBUI_MAX_WORKERS`` REPLACES that ceiling, ``cap`` included, because it
    is an explicit operator choice. ``n_tasks`` bounds the result when given,
    since a pool larger than its job list only spawns idle workers.
    """
    override = env_max_workers()
    if override is not None:
        ceiling = override
    else:
        ceiling = (os.cpu_count() or 2) - 1
        if cap is not None:
            ceiling = min(ceiling, cap)
    if n_tasks is not None:
        ceiling = min(ceiling, n_tasks)
    return max(1, ceiling)
