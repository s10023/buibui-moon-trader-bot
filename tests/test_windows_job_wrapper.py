"""Teeth for `deploy/windows/job.sh` — the Task Scheduler entry point.

Two behaviours are load-bearing and neither is obvious from reading the file.

**The exit code must survive the tee.** `run-job.sh` preserves the wrapped command's
code deliberately, `buibui-daily-check.service` declares `SuccessExitStatus=2` on top of
that, and `tools/task_probe.py` reads the code back out of Task Scheduler to decide
whether a run failed. A `cmd | tee` pipeline reports TEE's status by default, and tee
essentially always succeeds — so getting this wrong reports every failed job as a
success, which is the one direction a health check must never be wrong in.

**The log must stay bounded.** `deploy/README.md` keeps no logfile on Linux precisely so
nothing grows unmanaged on a laptop; Windows has no journal to delegate that to, so the
wrapper owns the cap.

`RUN_JOB` exists as a seam for these cases — the real `run-job.sh` wants `curl`, a
healthchecks URL and a Telegram token, none of which belong in a unit test.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_JOB = _REPO / "deploy/windows/job.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="no bash on PATH to run the wrapper"
)


def _stub(tmp_path: Path, body: str) -> Path:
    stub = tmp_path / "fake-run-job.sh"
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    stub.chmod(0o755)
    return stub


def _run(
    tmp_path: Path, stub: Path, *, label: str = "signal-watch", max_lines: str = "2000"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", _JOB.as_posix(), label, "HEALTHCHECKS_URL_SIGNAL", "--", "true"],
        capture_output=True,
        text=True,
        cwd=_REPO,
        env={
            "PATH": "/usr/bin:/bin",
            "RUN_JOB": stub.as_posix(),
            "BUIBUI_LOG_DIR": (tmp_path / "logs").as_posix(),
            "BUIBUI_LOG_MAX_LINES": max_lines,
        },
    )


@pytest.mark.parametrize("code", [0, 1, 2, 42], ids=["ok", "fail", "soft-tier2", "odd"])
def test_the_wrapped_exit_code_survives_the_tee(tmp_path: Path, code: int) -> None:
    """`2` is in here explicitly: it is the daily check's soft exit, and reporting it as
    0 would hide a tier-2 red while reporting it as a crash would page a false FAILED
    every amber day.

    ⚠ This pins the OUTCOME, not the mechanism. `job.sh` sets `pipefail`, so swapping
    its `${PIPESTATUS[0]}` for a bare `$?` still passes every case here — measured, and
    stated so a later reader does not credit this with teeth it does not have. What
    `PIPESTATUS` buys is independence from `pipefail` remaining set, and the ability to
    tell a failed JOB from a failed TEE; neither is reachable from a portable test, so
    neither is asserted.
    """
    stub = _stub(tmp_path, f"exit {code}")

    assert _run(tmp_path, stub).returncode == code


def test_the_jobs_output_reaches_the_log(tmp_path: Path) -> None:
    """On Linux `run-job.sh`'s closing `tail -n 60` lands in the journal. A Task
    Scheduler action's stdout goes nowhere, so without this the operator's actual
    debugging surface is discarded."""
    stub = _stub(tmp_path, "echo hello-from-the-job; exit 0")

    result = _run(tmp_path, stub)

    log = tmp_path / "logs/signal-watch.log"
    assert log.exists(), "no logfile written"
    assert "hello-from-the-job" in log.read_text(encoding="utf-8")
    assert "hello-from-the-job" in result.stdout, "output must still reach stdout too"


def test_each_job_gets_its_own_log(tmp_path: Path) -> None:
    """Five jobs sharing one file would interleave a 15-minute scan into the daily
    check's report, and the trim would then drop whichever job logged least."""
    stub = _stub(tmp_path, "echo scan; exit 0")
    _run(tmp_path, stub, label="signal-watch")
    _run(tmp_path, stub, label="daily-check")

    assert (tmp_path / "logs/signal-watch.log").exists()
    assert (tmp_path / "logs/daily-check.log").exists()


def test_the_log_is_trimmed_to_the_cap(tmp_path: Path) -> None:
    """Unbounded, this file grows forever on a 15-minute job — 96 runs a day."""
    stub = _stub(tmp_path, "seq 1 50; exit 0")

    for _ in range(3):
        _run(tmp_path, stub, max_lines="20")

    lines = (
        (tmp_path / "logs/signal-watch.log").read_text(encoding="utf-8").splitlines()
    )
    assert len(lines) == 20, f"log not trimmed to the cap: {len(lines)} lines"
    assert lines[-1] == "50", "the trim must keep the END, where the failure is"


def test_the_log_survives_a_failing_job(tmp_path: Path) -> None:
    """The run worth reading is the one that broke, so the trim must not be skipped on
    a non-zero exit — and the output must be there to read."""
    stub = _stub(tmp_path, "echo boom >&2; exit 1")

    result = _run(tmp_path, stub)

    assert result.returncode == 1
    assert "boom" in (tmp_path / "logs/signal-watch.log").read_text(encoding="utf-8")
