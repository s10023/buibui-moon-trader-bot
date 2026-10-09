"""Force UTF-8 onto stdout/stderr at a process entry point.

Windows hands a redirected or piped stream the ANSI codepage (cp1252 on this host), so
the first `print` of a `─`, `→` or a zh video title raises `UnicodeEncodeError` — and
it does so at the closing table, AFTER the work is done. Measured 2026-10-09: a
`buibui backtest --save` with stdout redirected saved all 57 runs, then exited 1 on
`format_sweep_table`'s `═` rule, so a chained caller read a completed sweep as a
failure.

`PYTHONUTF8=1` already covers `make` and the scheduled jobs
(`tests/test_utf8_output.py`); this covers the third surface, a bare `buibui …` or
`python tools/<name>.py`, which no environment variable reaches. Stdlib-only, so a
stdlib-only tool can take it without gaining a dependency.
"""

from __future__ import annotations

import io
import sys


def utf8_stdio() -> None:
    """Re-encode stdout and stderr as UTF-8, replacing anything unencodable.

    Call it first in an entry point, before anything prints. A stream that is not a
    `TextIOWrapper` (`None` under `pythonw`, or a test's replacement) is left alone.
    A no-op on Linux, where UTF-8 is already the default.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
