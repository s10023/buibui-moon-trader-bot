"""Both entry surfaces must force UTF-8 stdout, or Windows kills the run.

Windows defaults `sys.stdout` to the ANSI codepage (cp1252 on this host), so a
tool printing any non-ASCII character raises `UnicodeEncodeError` -- and it does
so AFTER the work is done, at the final `print`. Measured 2026-09-18:
`make buibui-xsmom-daily` completed the universe sync and then died formatting
`⛔` at `tools/xsmom_execute.py:429`, reporting failure on a run that had already
succeeded.

⚠ Coverage is the UNION of the callers, which is the lesson
`test_ohlcv_freshness.py` already had to learn the hard way: pinning one surface
reproduces the bug one layer up. `deploy/windows/job.sh` exports this for the
SCHEDULED jobs and always did, so the crash only ever showed on a HAND run --
exactly the surface a session and the operator use, and exactly the one no test
covered. Both are pinned here so neither can quietly lose it.

A no-op on Linux, where UTF-8 is already the default.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Set by BOTH surfaces below. Not imported from anywhere -- the point of the test
# is to pin the literal each surface actually ships.
UTF8_VAR = "PYTHONUTF8"

# The site that actually crashed, kept as the worked example so nobody deletes
# the export believing it is unused.
KNOWN_CRASH_SITE = Path("tools/xsmom_execute.py")


def _exports_utf8(text: str) -> bool:
    """True when `text` exports PYTHONUTF8=1, in make or shell spelling.

    Tolerant of spacing because the two files write it differently: make wants
    `export PYTHONUTF8 = 1`, shell wants a bare assignment plus `export`.
    """
    return re.search(rf"{UTF8_VAR}\s*=\s*1", text) is not None


def test_makefile_exports_utf8_for_every_target() -> None:
    """The HAND-RUN surface. This is the half that was missing."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert _exports_utf8(makefile), (
        "Makefile no longer forces UTF-8 stdout; every target whose tool prints "
        "a non-ASCII character will crash on Windows AFTER doing its work"
    )
    assert re.search(rf"^export\s+{UTF8_VAR}", makefile, re.MULTILINE), (
        f"{UTF8_VAR} must be `export`ed, or it never reaches a recipe's environment"
    )


def test_scheduled_job_wrapper_exports_utf8() -> None:
    """The SCHEDULED surface, which already had it. Pinned so it stays."""
    job_sh = (REPO_ROOT / "deploy" / "windows" / "job.sh").read_text(encoding="utf-8")

    assert _exports_utf8(job_sh), (
        "deploy/windows/job.sh no longer forces UTF-8; the scheduled jobs regain "
        "the cp1252 crash the Makefile export cannot reach"
    )
    assert re.search(rf"^export\s+{UTF8_VAR}", job_sh, re.MULTILINE), (
        f"{UTF8_VAR} must be `export`ed to reach the wrapped command"
    )


def test_the_export_is_not_vacuous() -> None:
    """Teeth: prove something really does print non-ASCII on the default path.

    Without this, both assertions above pass just as happily over a codebase that
    prints pure ASCII -- a guard nothing bites on, which is the shape this repo
    keeps having to re-learn. Anchored on the file that actually crashed rather
    than on a corpus-wide count, so it cannot drift green.
    """
    source = (REPO_ROOT / KNOWN_CRASH_SITE).read_text(encoding="utf-8")

    assert not source.isascii(), (
        f"{KNOWN_CRASH_SITE} is pure ASCII now, so it no longer demonstrates why "
        f"{UTF8_VAR} is needed -- re-anchor this test on a printer that does, "
        "rather than deleting it"
    )
