"""Behavioural tests for `tools/systemd_probe.py` — the daily check's off-site leg.

The defect this guards was measured 2026-08-19. `systemctl show` returns EMPTY
for every timestamp field of a unit systemd has garbage-collected out of memory,
which is the normal resting state of an inactive timer-triggered `static` unit.
`buibui-backup-offsite.service` was in exactly that state while its journal
showed four consecutive clean nightly runs, so a tier-1 line read "the sync has
NEVER run" every day against a healthy off-machine copy — and a permanently-red
tier 1 trains the operator to ignore the whole report.

These cases were a hand-run mutation script living beside the gitignored
`daily_check.py`. It proved every branch reachable and reported to nobody: CI
could not import what it was testing, so it only ever ran when a session
remembered to. Three cases below are new, and all three are branches that script
never reached — a clean shutdown with no "Finished", an unparseable timestamp,
and a journal that cannot be read at all.
"""

from __future__ import annotations

from datetime import datetime

from tools.systemd_probe import JournalReader, unit_last_completion


def _fixed(text: str | None) -> JournalReader:
    """A `JournalReader` that returns `text` for any unit."""

    def _read(unit: str) -> str | None:  # noqa: ARG001 - fixture ignores the unit
        return text

    return _read


def _iso(stamp: datetime | None) -> str | None:
    return stamp.isoformat() if stamp else None


class TestUnitLastCompletion:
    """The parser reads the LAST completion, and its cleanliness, from the journal."""

    def test_clean_run_reports_the_finish_time_in_utc(self) -> None:
        journal = (
            "2026-08-18T21:27:37+08:00 fedora systemd[1]: Starting buibui off-machine backup...\n"
            "2026-08-18T21:40:24+08:00 fedora systemd[1]: Finished buibui off-machine backup.\n"
            "2026-08-18T21:40:24+08:00 fedora systemd[1]: Consumed 11.778s CPU time"
        )
        stamp, ok = unit_last_completion("x.service", journal=_fixed(journal))
        assert (_iso(stamp), ok) == ("2026-08-18T13:40:24+00:00", True)

    def test_failed_run_reports_the_failure_time_and_not_ok(self) -> None:
        journal = (
            "2026-08-18T21:27:37+08:00 fedora systemd[1]: Starting buibui off-machine backup...\n"
            "2026-08-18T21:29:02+08:00 fedora systemd[1]: buibui-backup-offsite.service: "
            "Failed with result 'exit-code'."
        )
        stamp, ok = unit_last_completion("x.service", journal=_fixed(journal))
        assert (_iso(stamp), ok) == ("2026-08-18T13:29:02+00:00", False)

    def test_a_later_success_supersedes_an_earlier_failure(self) -> None:
        journal = (
            "2026-08-17T21:29:02+08:00 fedora systemd[1]: buibui-backup-offsite.service: "
            "Failed with result 'exit-code'.\n"
            "2026-08-18T21:40:24+08:00 fedora systemd[1]: Finished buibui off-machine backup."
        )
        stamp, ok = unit_last_completion("x.service", journal=_fixed(journal))
        assert (_iso(stamp), ok) == ("2026-08-18T13:40:24+00:00", True)

    def test_a_later_failure_supersedes_an_earlier_success(self) -> None:
        """The LAST completion wins, not the last success — a stale green is the defect."""
        journal = (
            "2026-08-17T21:40:24+08:00 fedora systemd[1]: Finished buibui off-machine backup.\n"
            "2026-08-18T21:29:02+08:00 fedora systemd[1]: buibui-backup-offsite.service: "
            "Failed with result 'exit-code'."
        )
        stamp, ok = unit_last_completion("x.service", journal=_fixed(journal))
        assert (_iso(stamp), ok) == ("2026-08-18T13:29:02+00:00", False)

    def test_a_clean_deactivation_counts_as_a_completion(self) -> None:
        """`Deactivated successfully` is how a `oneshot` unit ends without "Finished"."""
        journal = (
            "2026-08-18T21:40:24+08:00 fedora systemd[1]: buibui-backup-offsite.service: "
            "Deactivated successfully."
        )
        stamp, ok = unit_last_completion("x.service", journal=_fixed(journal))
        assert (_iso(stamp), ok) == ("2026-08-18T13:40:24+00:00", True)

    def test_empty_journal_reports_no_completion(self) -> None:
        stamp, ok = unit_last_completion("x.service", journal=_fixed(""))
        assert (stamp, ok) == (None, False)

    def test_unreadable_journal_reports_no_completion(self) -> None:
        """No `journalctl` on the box is not the same as a failed run, but reads as unknown."""
        stamp, ok = unit_last_completion("x.service", journal=_fixed(None))
        assert (stamp, ok) == (None, False)

    def test_a_start_with_no_completion_reports_no_completion(self) -> None:
        journal = "2026-08-18T21:27:37+08:00 fedora systemd[1]: Starting buibui off-machine backup..."
        stamp, ok = unit_last_completion("x.service", journal=_fixed(journal))
        assert (stamp, ok) == (None, False)

    def test_an_unparseable_timestamp_is_skipped_not_crashed(self) -> None:
        """A malformed line must not take the whole daily check down with it."""
        journal = (
            "not-a-timestamp fedora systemd[1]: Finished buibui off-machine backup.\n"
            "2026-08-18T21:40:24+08:00 fedora systemd[1]: Finished buibui off-machine backup."
        )
        stamp, ok = unit_last_completion("x.service", journal=_fixed(journal))
        assert (_iso(stamp), ok) == ("2026-08-18T13:40:24+00:00", True)

    def test_a_line_with_no_space_is_skipped(self) -> None:
        stamp, ok = unit_last_completion("x.service", journal=_fixed("Finished"))
        assert (stamp, ok) == (None, False)


class TestJournalTailIsTheDefault:
    """The production reader is wired in by default, so the caller passes nothing."""

    def test_default_reader_is_journal_tail(self) -> None:
        import inspect

        from tools.systemd_probe import journal_tail

        sig = inspect.signature(unit_last_completion)
        assert sig.parameters["journal"].default is journal_tail
