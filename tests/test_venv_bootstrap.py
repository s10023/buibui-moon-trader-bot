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
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from tools.venv_bootstrap import SENTINEL, reexec_into_venv


def _fake_venv(root: Path) -> Path:
    python = root / ".venv" / "bin" / "python"
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = _fake_venv(tmp_path)
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


def test_no_swap_when_already_inside_the_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_venv(tmp_path)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv"))
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    assert calls == []


def test_no_swap_when_the_venv_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh clone, a CI runner and `make preflight`'s clone all look like this."""
    monkeypatch.setattr(sys, "prefix", "/usr")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    assert calls == []


def test_no_swap_once_the_sentinel_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_venv(tmp_path)
    monkeypatch.setattr(sys, "prefix", "/usr")
    monkeypatch.setenv(SENTINEL, "1")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    assert calls == []


def test_symlinked_venv_python_still_swaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect the `sys.prefix` test exists for.

    `.venv/bin/python` is a symlink to the system interpreter, so a check comparing
    RESOLVED executables sees one path and never fires. Point the fake venv's python at
    the real `sys.executable` and the swap must still happen.
    """
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


def test_a_path_resolved_binary_is_reachable_after_the_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ST127: the swap must carry PATH, not just the interpreter.

    `tools/video_fetch.py` builds every command as `("yt-dlp", ...)` — resolved by the
    SHELL through PATH — so a re-exec that moves only `sys.executable` leaves the daily
    check's media canary reporting `FileNotFoundError` beside a venv that has the
    binary. `which` is the assertion because it is the same question the shell asks.
    """
    _fake_venv(tmp_path)
    installed = _executable(tmp_path / ".venv" / "bin" / "yt-dlp")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(sys, "prefix", "/usr")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    _, _, env = calls[0]
    assert shutil.which("yt-dlp", path=env["PATH"]) == str(installed)


def test_the_venv_wins_over_a_system_copy_of_the_same_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PREPENDED, not appended — the pin is the point.

    `yt-dlp` is held at a dated nightly because stable 403s on every media URL, so a
    stale system copy that shadowed the venv's would have the canary probe a version
    the pipeline never runs. Appending resolves the FileNotFoundError and still gets
    the wrong answer, which no reachability-only assertion can see.
    """
    _fake_venv(tmp_path)
    installed = _executable(tmp_path / ".venv" / "bin" / "yt-dlp")
    decoy_dir = tmp_path / "system-bin"
    _executable(decoy_dir / "yt-dlp")
    monkeypatch.setenv("PATH", str(decoy_dir))
    monkeypatch.setattr(sys, "prefix", "/usr")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    _, _, env = calls[0]
    assert shutil.which("yt-dlp", path=env["PATH"]) == str(installed)


def test_the_inherited_path_survives_the_prepend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A venv holds console scripts, not `git`, `ffmpeg` or `node` — replacing PATH
    rather than extending it would trade one FileNotFoundError for several."""
    _fake_venv(tmp_path)
    monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}/bin")
    monkeypatch.setattr(sys, "prefix", "/usr")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    _, _, env = calls[0]
    assert env["PATH"].split(os.pathsep) == [
        str(tmp_path / ".venv" / "bin"),
        "/usr/bin",
        "/bin",
    ]


def test_an_empty_path_yields_the_venv_bin_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty entry in PATH means the CWD to most shells, so a bare join would make
    the re-exec'd process resolve binaries out of whatever directory it started in."""
    _fake_venv(tmp_path)
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.setattr(sys, "prefix", "/usr")
    calls = _record_execve(monkeypatch)

    reexec_into_venv(tmp_path)

    _, _, env = calls[0]
    assert env["PATH"] == str(tmp_path / ".venv" / "bin")
