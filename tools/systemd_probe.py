"""When a systemd user unit last FINISHED, and whether it finished cleanly.

Read from the JOURNAL, not from `systemctl show`.

Why: `systemctl show -p InactiveEnterTimestamp` returns EMPTY for a unit systemd
has garbage-collected out of memory — as do `StateChangeTimestamp`,
`ExecMainExitTimestamp` and every sibling field. An inactive, timer-triggered,
`static` unit with no references is exactly that case. Measured 2026-08-19:
`buibui-backup-offsite.service` returned empty for all seven fields while its
journal showed four consecutive clean nightly runs, so the daily check's off-site
leg reported "the sync has NEVER run" every day against a healthy off-machine
copy. A permanently-red tier 1 trains the operator to ignore the report, so the
fix is a source that survives unit unloading rather than a different field on the
same dead source.

This lives in `tools/` rather than inside `docs/plans/daily_check.py` on purpose,
following `tools/media_probe.py` (ST43): that file is gitignored, so logic buried
there reaches no reclone, no CI and not the wifey fork. Before this module the
only test was a hand-run script that regex-extracted the function body out of the
gitignored source and `exec`'d it — it proved the branches reachable and reported
to nobody, because nothing in CI could import what it was testing.

The journal read is injected rather than called directly, so the parser is tested
against real `short-iso` journal text with no `journalctl` on the box.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import Protocol

_JOURNAL_LINES = 400
_JOURNAL_TIMEOUT_S = 15


class JournalReader(Protocol):
    """Returns `journalctl` stdout for a unit, or None when it cannot be read."""

    def __call__(self, unit: str) -> str | None: ...


def journal_tail(unit: str) -> str | None:
    """`journalctl --user -u <unit>` stdout, bounded so a huge journal cannot stall.

    `-u` (unit lifecycle: "Finished" / "Failed") rather than `-t` (the job's own
    tagged output) — the wrapper's tag says what the script printed, not how it
    exited.
    """
    try:
        r = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "journalctl",
                "--user",
                "-u",
                unit,
                "-o",
                "short-iso",
                "--no-pager",
                "-n",
                str(_JOURNAL_LINES),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_JOURNAL_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip()


def unit_last_completion(
    unit: str,
    *,
    journal: JournalReader = journal_tail,
) -> tuple[datetime | None, bool]:
    """`(when the unit last finished, whether that last completion was clean)`.

    `(None, False)` means no completion is visible at all — an absent journal, an
    empty one, or one showing only a start with no matching end. That is
    deliberately indistinguishable from "never ran" at this layer: the caller
    decides whether a missing completion is a red or a not-yet.

    The LAST completion wins, not the last success: a unit that succeeded on
    Monday and failed on Tuesday reports Tuesday's failure, because a stale
    success is what made the old `systemctl show` reading dangerous.
    """
    out = journal(unit)
    if not out:
        return None, False
    stamp: datetime | None = None
    ok = False
    for raw in out.splitlines():
        # short-iso: "2026-08-18T21:40:24+08:00 host systemd[N]: <message>"
        head, sep, msg = raw.partition(" ")
        if not sep:
            continue
        if "Finished" in msg:
            good = True
        elif "Failed" in msg or "Deactivated successfully" in msg:
            good = "Failed" not in msg
        else:
            continue
        try:
            stamp, ok = datetime.fromisoformat(head).astimezone(UTC), good
        except ValueError:
            continue
    return stamp, ok
