"""Tests for LEDGERS coverage in `deploy/backup-analytics.sh`.

Everything that array covers is gitignored and single-copy, so the array IS the
only copy. It was an allowlist of literal paths until 2026-08-20, and an
allowlist over such a tree defaults to UNCOVERED: a new artifact stays invisible
until someone diffs the backup against the live tree. That diff has been run
three times and found something new every time — six artifacts in 2026-08-08,
five in 2026-08-11, and two more this round (a hand-run gate script living beside
the health check, and a hand-taken `.bak` of a covered ledger).

So the property under test is not "these files are covered" — that is the
allowlist thinking that failed — but **a file nobody listed is covered anyway**.
`test_a_brand_new_artifact_is_covered_without_being_listed` is the whole point of
the suite; the rest guard the mechanics that make it work.

Both the dry-run report and the real copy loop are exercised, because they are
two separate loops over the same array and a glob that expanded in only one of
them would report coverage it does not deliver.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "backup-analytics.sh"


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A minimal repo the script will accept, with the real script inside it.

    The script resolves `REPO` from its OWN location (`cd $(dirname $0)/..`), so
    relocating it is the only way to point it at a fixture tree.
    """
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "deploy" / "backup-analytics.sh")
    (repo / "deploy" / "backup-analytics.sh").chmod(0o755)

    (repo / "docs" / "plans").mkdir(parents=True)
    (repo / "docs" / "plans" / "thesis-inbox.md").write_text("listed by name before\n")
    (repo / "docs" / "plans" / "__pycache__").mkdir()
    (repo / "docs" / "plans" / "__pycache__" / "x.pyc").write_text("build artifact\n")

    # The verify step runs `$REPO/.venv/bin/python` when it is executable, and the
    # interpreter running these tests is the one that has duckdb.
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    # A wrapper, not a symlink: python resolves its venv root from argv[0], so a
    # symlink into a fixture tree finds no site-packages and imports no duckdb.
    shim = venv_bin / "python"
    shim.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n')
    shim.chmod(0o755)

    # A snapshot with 0 `signal_alert_outcomes` rows is refused by design, so the
    # fixture DB has to carry one.
    con = duckdb.connect(str(repo / "analytics.db"))
    con.execute("CREATE TABLE signal_alert_outcomes (id INTEGER)")
    con.execute("INSERT INTO signal_alert_outcomes VALUES (1)")
    con.close()
    return repo


def _run(repo: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "BUIBUI_BACKUP_ROOT": str(tmp_path / "backups"),
        "BUIBUI_LOCK_RETRIES": "1",
        "BUIBUI_LOCK_SLEEP": "0",
    }
    return subprocess.run(  # noqa: S603
        [str(repo / "deploy" / "backup-analytics.sh"), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


class TestLedgerGlobCoverage:
    """A file under `docs/plans/` is covered because of where it is, not who listed it."""

    def test_a_brand_new_artifact_is_covered_without_being_listed(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """The regression that matters: this file's name appears nowhere in the script."""
        (fake_repo / "docs" / "plans" / "brand-new-ledger.jsonl").write_text(
            '{"a": 1}\n'
        )

        r = _run(fake_repo, tmp_path)

        assert r.returncode == 0, r.stdout + r.stderr
        snapshots = sorted((tmp_path / "backups" / "daily").iterdir())
        assert snapshots, "no snapshot was written"
        copied = snapshots[-1] / "docs" / "plans" / "brand-new-ledger.jsonl"
        assert copied.read_text() == '{"a": 1}\n'
        assert "brand-new-ledger" not in SCRIPT.read_text()

    def test_the_dry_run_report_lists_the_same_unlisted_artifact(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """The report and the copy are separate loops; a glob must expand in both."""
        (fake_repo / "docs" / "plans" / "brand-new-ledger.jsonl").write_text("{}\n")

        r = _run(fake_repo, tmp_path, "--dry-run")

        assert r.returncode == 0, r.stdout + r.stderr
        assert "ledger     docs/plans/brand-new-ledger.jsonl" in r.stdout
        assert not (tmp_path / "backups" / "daily").exists()

    def test_a_directory_under_plans_is_not_copied_as_a_file(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """`__pycache__` matches the glob; the `-f` test is what rejects it."""
        r = _run(fake_repo, tmp_path)

        assert r.returncode == 0, r.stdout + r.stderr
        snapshot = sorted((tmp_path / "backups" / "daily").iterdir())[-1]
        assert not (snapshot / "docs" / "plans" / "__pycache__").exists()

    def test_a_pattern_matching_nothing_is_reported_not_fatal(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """An absent literal entry must still be visible — that is what the report is for."""
        r = _run(fake_repo, tmp_path, "--dry-run")

        assert r.returncode == 0, r.stdout + r.stderr
        assert "config/pundit_roster.toml -- NO MATCH" in r.stdout

    def test_an_empty_plans_tree_does_not_copy_the_literal_pattern(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """An unmatched glob expands to ITSELF in bash, so the `-f` test is load-bearing."""
        for f in (fake_repo / "docs" / "plans").iterdir():
            if f.is_file():
                f.unlink()
            else:
                shutil.rmtree(f)

        r = _run(fake_repo, tmp_path)

        assert r.returncode == 0, r.stdout + r.stderr
        snapshot = sorted((tmp_path / "backups" / "daily").iterdir())[-1]
        assert not (snapshot / "docs" / "plans" / "*").exists()
