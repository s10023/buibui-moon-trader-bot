"""Teeth for `tools.venv_bootstrap.reexec_into_venv`.

The interesting case is the POSITIVE one — delete the `os.execve` line and
`test_swaps_interpreter_when_outside_the_venv` is the only test that fails. The three
no-op tests exist because this function is called unconditionally at import time by a
script that must keep working on a clone with no venv at all.
"""

from __future__ import annotations

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
