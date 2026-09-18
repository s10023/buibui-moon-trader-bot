"""Re-exec a hand-run script into the project venv instead of degrading in place.

The trap this closes: `python3 docs/plans/daily_check.py` RUNS. Every leg needing a
third-party dep raises `ModuleNotFoundError`, and because that script wraps each leg so
one breakage cannot kill the report, the run prints a plausible partial report with nine
`?` legs. Its outcome handling is already correct — a `?` is not a `+`, and a tier-1 `?`
still exits 1 (see `daily_check.broke`) — so nothing is mis-reported. What correctness
does not buy back is the ROUND TRIP: the operator learns which interpreter they typed
only after paying for a full run.

The standing rule was prose, in three places at once (that script's docstring, the
handoff, `AGENTS.md`), and its own inclusion-rule comment records the score for prose
rules here: **0-for-15, against 15-for-15 for the marker check**. So this is a
mechanism rather than a fourth sentence.

⚠ **Scope is narrow on purpose, and the discriminator is whether the failure is LOUD.**
Every `tools/*.py` audit script imports its deps at module level and dies on an
immediate traceback naming the missing module — you learn what you typed in one line,
and there is no partial result to misread. Only a script that KEEPS GOING and renders
something that looks like an answer earns this. Do not blanket-apply it to the 36
loud ones; that trades a one-line failure for a hidden interpreter switch.

This lives in tracked `tools/` rather than inside `daily_check.py` for the ST43 reason:
that file is gitignored, so anything buried in it reaches no reclone, no CI and not the
wifey fork. Here it is covered by `tests/test_venv_bootstrap.py`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from tools import host_platform

SENTINEL = "BUIBUI_VENV_REEXEC"


def reexec_into_venv(root: Path, *, sentinel: str = SENTINEL) -> None:
    """Replace this process with the same argv run under ``root/.venv``.

    Returns normally — never raises, never exits — in every case where the swap cannot
    or should not happen: the sentinel is already set (a prior re-exec, so a broken venv
    cannot loop), the venv is absent (a fresh clone, a CI runner, `make preflight`'s
    clone before `poetry install`), or we are already inside it. The caller then behaves
    exactly as it did before this function existed, which is what keeps it safe to call
    unconditionally at import time.

    ⚠ The "already inside it" test is ``sys.prefix``, NOT ``sys.executable``.
    ``.venv/bin/python`` is a symlink to the system interpreter, so comparing resolved
    executables reports the venv and a bare `python3` as the SAME path and the swap
    never fires. ``sys.prefix`` is the venv root inside a venv and the base install
    outside one, which is the distinction actually being asked about.

    ⚠ The swap carries ``PATH`` as well as the interpreter — see `_venv_first_path`.
    """
    if os.environ.get(sentinel) == "1":
        return
    venv = root / ".venv"
    python = _venv_python(venv)
    if not python.exists():
        return
    if Path(sys.prefix).resolve() == venv.resolve():
        return
    # stderr, not stdout: `deploy/run-job.sh` captures stdout and pushes it to Telegram
    # under a line budget, so a note about the interpreter must not spend a report line.
    print(
        f"note: re-exec into {python} (was {sys.executable})",
        file=sys.stderr,
    )
    os.execve(
        str(python),
        [str(python), *sys.argv],
        {**os.environ, sentinel: "1", "PATH": _venv_first_path(venv)},
    )


def _venv_first_path(venv: Path) -> str:
    """``PATH`` with ``venv/bin`` PREPENDED, so the venv's console scripts resolve too.

    Swapping the interpreter is not enough. A subprocess resolved BY NAME is resolved by
    the SHELL, through ``PATH``, which ``os.execve`` inherits unchanged — so before this
    a hand-run `daily_check.py` re-exec'd correctly and then reported
    ``FileNotFoundError: 'yt-dlp'`` for its media canary while `.venv/bin/yt-dlp` sat
    right there. That is the ST119 bootstrap's own documented scope gap (it covers what
    PYTHON resolves, never what the SHELL does) arriving one level out, and it is the
    same shape `AGENTS.md` already records for repo-vs-third-party imports.

    PREPENDED rather than appended, because the pin is the whole point: `yt-dlp` is held
    at a dated NIGHTLY (stable 403s on every media URL), so a stale system copy earlier
    on ``PATH`` would shadow it and the canary would probe a version the pipeline never
    runs — a false verdict, which is worse than the missing one this replaces.
    """
    bin_dir = str(_venv_bin_dir(venv))
    current = os.environ.get("PATH", "")
    return f"{bin_dir}{os.pathsep}{current}" if current else bin_dir


def _venv_bin_dir(venv: Path) -> Path:
    """``Scripts`` on Windows, ``bin`` everywhere else.

    Not a Windows VARIANT -- a portability fix, correct on both platforms. The
    hardcoded ``bin`` made every leg of this module a silent no-op on Windows: the
    interpreter probe below could not find a venv that was right there, so
    `reexec_into_venv` returned as if the venv were ABSENT. That is the module's
    documented safe path, which is exactly why it would never have surfaced as an
    error -- the operator gets the partial `?` report this file exists to prevent,
    on the one platform where nothing says so.

    The platform is read at CALL time through `host_platform.is_windows`, so a test
    can exercise the branch it is not running on -- which is what buys Windows coverage
    out of an ubuntu-only CI matrix. That module's docstring says why the check is a
    function rather than an inline `os.name` read; do not inline it back.
    """
    return venv / ("Scripts" if host_platform.is_windows() else "bin")


def _venv_python(venv: Path) -> Path:
    """The venv interpreter, named as the platform names it.

    ⚠ The extension is load-bearing and is NOT merely cosmetic: `Path.exists()` on
    ``.venv/Scripts/python`` is False on Windows, so dropping ``.exe`` reproduces the
    absent-venv no-op this helper removes.
    """
    return _venv_bin_dir(venv) / (
        "python.exe" if host_platform.is_windows() else "python"
    )
