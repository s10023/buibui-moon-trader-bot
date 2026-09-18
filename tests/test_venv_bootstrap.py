"""Teeth for `tools.venv_bootstrap.reexec_into_venv`.

The interesting case is the POSITIVE one — delete the `os.execve` line and
`test_swaps_interpreter_when_outside_the_venv` is the only test that fails. The three
no-op tests exist because this function is called unconditionally at import time by a
script that must keep working on a clone with no venv at all.

⚠ The PATH cases assert REACHABILITY through `shutil.which`, never the shape of the
string. Asserting `sys.prefix` moved — or that `PATH` merely contains the venv — is
what let ST127 ship: the interpreter swapped correctly and a subprocess resolved BY
NAME still died `FileNotFoundError`, because that resolution is the shell's, not
Python's. A test that cannot tell those two apart is the blind spot, restated.

⚠ **Every platform-sensitive case forces the platform rather than inheriting the
host's**, so the suite gives the same answer on the Linux box and the Windows one.
Before that this file was silently POSIX-shaped: it built `.venv/bin/python` and read
as a full pass on ubuntu CI while 6 of its 9 cases failed on a Windows host. A suite
that can only be right on the platform that happens to run it is the same blind spot
one level up.

⚠ **It forces it by patching `host_platform.is_windows`, NEVER `os.name`.**
`pathlib` dispatches on `os.name`, so patching it to the foreign value makes any
`Path(...)` — including pytest's own — raise `NotImplementedError: cannot instantiate
'PosixPath' on your system`. Measured: that took the whole run down as an
`INTERNALERROR` inside failure reporting, so nothing was reported at all.

⚠ **`_fake_venv` spells each layout LITERALLY rather than calling `_venv_bin_dir`.**
Building the fixture from the function under test makes the test mirror whatever the
production helper does, bug included — it would have passed just as cleanly against
the hardcoded `bin` this file now pins.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from tools import host_platform
from tools.venv_bootstrap import SENTINEL, reexec_into_venv

# The two layouts, spelled out. Stated as a constant so a change to either is a
# visible diff rather than a derived value nobody reviews.
_LAYOUT = {"posix": ("bin", "python"), "nt": ("Scripts", "python.exe")}

# ⚠ `shutil.which` keys on `sys.platform`, NOT on `os.name`, so the patch that steers
# the production code cannot steer it. That is why the two reachability cases below
# are pinned to the host's REAL platform instead of being parametrised: asked the
# other platform's question, `which` would answer the host's anyway and the assertion
# would be measuring nothing. The bin-vs-Scripts discrimination those cases give up is
# covered by `test_the_inherited_path_survives_the_prepend` and the mutation pair.
_NATIVE = "nt" if sys.platform == "win32" else "posix"


@pytest.fixture(params=["posix", "nt"])
def os_name(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run the case under both platform names, whichever host is executing.

    `monitor/price_lib.py` + `tests/test_price_monitor.py` established the idea here
    — assert the branch you are not running on — which is what buys Windows coverage
    out of an ubuntu-only CI matrix. The MECHANISM differs on purpose: see the module
    docstring on why `os.name` itself must not be the thing patched.
    """
    _force_platform(monkeypatch, str(request.param))
    return str(request.param)


def _force_platform(monkeypatch: pytest.MonkeyPatch, os_name: str) -> None:
    monkeypatch.setattr(host_platform, "is_windows", lambda: os_name == "nt")


def _fake_venv(root: Path, os_name: str) -> Path:
    bin_dir, exe = _LAYOUT[os_name]
    python = root / ".venv" / bin_dir / exe
    python.parent.mkdir(parents=True)
    python.touch()
    return python


@pytest.fixture(autouse=True)
def _clear_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SENTINEL, raising=False)


def _record_execve(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        "os.execve",
        lambda *args: calls.append(args),
    )
    return calls


def test_swaps_interpreter_when_outside_the_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    python = _fake_venv(tmp_path, os_name)
    monkeypatch.setattr(sys, "prefix", "/usr")
    monkeypatch.setattr(sys, "argv", ["docs/plans/daily_check.py", "--telegram"])
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    assert len(calls) == 1, "the interpreter swap did not fire"
    path, argv, env = calls[0]
    assert path == str(python)
    # argv[0] is the interpreter; the script's own argv rides behind it unchanged, so
    # flags survive the swap -- a re-exec that dropped --telegram would push nothing.
    assert argv == [str(python), "docs/plans/daily_check.py", "--telegram"]
    assert env[SENTINEL] == "1", "without the sentinel a broken venv re-execs forever"


@pytest.mark.parametrize(
    ("running", "built"),
    [("nt", "posix"), ("posix", "nt")],
    ids=["nt-sees-bin", "posix-sees-Scripts"],
)
def test_the_other_platforms_layout_is_not_a_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, running: str, built: str
) -> None:
    """The mutation case, and the defect this pair of helpers was added for.

    Hardcoding `bin` did not CRASH on Windows — it made the venv look ABSENT, which
    is this module's documented safe path, so `reexec_into_venv` returned quietly and
    `daily_check.py` went on to render the partial `?` report the whole file exists to
    prevent. Nothing in the happy-path case above can see that: it passes whenever the
    layout it builds is the layout the code looks for, which was true on ubuntu.

    Asserting the swap does NOT fire across the layouts is what pins the
    discrimination rather than the coincidence.
    """
    _fake_venv(tmp_path, built)
    _force_platform(monkeypatch, running)
    monkeypatch.setattr(sys, "prefix", "/usr")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    assert calls == []


def test_no_swap_when_already_inside_the_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    _fake_venv(tmp_path, os_name)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv"))
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    assert calls == []


def test_no_swap_when_the_venv_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    """A fresh clone, a CI runner and `make preflight`'s clone all look like this."""
    monkeypatch.setattr(sys, "prefix", "/usr")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    assert calls == []


def test_no_swap_once_the_sentinel_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    _fake_venv(tmp_path, os_name)
    monkeypatch.setattr(sys, "prefix", "/usr")
    monkeypatch.setenv(SENTINEL, "1")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    assert calls == []


@pytest.mark.skipif(
    sys.platform == "win32", reason="needs unprivileged symlinks; POSIX-only defect"
)
def test_symlinked_venv_python_still_swaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect the `sys.prefix` test exists for.

    `.venv/bin/python` is a symlink to the system interpreter, so a check comparing
    RESOLVED executables sees one path and never fires. Point the fake venv's python at
    the real `sys.executable` and the swap must still happen.

    POSIX-only by nature, not by convenience: this is a statement about how
    `python -m venv` builds a venv on Linux. Windows COPIES the interpreter instead of
    symlinking it, so there is no equivalent trap to pin there, and creating a symlink
    at all needs a privilege neither CI nor the operator's shell reliably holds.
    """
    _force_platform(monkeypatch, "posix")
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    monkeypatch.setattr(sys, "prefix", "/usr")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    assert len(calls) == 1


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def _resolves_to(found: str | None, installed: Path) -> bool:
    """Compare through `os.path.normcase`, the standard case-insensitive-FS idiom.

    ⚠ Not cosmetic. `shutil.which` synthesises the candidate name by concatenating the
    bare command with each `PATHEXT` entry VERBATIM, so it returns `yt-dlp.EXE` for a
    file on disk named `yt-dlp.exe` — a mismatch that is about the env var's casing and
    says nothing about reachability, which is the property under test. `normcase`
    lowercases on Windows and is the identity on POSIX, so one assertion stays correct
    on both hosts.
    """
    return found is not None and os.path.normcase(found) == os.path.normcase(
        str(installed)
    )


def _install_ytdlp(root: Path) -> Path:
    """Put a `yt-dlp` in the native venv layout, named as this platform names it."""
    bin_dir, _ = _LAYOUT[_NATIVE]
    name = "yt-dlp.exe" if _NATIVE == "nt" else "yt-dlp"
    return _executable(root / ".venv" / bin_dir / name)


def test_a_path_resolved_binary_is_reachable_after_the_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ST127: the swap must carry PATH, not just the interpreter.

    `tools/video_fetch.py` builds every command as `("yt-dlp", ...)` — resolved by the
    SHELL through PATH — so a re-exec that moves only `sys.executable` leaves the daily
    check's media canary reporting `FileNotFoundError` beside a venv that has the
    binary. `which` is the assertion because it is the same question the shell asks.
    """
    _force_platform(monkeypatch, _NATIVE)
    _fake_venv(tmp_path, _NATIVE)
    installed = _install_ytdlp(tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setattr(sys, "prefix", "/usr")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    _, _, env = calls[0]
    assert _resolves_to(shutil.which("yt-dlp", path=env["PATH"]), installed)


def test_the_venv_wins_over_a_system_copy_of_the_same_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PREPENDED, not appended — the pin is the point.

    `yt-dlp` is held at a dated nightly because stable 403s on every media URL, so a
    stale system copy that shadowed the venv's would have the canary probe a version
    the pipeline never runs. Appending resolves the FileNotFoundError and still gets
    the wrong answer, which no reachability-only assertion can see.
    """
    _force_platform(monkeypatch, _NATIVE)
    _fake_venv(tmp_path, _NATIVE)
    installed = _install_ytdlp(tmp_path)
    decoy_dir = tmp_path / "system-bin"
    _executable(decoy_dir / installed.name)
    monkeypatch.setenv("PATH", str(decoy_dir))
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setattr(sys, "prefix", "/usr")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    _, _, env = calls[0]
    assert _resolves_to(shutil.which("yt-dlp", path=env["PATH"]), installed)


def test_the_inherited_path_survives_the_prepend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    """A venv holds console scripts, not `git`, `ffmpeg` or `node` — replacing PATH
    rather than extending it would trade one FileNotFoundError for several.

    Parametrised where the two `which` cases above could not be: this asserts the
    string the code BUILDS, which is steered by the same `os.name` the code reads.
    """
    _fake_venv(tmp_path, os_name)
    bin_dir, _ = _LAYOUT[os_name]
    monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}/bin")
    monkeypatch.setattr(sys, "prefix", "/usr")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    _, _, env = calls[0]
    assert env["PATH"].split(os.pathsep) == [
        str(tmp_path / ".venv" / bin_dir),
        "/usr/bin",
        "/bin",
    ]


def test_an_empty_path_yields_the_venv_bin_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    """An empty entry in PATH means the CWD to most shells, so a bare join would make
    the re-exec'd process resolve binaries out of whatever directory it started in."""
    _fake_venv(tmp_path, os_name)
    bin_dir, _ = _LAYOUT[os_name]
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.setattr(sys, "prefix", "/usr")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    _, _, env = calls[0]
    assert env["PATH"] == str(tmp_path / ".venv" / bin_dir)
