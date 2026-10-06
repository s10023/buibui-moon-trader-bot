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

import json
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
    (repo / "docs" / "plans" / "thesis-inbox.md").write_text(
        "listed by name before\n", encoding="utf-8"
    )
    (repo / "docs" / "plans" / "__pycache__").mkdir()
    (repo / "docs" / "plans" / "__pycache__" / "x.pyc").write_text(
        "build artifact\n", encoding="utf-8"
    )

    # The verify step runs `$REPO/.venv/bin/python` when it is executable, and the
    # interpreter running these tests is the one that has duckdb.
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    # A wrapper, not a symlink: python resolves its venv root from argv[0], so a
    # symlink into a fixture tree finds no site-packages and imports no duckdb.
    shim = venv_bin / "python"
    shim.write_text(
        f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n', encoding="utf-8"
    )
    shim.chmod(0o755)

    # A snapshot with 0 `signal_alert_outcomes` rows is refused by design, so the
    # fixture DB has to carry one.
    con = duckdb.connect(str(repo / "analytics.db"))
    con.execute("CREATE TABLE signal_alert_outcomes (id INTEGER)")
    con.execute("INSERT INTO signal_alert_outcomes VALUES (1)")
    con.close()
    return repo


def _run(
    repo: Path,
    tmp_path: Path,
    *args: str,
    home: Path | None = None,
    path_prepend: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "BUIBUI_BACKUP_ROOT": str(tmp_path / "backups"),
        "BUIBUI_LOCK_RETRIES": "1",
        "BUIBUI_LOCK_SLEEP": "0",
    }
    # Shadowing a coreutil is how the publish-retry tests inject a transient
    # failure -- the script calls `mv` exactly once, so the stub is precise.
    if path_prepend is not None:
        env["PATH"] = f"{path_prepend}{os.pathsep}{env.get('PATH', '')}"
    env.update(env_extra or {})
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
            '{"a": 1}\n', encoding="utf-8"
        )

        r = _run(fake_repo, tmp_path)

        assert r.returncode == 0, r.stdout + r.stderr
        snapshots = sorted((tmp_path / "backups" / "daily").iterdir())
        assert snapshots, "no snapshot was written"
        copied = snapshots[-1] / "docs" / "plans" / "brand-new-ledger.jsonl"
        assert copied.read_text(encoding="utf-8") == '{"a": 1}\n'
        assert "brand-new-ledger" not in SCRIPT.read_text(encoding="utf-8")

    def test_the_dry_run_report_lists_the_same_unlisted_artifact(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """The report and the copy are separate loops; a glob must expand in both."""
        (fake_repo / "docs" / "plans" / "brand-new-ledger.jsonl").write_text(
            "{}\n", encoding="utf-8"
        )

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
        (fake_repo / "signal_state.json").write_text(
            '{"BTCUSDT:15m:bos:4": 1}\n', encoding="utf-8"
        )

        r = _run(fake_repo, tmp_path)

        assert r.returncode == 0, r.stdout + r.stderr
        snapshots = sorted((tmp_path / "backups" / "daily").iterdir())
        assert snapshots, "no snapshot was written"
        copied = snapshots[-1] / "signal_state.json"
        assert copied.exists(), "the catch-up watermark was not backed up"
        assert copied.read_text(encoding="utf-8") == '{"BTCUSDT:15m:bos:4": 1}\n'

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
    (tools / "budget.py").write_text(STUB_BUDGET, encoding="utf-8")
    (tools / "budget-repos.json").write_text("{}\n", encoding="utf-8")
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
        assert "2026-08-25" in copied.read_text(encoding="utf-8")

    def test_a_stale_index_is_overwritten_before_the_copy(
        self, fake_repo: Path, tmp_path: Path, fake_home: Path
    ) -> None:
        """The regression that matters: ordering. A pre-existing index must not survive.

        If the refresh ran AFTER the copy — or not at all — the snapshot would carry the
        stale bytes while reading as current, which is the whole failure ST78 describes.
        """
        index = fake_home / ".claude-personal" / "tools" / "budget-session-index.jsonl"
        index.write_text(
            '{"week": "STALE", "session": "old", "units": 0.0}\n', encoding="utf-8"
        )

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
        assert "STALE" not in copied.read_text(encoding="utf-8")
        assert "2026-08-25" in copied.read_text(encoding="utf-8")

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


class TestPublishSurvivesATransientLock:
    """Publishing the snapshot must survive a momentary lock on its own files.

    Windows refuses to rename a directory while ANY file inside it is open, and a
    freshly written ~259 MiB `.db` is exactly what a virus scanner or the search
    indexer opens the instant it lands. Observed 2026-09-18: the 15:41 run died at
    the publish `mv` with `Permission denied` having already built AND verified a
    complete snapshot, while the 20:41 run published the identical tree cleanly.
    Giving up on the first attempt discarded the entire run's work.

    Linux never takes the retry -- rename(2) there does not care about open
    handles -- so on the VPS the first attempt succeeds and the loop exits at once.
    """

    @staticmethod
    def _flaky_mv(tmp_path: Path, *, fail_times: int) -> Path:
        """A `mv` that fails `fail_times` times with the real error, then works."""
        real_mv = shutil.which("mv")
        assert real_mv, "these tests need a real `mv` to delegate to"

        bin_dir = tmp_path / "flaky-bin"
        bin_dir.mkdir()
        counter = tmp_path / "mv.count"
        stub = bin_dir / "mv"
        stub.write_text(
            "#!/bin/sh\n"
            f'n=$(cat "{counter}" 2>/dev/null || echo 0)\n'
            "n=$((n + 1))\n"
            f'printf %s "$n" > "{counter}"\n'
            f'if [ "$n" -le {fail_times} ]; then\n'
            '  printf "mv: cannot move: Permission denied\n" >&2\n'
            "  exit 1\n"
            "fi\n"
            f'exec "{real_mv}" "$@"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        return bin_dir

    def test_a_transient_publish_failure_is_retried_rather_than_fatal(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        r = _run(
            fake_repo,
            tmp_path,
            path_prepend=self._flaky_mv(tmp_path, fail_times=2),
            env_extra={"BUIBUI_PUBLISH_WAIT": "0"},
        )

        assert r.returncode == 0, r.stdout + r.stderr
        assert "retrying" in r.stdout, f"the retry was never reported: {r.stdout}"
        snapshots = sorted((tmp_path / "backups" / "daily").iterdir())
        assert snapshots, "a transient lock must not cost the whole snapshot"

    def test_a_persistent_failure_still_fails(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """Teeth. A retry that can never give up is a hang, not a fix.

        Without this the loop could retry forever, or swallow a genuine permission
        problem and report a snapshot that was never published -- which is worse
        than the crash being repaired, because the freshness check would then read
        a stale directory as current.
        """
        r = _run(
            fake_repo,
            tmp_path,
            path_prepend=self._flaky_mv(tmp_path, fail_times=10**6),
            env_extra={"BUIBUI_PUBLISH_TRIES": "3", "BUIBUI_PUBLISH_WAIT": "0"},
        )

        assert r.returncode == 1, "a real permission failure must still fail the run"
        assert "after 3 attempts" in r.stderr, r.stderr
        daily = tmp_path / "backups" / "daily"
        published = [d for d in daily.iterdir() if not d.name.startswith(".staging")]
        assert not published, "nothing may be published when the mv never succeeded"


@pytest.fixture
def two_root_home(tmp_path: Path) -> Path:
    """A $HOME carrying a memory tree under BOTH harness config roots.

    `.claude-personal` holds a legacy Linux-slug tree (what a migration restore
    leaves behind); `.claude` holds the live Windows-slug one. The bug this
    guards reproduced only with both present, because the legacy tree is what
    kept the snapshot looking populated.
    """
    home = tmp_path / "home"
    legacy = home / ".claude-personal" / "projects" / "-home-kng-repo-buibui" / "memory"
    legacy.mkdir(parents=True)
    (legacy / "MEMORY.md").write_text("legacy tree\n", encoding="utf-8")

    live = home / ".claude" / "projects" / "C--Users-User-repo-buibui" / "memory"
    live.mkdir(parents=True)
    (live / "MEMORY.md").write_text("live tree\n", encoding="utf-8")
    (live / "project_todo_master.md").write_text("the SoT\n", encoding="utf-8")
    return home


class TestBothConfigRootsAreCovered:
    """The memory tree moved roots at the 2026-09-18 Windows migration.

    `~/.claude-personal` on the old Linux box, `~/.claude` on the laptop. Covering
    only the first kept MATCHING -- on the legacy trees a restore had left behind --
    so the snapshot read populated while the live tree, which is in no git remote,
    was copied nowhere. Same shape as the LEDGERS allowlist defect one array over:
    the glob's miss-is-a-skip contract cannot tell an absent tree from a moved one.
    """

    def test_the_live_tree_under_the_second_root_is_copied(
        self, fake_repo: Path, tmp_path: Path, two_root_home: Path
    ) -> None:
        r = _run(fake_repo, tmp_path, home=two_root_home)
        assert r.returncode == 0, r.stdout + r.stderr

        snapshots = sorted((tmp_path / "backups" / "daily").iterdir())
        assert snapshots, "no snapshot was written"
        live = (
            snapshots[-1]
            / "_external"
            / "claude"
            / "projects"
            / "C--Users-User-repo-buibui"
            / "memory"
        )
        assert (live / "MEMORY.md").exists(), "the live memory tree was not copied"
        assert (live / "project_todo_master.md").exists(), "the SoT was not copied"
        assert (live / "MEMORY.md").read_text(encoding="utf-8").strip() == "live tree"

    def test_the_legacy_root_is_still_copied_beside_it(
        self, fake_repo: Path, tmp_path: Path, two_root_home: Path
    ) -> None:
        """Covering the new root must not silently drop the old one."""
        r = _run(fake_repo, tmp_path, home=two_root_home)
        assert r.returncode == 0, r.stdout + r.stderr

        snapshots = sorted((tmp_path / "backups" / "daily").iterdir())
        legacy = (
            snapshots[-1]
            / "_external"
            / "claude-personal"
            / "projects"
            / "-home-kng-repo-buibui"
            / "memory"
            / "MEMORY.md"
        )
        assert legacy.exists(), "the legacy tree stopped being copied"

    def test_the_two_roots_do_not_collide_in_the_snapshot(
        self, fake_repo: Path, tmp_path: Path, two_root_home: Path
    ) -> None:
        """Distinct dest prefixes -- one overwriting the other is the data-losing bug."""
        _run(fake_repo, tmp_path, home=two_root_home)
        snapshots = sorted((tmp_path / "backups" / "daily").iterdir())
        ext = snapshots[-1] / "_external"
        live = ext / "claude" / "projects" / "C--Users-User-repo-buibui" / "memory"
        legacy = (
            ext / "claude-personal" / "projects" / "-home-kng-repo-buibui" / "memory"
        )
        assert (live / "MEMORY.md").read_text(encoding="utf-8").strip() == "live tree"
        assert (legacy / "MEMORY.md").read_text(
            encoding="utf-8"
        ).strip() == "legacy tree"

    def test_mutation_removing_the_second_root_entry_fails_this_suite(
        self, fake_repo: Path, tmp_path: Path, two_root_home: Path
    ) -> None:
        """Proves the assertions above bite on the ENTRY, not on something incidental."""
        script = fake_repo / "deploy" / "backup-analytics.sh"
        text = script.read_text(encoding="utf-8")
        entry = '    "$HOME/.claude/projects/*/memory:claude/projects"\n'
        assert entry in text, "the guarded entry is not present to mutate"
        script.write_text(text.replace(entry, ""), encoding="utf-8")

        r = _run(fake_repo, tmp_path, home=two_root_home)
        assert r.returncode == 0, r.stdout + r.stderr
        snapshots = sorted((tmp_path / "backups" / "daily").iterdir())
        live = (
            snapshots[-1]
            / "_external"
            / "claude"
            / "projects"
            / "C--Users-User-repo-buibui"
            / "memory"
            / "MEMORY.md"
        )
        assert not live.exists(), (
            "the live tree was copied WITHOUT the entry -- these tests would pass "
            "against the defect and guard nothing"
        )


class TestManifestIsValidJson:
    """MANIFEST.json is built through a JSON encoder, never a printf format string.

    printf interpolated the source path raw, so any field carrying a backslash
    wrote an illegal escape (a Windows `C:` path spelled with backslashes makes
    `\\U`) and the manifest was unparseable. `backup_check` degrades on
    JSONDecodeError, so the symptom was a check stuck reading stale rather than
    an error (wifey #295). The repo path is the field that reaches the manifest
    raw, so a repo whose path carries a backslash AND a double quote is the
    smallest input that broke the old form.
    """

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows cannot create the fixture dir: '\\' is a separator and '\"' is illegal",
    )
    def test_a_path_with_a_backslash_and_a_quote_still_parses(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        awkward = tmp_path / 'we\\ird"dir'
        awkward.mkdir()
        repo = awkward / "repo"
        shutil.move(str(fake_repo), str(repo))

        r = _run(repo, tmp_path)

        assert r.returncode == 0, r.stdout + r.stderr
        snapshot = sorted((tmp_path / "backups" / "daily").iterdir())[-1]
        manifest = json.loads((snapshot / "MANIFEST.json").read_text(encoding="utf-8"))
        assert manifest["source"] == str(repo / "analytics.db")
        assert manifest["row_counts"] == {"signal_alert_outcomes": 1}
        assert isinstance(manifest["source_bytes"], int)
        assert isinstance(manifest["snapshot_bytes"], int)
        assert set(manifest) == {
            "captured_at_utc",
            "method",
            "source",
            "source_bytes",
            "snapshot_bytes",
            "git_commit",
            "duckdb",
            "row_counts",
        }
