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

`TestSpendSessionIndex` covers ST78, which is the same failure one level out:
`transcript-archive/` is excluded from this backup as "regenerable" when nothing
regenerates a transcript, and `budget.py` scans it to turn a weekly spend total
from a floor into an exact sum. The script now refreshes the DERIVED per-session
index into the already-covered `tools/` tree instead, so the property under test
is that the refresh happens BEFORE that tree is copied — a stale index would be
copied happily and read as current.
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


def _run(
    repo: Path, tmp_path: Path, *args: str, home: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "BUIBUI_BACKUP_ROOT": str(tmp_path / "backups"),
        "BUIBUI_LOCK_RETRIES": "1",
        "BUIBUI_LOCK_SLEEP": "0",
    }
    # Every EXTERNAL_* path is spelled relative to $HOME, so overriding it is what
    # isolates these tests from the operator's real ~/.claude-personal tree.
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(  # noqa: S603
        # Through bash, not by shebang -- Windows cannot exec a `.sh`.
        # See the same note in `test_run_job_wrapper.py`.
        ["bash", str(repo / "deploy" / "backup-analytics.sh"), *args],
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

    def test_the_catch_up_watermark_is_covered(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """`signal_state.json` sits at the repo ROOT, so the `docs/plans/*` glob that
        covers every other ledger cannot reach it -- it needs its own entry, and until
        2026-09-18 it had none.

        The cost is measured rather than hypothetical. A Windows-migration restore
        brought back a 2026-09-15 snapshot with no watermark, so every
        (symbol, tf, strategy) key read as cold; `scanner.py`'s cold-start guard then
        keeps ONLY the latest closed candle for an unwatermarked key, and `--catch-up`
        replayed nothing. Three days of fires were lost. The OHLCV bars and the outcome
        resolutions both came back -- only the fires depend on this file.

        ⚠ Its loss is silent in BOTH directions: no error, and no burst of stale
        alerts either. It just quietly narrows what catch-up will replay, which is why
        nothing caught it for three days.
        """
        (fake_repo / "signal_state.json").write_text('{"BTCUSDT:15m:bos:4": 1}\n')

        r = _run(fake_repo, tmp_path)

        assert r.returncode == 0, r.stdout + r.stderr
        snapshots = sorted((tmp_path / "backups" / "daily").iterdir())
        assert snapshots, "no snapshot was written"
        copied = snapshots[-1] / "signal_state.json"
        assert copied.exists(), "the catch-up watermark was not backed up"
        assert copied.read_text() == '{"BTCUSDT:15m:bos:4": 1}\n'

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


STUB_BUDGET = """import sys
from pathlib import Path

# Stands in for the account-level tracker, which lives outside this repo. It writes the
# same artifact at the same place; what is under test is the SCRIPT's wiring, not it.
if "--export-index" in sys.argv:
    out = Path(__file__).with_name("budget-session-index.jsonl")
    out.write_text('{"week": "2026-08-25", "session": "abc", "units": 1.0}\\n')
    print(f"{out}: 1 session row(s), 1 new this run")
"""


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """A $HOME whose `.claude-personal/tools/` holds a stub tracker."""
    tools = tmp_path / "home" / ".claude-personal" / "tools"
    tools.mkdir(parents=True)
    (tools / "budget.py").write_text(STUB_BUDGET)
    (tools / "budget-repos.json").write_text("{}\n")
    return tmp_path / "home"


class TestSpendSessionIndex:
    """ST78: the derived index must be refreshed BEFORE `tools/` is copied."""

    def test_the_index_is_refreshed_and_lands_in_the_snapshot(
        self, fake_repo: Path, tmp_path: Path, fake_home: Path
    ) -> None:
        """The index does not exist before the run; the snapshot must still carry it."""
        index = fake_home / ".claude-personal" / "tools" / "budget-session-index.jsonl"
        assert not index.exists(), "fixture must not pre-create the artifact under test"

        r = _run(fake_repo, tmp_path, home=fake_home)

        assert r.returncode == 0, r.stdout + r.stderr
        snapshots = sorted((tmp_path / "backups" / "daily").iterdir())
        assert snapshots, "no snapshot was written"
        copied = (
            snapshots[-1]
            / "_external"
            / "claude-personal"
            / ".claude-personal"
            / "tools"
            / "budget-session-index.jsonl"
        )
        assert copied.exists(), "the refreshed index was not copied into the snapshot"
        assert "2026-08-25" in copied.read_text()

    def test_a_stale_index_is_overwritten_before_the_copy(
        self, fake_repo: Path, tmp_path: Path, fake_home: Path
    ) -> None:
        """The regression that matters: ordering. A pre-existing index must not survive.

        If the refresh ran AFTER the copy — or not at all — the snapshot would carry the
        stale bytes while reading as current, which is the whole failure ST78 describes.
        """
        index = fake_home / ".claude-personal" / "tools" / "budget-session-index.jsonl"
        index.write_text('{"week": "STALE", "session": "old", "units": 0.0}\n')

        r = _run(fake_repo, tmp_path, home=fake_home)

        assert r.returncode == 0, r.stdout + r.stderr
        snapshots = sorted((tmp_path / "backups" / "daily").iterdir())
        copied = (
            snapshots[-1]
            / "_external"
            / "claude-personal"
            / ".claude-personal"
            / "tools"
            / "budget-session-index.jsonl"
        )
        assert "STALE" not in copied.read_text()
        assert "2026-08-25" in copied.read_text()

    def test_the_dry_run_reports_the_same_step(
        self, fake_repo: Path, tmp_path: Path, fake_home: Path
    ) -> None:
        """Same two-loop discipline as the glob: the report must not omit a real step."""
        r = _run(fake_repo, tmp_path, "--dry-run", home=fake_home)

        assert r.returncode == 0, r.stdout + r.stderr
        assert "spend-idx" in r.stdout
        assert "budget-session-index.jsonl" in r.stdout

    def test_an_absent_tracker_does_not_fail_the_backup(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """budget.py is account-level and legitimately absent on another box.

        The crown jewels are verified before this step, so a missing optional tool must
        never take the backup down with it.
        """
        bare_home = tmp_path / "bare-home"
        bare_home.mkdir()

        r = _run(fake_repo, tmp_path, home=bare_home)

        assert r.returncode == 0, r.stdout + r.stderr
        snapshots = sorted((tmp_path / "backups" / "daily").iterdir())
        assert snapshots, "a missing optional tracker must not prevent the snapshot"
