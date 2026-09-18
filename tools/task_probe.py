"""When a Windows scheduled task last ran, and whether it ended cleanly.

The Windows half of `tools/systemd_probe.py`. Same question, same return contract, so
a caller asks `scheduler_last_completion()` once and does not care which host it is on.

WHY THIS IS SIMPLER THAN ITS SIBLING, AND WHY THAT IS NOT AN OVERSIGHT
----------------------------------------------------------------------
`systemd_probe` reads the JOURNAL rather than `systemctl show` because `show` returns
EMPTY for a unit systemd has garbage-collected out of memory — an inactive,
timer-triggered, `static` unit with no references is exactly that case, and reading it
reported "the sync has NEVER run" every day against a healthy off-machine copy.

Task Scheduler has no equivalent hole: `LastRunTime` / `LastTaskResult` live in the
task store on disk and survive service restarts and reboots indefinitely. So the
structured query IS the reliable source here, and there is no log to fall back to. That
matters because the Task Scheduler *event* log (`Microsoft-Windows-TaskScheduler/
Operational`), which is the real analogue of the journal, ships DISABLED on Windows —
a probe built on it would read as "never ran" on a default install, reproducing the
exact defect the sibling module was written to fix.

⚠ **`LastRunTime` is when the run STARTED; the journal's "Finished" is when it ENDED.**
The two probes therefore answer subtly different questions. It does not matter for the
question either is actually asked — *is this job still firing?* — because the gap is
one job duration, and every job here is bounded by `ExecutionTimeLimit` well under its
own period. It WOULD matter for a job whose runtime approached its interval, so read a
staleness threshold as needing that much headroom rather than being exact.

The reader is injected, exactly as `journal_tail` is, so the parser is tested against
real PowerShell output with no Task Scheduler on the box.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import Protocol

from tools import host_platform, systemd_probe

_QUERY_TIMEOUT_S = 15

# Task Scheduler reports its own status in `LastTaskResult` using the SCHED_S_* facility
# rather than the action's exit code, so these values are NOT exit codes and must never
# be compared against `success_codes`.
#
# ⚠ Both are mapped to "no completion visible" ON PURPOSE, mirroring the sibling: its
# docstring states that a journal showing "only a start with no matching end" returns
# `(None, False)`. A task that has never run, and a task still running its first or
# current attempt, are both exactly that — a start with no end. Reporting the START
# stamp with `ok=True` for a running task would make a HUNG job read as freshly healthy,
# since `LastRunTime` advances the moment it starts.
SCHED_S_TASK_RUNNING = 267009  # 0x00041301
SCHED_S_TASK_HAS_NOT_RUN = 267011  # 0x00041303
_NO_COMPLETION = frozenset({SCHED_S_TASK_RUNNING, SCHED_S_TASK_HAS_NOT_RUN})

# `-f '{0:o} {1}'` is .NET's round-trip format: `2026-09-18T01:26:44.0000000Z`, which
# `datetime.fromisoformat` parses directly. Pinned against real output rather than
# assumed.
#
# ⚠ Do NOT print the raw property. PowerShell renders a DateTime in the host's locale
# (`09/18/2026 20:00:00` here), so a parser reading that is one machine's regional
# settings away from silently failing. `ToUniversalTime()` then `{0:o}` is
# locale-independent and carries its own offset.
_PS_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "$i=Get-ScheduledTaskInfo -TaskPath '{path}' -TaskName '{name}';"
    "if($i.LastRunTime){{'{{0:o}} {{1}}' -f $i.LastRunTime.ToUniversalTime(),"
    "$i.LastTaskResult}}"
)


class TaskInfoReader(Protocol):
    """Returns one `<iso-stamp> <last-result>` line for a task, or None."""

    def __call__(self, path: str, name: str) -> str | None: ...


def powershell_task_info(path: str, name: str) -> str | None:
    """`Get-ScheduledTaskInfo` for one task, as `<iso-stamp> <last-result>`.

    Returns None on every failure mode — no PowerShell, no such task, a timeout — so
    the caller's "no completion visible" branch covers an unreadable scheduler the same
    way the sibling's covers an unreadable journal.
    """
    script = _PS_SCRIPT.format(path=path, name=name)
    try:
        r = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_QUERY_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def task_last_completion(
    path: str,
    name: str,
    *,
    reader: TaskInfoReader | None = None,
    success_codes: frozenset[int] = frozenset({0}),
) -> tuple[datetime | None, bool]:
    """`(when the task last started, whether its last result was a success)`.

    `(None, False)` means no completion is visible at all — no PowerShell, no such
    task, a task that has never run, or one running right now. Deliberately
    indistinguishable from "never ran" at this layer, exactly as in `systemd_probe`:
    the caller decides whether that is a red or a not-yet.

    ⚠ `success_codes` exists because **Task Scheduler has no `SuccessExitStatus=`.**
    `buibui-daily-check.service` declares `SuccessExitStatus=2` so that a routine
    tier-2 red is not recorded as a failed unit, and `run-job.sh` preserves the wrapped
    command's exit code on both platforms — so without this argument the Windows host
    would report a FAILED daily check every day tier 2 is amber. A daily false FAILED
    trains its reader to ignore the channel just as effectively as silence does, which
    is the failure `TELEGRAM_ALWAYS` already exists to prevent.
    """
    # Resolved at CALL time, never as an early-bound default. A default of
    # `reader=powershell_task_info` captures the function OBJECT at import, so
    # monkeypatching the module attribute -- which is how a caller's test fakes the
    # scheduler -- silently has no effect and the test runs the real PowerShell.
    out = (reader or powershell_task_info)(path, name)
    if not out:
        return None, False
    stamp_text, _, result_text = out.strip().partition(" ")
    try:
        stamp = datetime.fromisoformat(stamp_text).astimezone(UTC)
        result = int(result_text.strip())
    except ValueError:
        return None, False
    if result in _NO_COMPLETION:
        return None, False
    return stamp, result in success_codes


def scheduler_last_completion(
    unit: str,
    *,
    task_path: str = "\\buibui\\",
    success_codes: frozenset[int] = frozenset({0}),
) -> tuple[datetime | None, bool]:
    """The one call a caller should make: ask whichever scheduler this host runs.

    `unit` is the systemd unit name (`buibui-signal-watch.service`). The Windows task
    name is derived from it rather than passed separately, so a caller cannot name the
    two halves inconsistently — `deploy/windows/install-tasks.ps1` registers tasks
    under exactly this transform, and `tests/test_task_probe.py` pins that both ends
    agree.
    """
    if not host_platform.is_windows():
        return systemd_probe.unit_last_completion(unit)
    return task_last_completion(
        task_path,
        task_name_for_unit(unit),
        success_codes=success_codes,
    )


def task_name_for_unit(unit: str) -> str:
    """`buibui-signal-watch.service` -> `buibui-signal-watch`.

    A Task Scheduler name has no type suffix, and `.service` in one would read as a
    file extension. The transform is a function rather than a convention so the
    installer and the probe cannot drift.
    """
    return unit.removesuffix(".service").removesuffix(".timer")
