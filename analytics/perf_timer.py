"""Simple wall-clock timing utility for before/after benchmarking.

Usage:
    from analytics.perf_timer import timed

    with timed("param sweep grid"):
        rows = run_param_sweep(...)

    # Output: [perf] param sweep grid: 3.41s
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager


@contextmanager
def timed(label: str) -> Generator[None]:
    t0 = time.perf_counter()
    yield
    elapsed = time.perf_counter() - t0
    _fmt = f"{elapsed:.2f}s" if elapsed < 60 else f"{elapsed / 60:.1f}m"
    print(f"  [perf] {label}: {_fmt}")
