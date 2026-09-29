"""Tests for the destination guards in `deploy/backup-offsite.sh`.

`rclone sync` mirrors deletions into its destination, so every one of these
guards stands between a mistyped env var and the permanent loss of data this
repo does not own. Nothing else pins them: there is no shellcheck in this
repo and no CI step reads this script, so these tests are the only gate.

Two design choices keep the suite from going vacuous — the failure mode where
a guard is "verified" by a fixture that could never have reached the guarded
call in the first place (see the vacuous-guard-fixture trap):

1. Every rejection test asserts `sync` was NEVER invoked, not merely that the
   exit code was 1. A script that died for an unrelated reason also exits 1.
2. `test_destination_matching_local_root_is_allowed` is the positive control
   for the intruder check. Without it, that guard would pass just as well if
   it rejected *any* non-empty destination, which would break every sync after
   the first one.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "backup-offsite.sh"


@pytest.fixture
def backup_root(tmp_path: Path) -> Path:
    """A backup root that passes the pre-existing source-side guards."""
    snapshot = tmp_path / "root" / "daily" / "2026-01-01"
    snapshot.mkdir(parents=True)
    (snapshot / "MANIFEST.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (tmp_path / "root" / "weekly").mkdir()
    return tmp_path / "root"


@pytest.fixture
def fake_rclone(tmp_path: Path) -> Path:
    """A shim that records its argv and replays scripted `lsf` output.

    Returning success for every subcommand is deliberate: it means a guard that
    fails to fire produces a *passing* sync, so the rejection tests below can
    only pass because the guard actually blocked.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "rclone"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$@" >> "$RCLONE_LOG"\n'
        'case "$1" in\n'
        '  lsf) printf %s "${FAKE_LSF_OUTPUT:-}"\n'
        '       [ -n "${FAKE_LSF_STDERR:-}" ] && echo "$FAKE_LSF_STDERR" >&2\n'
        '       exit "${FAKE_LSF_RC:-0}" ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return bindir


def run_script(
    backup_root: Path,
    fake_rclone: Path,
    tmp_path: Path,
    remote: str | None,
    lsf_output: str = "",
    lsf_rc: int = 0,
    lsf_stderr: str = "",
) -> tuple[int, str, list[str]]:
    """Run the script; return (exit code, stderr, rclone subcommands invoked)."""
    log = tmp_path / "rclone.log"
    log.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{fake_rclone}{os.pathsep}{env['PATH']}",
            "BUIBUI_BACKUP_ROOT": str(backup_root),
            "RCLONE_LOG": str(log),
            "FAKE_LSF_OUTPUT": lsf_output,
            "FAKE_LSF_RC": str(lsf_rc),
            "FAKE_LSF_STDERR": lsf_stderr,
        }
    )
    if remote is None:
        env.pop("BUIBUI_BACKUP_REMOTE", None)
    else:
        env["BUIBUI_BACKUP_REMOTE"] = remote

    proc = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=20
    )
    calls = [
        line.split()[0]
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return proc.returncode, proc.stderr, calls


def test_wellformed_remote_syncs(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """Positive control: without it, every rejection below could be a no-op."""
    rc, _, calls = run_script(backup_root, fake_rclone, tmp_path, "gdrive:buibui")
    assert rc == 0
    assert "sync" in calls


def test_bare_remote_without_path_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """`gdrive:` is the ENTIRE drive, and sync mirrors deletions into it."""
    rc, err, calls = run_script(backup_root, fake_rclone, tmp_path, "gdrive:")
    assert rc == 1
    assert "sync" not in calls
    assert "no path component" in err


def test_remote_without_colon_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """No colon means rclone writes LOCALLY — green job, no off-machine copy."""
    rc, err, calls = run_script(backup_root, fake_rclone, tmp_path, "gdrive")
    assert rc == 1
    assert "sync" not in calls
    assert "is not a remote" in err


def test_unset_remote_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    rc, err, calls = run_script(backup_root, fake_rclone, tmp_path, None)
    assert rc == 1
    assert "sync" not in calls
    assert "BUIBUI_BACKUP_REMOTE is unset" in err


def test_foreign_destination_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """A well-formed remote aimed at somebody else's folder must not sync."""
    rc, err, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        "gdrive:buibui",
        lsf_output="daily/\nPhotos/\nresume.pdf\n",
    )
    assert rc == 1
    assert "sync" not in calls
    assert "Photos" in err
    assert "resume.pdf" in err
    assert "daily" not in err  # the entry we DO own must not be flagged


def test_destination_matching_local_root_is_allowed(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """The control for the guard above: a populated OWN destination must pass.

    Every sync after the first one hits this path. A guard that rejected any
    non-empty destination would pass the intruder test and break production.
    """
    rc, _, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        "gdrive:buibui",
        lsf_output="daily/\nweekly/\n",
    )
    assert rc == 0
    assert "sync" in calls


def test_unlistable_destination_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """ST146: a listing that FAILED is not an empty destination.

    Before the fix, lsf's stderr and exit code were both discarded, so a broken
    config listed nothing and the intruder guard passed without having looked.
    Measured live: a missing config exits 1.
    """
    rc, err, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        "gdrive:buibui",
        lsf_rc=1,
        lsf_stderr='CRITICAL: didn\'t find section in config file ("gdrive")',
    )
    assert rc == 1
    assert "sync" not in calls
    assert "could not list" in err
    assert "didn't find section" in err  # rclone's own reason is surfaced


def test_absent_destination_still_syncs(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """The control for the test above: rc 3 IS an empty destination.

    An absent Drive folder makes lsf exit 3 (measured live), and the first-ever
    sync depends on that passing. A guard that failed on ANY non-zero lsf would
    pass the test above and make the off-site leg impossible to bootstrap.
    """
    rc, _, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        "gdrive:buibui",
        lsf_rc=3,
        lsf_stderr="NOTICE: Failed to lsf: directory not found",
    )
    assert rc == 0
    assert "sync" in calls


def test_same_shaped_sibling_destination_is_NOT_blocked(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """Documents a KNOWN LIMIT rather than asserting a protection.

    The intruder check compares TOP-LEVEL entries only, so a sibling repo whose
    tree is also `daily/` + `weekly/` is indistinguishable from our own data and
    the sync proceeds. Measured against the live remote on 2026-08-15: a
    wifey-shaped root aimed at this repo's destination dry-ran `Skipped delete`
    over real snapshots.

    This test exists so the limit cannot drift away from the docs that state it.
    If someone strengthens the guard, this test fails and AGENTS.md, README.md,
    deploy/README.md and .claude/context/execution.md all need updating with it.

    The real fix is confinement, not detection: a separate remote with its own
    `root_folder_id` makes the collision unreachable.
    """
    rc, _, calls = run_script(
        backup_root,
        fake_rclone,
        tmp_path,
        "gdrive:someone-elses-snapshots",
        lsf_output="daily/\nweekly/\n",
    )
    assert rc == 0
    assert "sync" in calls  # NOT a desirable outcome -- a recorded one


def test_missing_manifest_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    """Pre-existing guard, pinned here because sync would mirror the empty tree."""
    for manifest in backup_root.rglob("MANIFEST.json"):
        manifest.unlink()
    rc, err, calls = run_script(backup_root, fake_rclone, tmp_path, "gdrive:buibui")
    assert rc == 1
    assert "sync" not in calls
    assert "no MANIFEST.json" in err


def test_missing_backup_root_is_rejected(
    backup_root: Path, fake_rclone: Path, tmp_path: Path
) -> None:
    rc, err, calls = run_script(
        backup_root.parent / "absent", fake_rclone, tmp_path, "gdrive:buibui"
    )
    assert rc == 1
    assert "sync" not in calls
    assert "does not exist" in err
