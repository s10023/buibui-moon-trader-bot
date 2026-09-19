"""The one place this repo asks which platform it is running on.

Two modules need the question (`tools/venv_bootstrap.py` for the venv layout,
`tools/task_probe.py` for which scheduler to interrogate) and the ANSWER is trivial.
What is not trivial is the warning below, and a warning restated per call site is the
shape `AGENTS.md` already records going wrong six ways — so the question lives here and
the warning is written once.

⚠ **Call `is_windows()`; never inline `os.name == "nt"` at a call site.** The reason is
testability, and it is sharper than a style preference: patching `os.name` to the
foreign value to exercise the other branch ALSO repoints `pathlib`, which dispatches on
it. Any `Path(...)` under that patch then raises ``NotImplementedError: cannot
instantiate 'PosixPath' on your system``. Measured 2026-09-18: that did not merely fail
the case under test, it took pytest's own failure REPORTING down with it
(`INTERNALERROR` out of `_repr_failure_py`), so the run reported nothing at all rather
than a red. A function is patchable without touching the interpreter's own path
machinery; `os.name` is not.

`monitor/price_lib.py` does inline the check and `tests/test_price_monitor.py` does
patch `os.name` — that pair survives only because nothing under its patch constructs a
Path. Read it as narrower than it looks, not as the house idiom.
"""

from __future__ import annotations

import os


def is_windows() -> bool:
    """True on a Windows host, read at CALL time so a test can force either branch."""
    return os.name == "nt"
