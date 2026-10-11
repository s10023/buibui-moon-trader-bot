"""Teeth for `tools/task_probe.py` — the daily check's liveness leg on a Windows host.

Mirrors `tests/test_systemd_probe.py`: the reader is injected, so the parser is
exercised against real text with no Task Scheduler on the box. The fixtures below are
PINNED FROM A LIVE `Get-ScheduledTaskInfo` on Windows 11 (2026-09-18) rather than
composed by hand — the sibling's own history is why, since a parser tested only against
text its author invented agrees with the author and not with the source.

The interesting cases are the two that must NOT read as healthy: a task that has never
run, and one running right now. Both report a perfectly good `LastRunTime`, so a probe
that only parsed the stamp would call a job that has never once fired "fresh".
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools import host_platform, systemd_probe, task_probe
from tools.task_probe import (
    DEFAULT_TASK_PATH,
    SCHED_S_TASK_HAS_NOT_RUN,
    SCHED_S_TASK_RUNNING,
    SCHED_S_TASK_TERMINATED,
    TASK_STATE_DISABLED,
    scheduler_last_completion,
    task_last_completion,
    task_last_result,
    task_name_for_unit,
    task_state,
)

# Verbatim from `powershell_task_info("\\", "Adobe Acrobat Update Task")` on a real box.
_LIVE_OK = "2026-09-18T01:26:44.0000000Z 0"

_INSTALLER = Path(__file__).resolve().parents[1] / "deploy/windows/install-tasks.ps1"


def _installer_rows() -> list[tuple[str, str]]:
    """Every `@{ Unit = '...'; Task = '...'; ... }` row, as `(unit, task)`."""
    pattern = re.compile(r"@\{ Unit = '([^']+)'; Task = '([^']+)'")
    return pattern.findall(_INSTALLER.read_text(encoding="utf-8"))


def _reader(text: str | None) -> task_probe.TaskInfoReader:
    def read(path: str, name: str) -> str | None:
        return text

    return read


def test_parses_real_powershell_output() -> None:
    stamp, ok = task_last_completion("\\buibui\\", "x", reader=_reader(_LIVE_OK))

    assert stamp == datetime(2026, 9, 18, 1, 26, 44, tzinfo=UTC)
    assert ok is True


def test_the_stamp_comes_back_in_utc() -> None:
    """The reader asks PowerShell for `ToUniversalTime()`, but an offset-carrying stamp
    must still normalise here — a caller comparing it to `datetime.now(UTC)` would
    otherwise be eight hours out on this operator's box and never notice in summer."""
    stamp, _ = task_last_completion(
        "\\buibui\\", "x", reader=_reader("2026-09-18T09:26:44.0000000+08:00 0")
    )

    assert stamp == datetime(2026, 9, 18, 1, 26, 44, tzinfo=UTC)


@pytest.mark.parametrize(
    "result",
    [SCHED_S_TASK_HAS_NOT_RUN, SCHED_S_TASK_RUNNING],
    ids=["has-not-run", "running-now"],
)
def test_a_scheduler_status_is_not_a_completion(result: int) -> None:
    """The case a stamp-only parser gets wrong, and the reason `_NO_COMPLETION` exists.

    `LastTaskResult` carries Task Scheduler's own SCHED_S_* status rather than the
    action's exit code in both states, and `LastRunTime` is populated and recent in
    both — so reporting `(stamp, ok)` here would make a job that has NEVER fired, and a
    job HUNG since it started, each read as freshly healthy. `(None, False)` mirrors
    the sibling, whose docstring gives "only a start with no matching end" the same
    answer.
    """
    stamp, ok = task_last_completion(
        "\\buibui\\", "x", reader=_reader(f"2026-09-18T01:26:44.0000000Z {result}")
    )

    assert (stamp, ok) == (None, False)


def test_a_nonzero_exit_code_is_a_failure() -> None:
    stamp, ok = task_last_completion(
        "\\buibui\\", "x", reader=_reader("2026-09-18T01:26:44.0000000Z 1")
    )

    assert stamp == datetime(2026, 9, 18, 1, 26, 44, tzinfo=UTC), (
        "a failure still has a time -- the caller reports WHEN it broke"
    )
    assert ok is False


def test_success_codes_carries_the_soft_exit() -> None:
    """Task Scheduler has no `SuccessExitStatus=`, which `buibui-daily-check.service`
    declares as 2. Without this argument the Windows host reports a FAILED daily check
    every day tier 2 is merely amber — a daily false FAILED, which trains its reader to
    ignore the channel exactly as silence does."""
    text = "2026-09-18T01:26:44.0000000Z 2"

    assert task_last_completion("\\buibui\\", "x", reader=_reader(text))[1] is False
    assert (
        task_last_completion(
            "\\buibui\\", "x", reader=_reader(text), success_codes=frozenset({0, 2})
        )[1]
        is True
    )


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "not-a-timestamp 0",
        "2026-09-18T01:26:44.0000000Z",
        "2026-09-18T01:26:44.0000000Z not-a-number",
        "09/18/2026 20:00:00 0",
    ],
    ids=[
        "reader-returned-none",
        "empty",
        "whitespace",
        "unparseable-stamp",
        "no-result-field",
        "unparseable-result",
        "locale-rendered-datetime",
    ],
)
def test_unreadable_output_is_no_completion(text: str | None) -> None:
    """Every failure mode collapses to the caller's not-yet branch rather than raising.

    ⚠ `locale-rendered-datetime` is the load-bearing one. PowerShell renders a DateTime
    in the HOST's locale when you print the property raw, so this is what the probe
    would receive if anyone "simplified" `_PS_SCRIPT` to drop the `{0:o}` format. It
    must fail closed rather than be parsed by luck on some machines' regional settings.
    """
    assert task_last_completion("\\buibui\\", "x", reader=_reader(text)) == (
        None,
        False,
    )


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("buibui-signal-watch.service", "buibui-signal-watch"),
        ("buibui-signal-watch.timer", "buibui-signal-watch"),
        ("buibui-daily-check.service", "buibui-daily-check"),
        ("buibui-backup-offsite.service", "buibui-backup-offsite"),
    ],
)
def test_task_name_for_unit(unit: str, expected: str) -> None:
    assert task_name_for_unit(unit) == expected


def test_the_installer_and_the_probe_agree_on_every_task_name() -> None:
    """The drift this transform exists to prevent.

    A probe asking about `buibui-signal-watch` while the installer registered
    `buibui-signal-watch.service` reports "never ran" forever, against a perfectly
    healthy task — the same permanently-red-line failure `systemd_probe` was written
    for, reached from the other end.

    The installer states both spellings per row instead of computing one from the
    other, precisely so this can compare them. Rows are READ off disk, so adding a
    sixth job cannot pass by default.
    """
    rows = _installer_rows()
    assert len(rows) == 5, f"expected 5 jobs, parsed {len(rows)}"
    for unit, task in rows:
        assert task_name_for_unit(unit) == task


def test_the_installer_and_the_probe_agree_on_the_task_path() -> None:
    """`scheduler_last_completion`'s default must be the path tasks land under.

    Two spellings of the task path is the same drift one level up: the probe
    would ask a path Task Scheduler has nothing under, and get the never-ran
    answer forever.
    """
    text = _INSTALLER.read_text(encoding="utf-8")
    declared = re.search(r"\$TaskPath = '([^']+)'", text)
    assert declared, "no $TaskPath default found in the installer"
    assert (
        declared.group(1)
        == inspect.signature(scheduler_last_completion).parameters["task_path"].default
    )


def test_dispatch_reads_systemd_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_platform, "is_windows", lambda: False)
    asked: list[str] = []

    def fake(unit: str) -> tuple[datetime | None, bool]:
        asked.append(unit)
        return None, False

    monkeypatch.setattr(systemd_probe, "unit_last_completion", fake)

    scheduler_last_completion("buibui-signal-watch.service")

    assert asked == ["buibui-signal-watch.service"], (
        "the systemd side must get the UNIT name, suffix included"
    )


def test_dispatch_reads_task_scheduler_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(host_platform, "is_windows", lambda: True)
    asked: list[tuple[str, str]] = []

    def fake(path: str, name: str) -> str | None:
        asked.append((path, name))
        return _LIVE_OK

    monkeypatch.setattr(task_probe, "powershell_task_info", fake)

    stamp, ok = scheduler_last_completion("buibui-signal-watch.service")

    assert asked == [("\\buibui\\", "buibui-signal-watch")], (
        "the Task Scheduler side must get the STRIPPED name, or it asks about nothing"
    )
    assert (stamp, ok) == (datetime(2026, 9, 18, 1, 26, 44, tzinfo=UTC), True)


# ---------------------------------------------------------------- task_state
#
# Added 2026-09-22 with the daily check's off-site leg, which could not be ported to
# Windows without it: `task_last_completion` answers "absent" and "never ran" with the
# same `(None, False)`, and that leg's amber-vs-red split is exactly that distinction.


def _state_reader(text: str | None) -> task_probe.TaskStateReader:
    def read(path: str, name: str) -> str | None:
        return text

    return read


@pytest.mark.parametrize("state", ["Ready", "Disabled", "Running", "Queued"])
def test_task_state_returns_the_state_name(state: str) -> None:
    assert task_state(DEFAULT_TASK_PATH, "x", reader=_state_reader(state)) == state


@pytest.mark.parametrize(
    "text", [None, "", "   "], ids=["absent", "empty", "whitespace"]
)
def test_unreadable_state_is_none(text: str | None) -> None:
    """MUTATION GUARD on the `or None` in `powershell_task_state`.

    An empty stdout is what a `Get-ScheduledTask` that printed nothing leaves behind.
    Returning `""` instead of None reads as TRUTHY-ABSENT at the caller: the off-site
    leg's `_o_state is None` test goes False, it skips the amber "not installed" row
    and reports a RED about a task that may not exist. Amber and red are the two
    answers that leg exists to keep apart, so this is not a tidiness assertion.
    """
    assert task_state(DEFAULT_TASK_PATH, "x", reader=_state_reader(text)) is None


def test_task_state_tells_absent_from_never_ran() -> None:
    """The whole reason this function exists, asserted side by side.

    Same task, same absence: `task_last_completion` cannot distinguish a task that is
    not installed from one installed and never run, and its docstring says the caller
    decides. `task_state` is how the caller decides. If these two ever agree, the
    off-site leg has lost its amber-vs-red split and nothing else would notice.
    """
    never_ran = f"2026-09-18T01:26:44.0000000Z {SCHED_S_TASK_HAS_NOT_RUN}"

    assert task_last_completion(DEFAULT_TASK_PATH, "x", reader=_reader(never_ran)) == (
        None,
        False,
    )
    assert task_last_completion(DEFAULT_TASK_PATH, "x", reader=_reader(None)) == (
        None,
        False,
    )

    assert task_state(DEFAULT_TASK_PATH, "x", reader=_state_reader("Ready")) == "Ready"
    assert task_state(DEFAULT_TASK_PATH, "x", reader=_state_reader(None)) is None


def test_the_state_reader_is_resolved_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION GUARD on `reader or powershell_task_state`.

    Written as `reader: TaskStateReader = powershell_task_state` the default binds the
    function OBJECT at import, so this monkeypatch silently has no effect and the test
    shells out to real PowerShell — passing on Windows, hanging or failing elsewhere,
    and testing nothing either way. Its sibling `task_last_completion` carries the same
    guard in a comment; this is the assertion.
    """
    monkeypatch.setattr(task_probe, "powershell_task_state", lambda path, name: "Ready")

    assert task_state(DEFAULT_TASK_PATH, "buibui-backup-offsite") == "Ready"


def test_disabled_is_spelled_the_way_task_scheduler_spells_it() -> None:
    """`Get-ScheduledTask`'s `State` is an enum NAME, so the casing is the contract.

    Pinned as a constant rather than written at the call site because the off-site leg
    compares against it to decide FROZEN-vs-healthy; a lowercase "disabled" there would
    never match and a disabled backup task would read as merely silent.
    """
    assert TASK_STATE_DISABLED == "Disabled"


def test_the_installer_and_the_probe_agree_on_the_task_path_constant() -> None:
    """Sibling of the signature test above, one level down.

    `DEFAULT_TASK_PATH` now backs both `scheduler_last_completion`'s default AND the
    off-site leg's direct `task_state` call, so the installer must agree with the
    CONSTANT and not merely with one function's default — those could drift apart the
    moment someone re-inlines the literal.
    """
    text = _INSTALLER.read_text(encoding="utf-8")
    declared = re.search(r"\$TaskPath = '([^']+)'", text)
    assert declared, "no $TaskPath default found in the installer"
    assert declared.group(1) == DEFAULT_TASK_PATH


def test_a_time_limit_kill_is_readable_as_its_own_result() -> None:
    """#1031: the off-site task's 10-10 run read exactly this after a 3h-cap kill."""
    out = "2026-10-10T13:26:12.0000000Z 267014"

    assert task_last_result("\buibui\\", "x", reader=_reader(out)) == (
        SCHED_S_TASK_TERMINATED
    )
    # ... and it still folds to a FAILURE for every caller that reads only the bool.
    assert task_last_completion("\buibui\\", "x", reader=_reader(out))[1] is False


@pytest.mark.parametrize("text", [None, "", "garbage", "2026-10-10T13:26:12Z"])
def test_an_unreadable_result_is_none(text: str | None) -> None:
    assert task_last_result("\buibui\\", "x", reader=_reader(text)) is None
