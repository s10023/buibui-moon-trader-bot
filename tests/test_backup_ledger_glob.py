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
CLAUDE_HOME_SRC = Path(__file__).resolve().parents[1] / "tools" / "claude_home.py"


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

    # The script resolves the Claude config root through `tools/claude_home.py` (#838)
    # and imports it from the checkout it runs in, which here is this fixture.
    (repo / "tools").mkdir()
    shutil.copy(CLAUDE_HOME_SRC, repo / "tools" / "claude_home.py")

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
    # The host's own override must not steer the script under test; a test that wants
    # one passes it through `env_extra`.
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.update(env_extra or {})
    # Every EXTERNAL_* path is spelled relative to $HOME, so overriding it is what
    # isolates these tests from the operator's real ~/.claude-personal tree.
    if home is not None:
        env["HOME"] = str(home)
        # The resolver runs `Path.home()`, which reads USERPROFILE on Windows and
        # ignores HOME: without this the fixture escaped to the real ~/.claude.
        env["USERPROFILE"] = str(home)
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


def _fixture_slug(tmp_path: Path) -> str:
    """The project slug the script will derive for the `fake_repo` fixture."""
    from tools.claude_home import project_slug

    return project_slug(tmp_path / "repo")


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """A $HOME whose `.claude-personal/tools/` holds a stub tracker.

    The root also holds the fixture repo's `projects/<slug>` dir: the script picks the
    account root by the project tree it holds, never by the root merely existing.
    """
    tools = tmp_path / "home" / ".claude-personal" / "tools"
    tools.mkdir(parents=True)
    (
        tmp_path / "home" / ".claude-personal" / "projects" / _fixture_slug(tmp_path)
    ).mkdir(parents=True)
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
        # The per-root memory glob is added by a loop over every candidate root (#838);
        # restricting the loop to the first root is the old "covers only one" defect.
        entry = 'for _root in "${CLAUDE_ROOTS[@]}"; do\n'
        assert entry in text, "the guarded loop is not present to mutate"
        script.write_text(
            text.replace(entry, 'for _root in "${CLAUDE_ROOTS[0]}"; do\n'),
            encoding="utf-8",
        )

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


def _code_lines_naming_a_config_root(script_text: str) -> list[str]:
    """Non-comment lines of the script that spell a profile directory as a literal."""
    return [
        line
        for line in script_text.splitlines()
        if not line.lstrip().startswith("#") and "claude-personal" in line
    ]


def _windows_style_home(tmp_path: Path, repo_slug: str) -> Path:
    """A $HOME as the migrated laptop has it: ONLY `~/.claude`, holding the live tree.

    Also plants the two files that must NEVER be copied (`.credentials.json` inside
    the root, `.claude.json` beside it) so one fixture serves the exclusion test.
    """
    home = tmp_path / "home"
    root = home / ".claude"
    (root / "projects" / repo_slug / "memory").mkdir(parents=True)
    (root / "projects" / repo_slug / "memory" / "MEMORY.md").write_text(
        "live\n", encoding="utf-8"
    )
    (root / "history.jsonl").write_text('{"p": 1}\n', encoding="utf-8")
    (root / "CLAUDE.md").write_text("account rules\n", encoding="utf-8")
    (root / "settings.json").write_text("{}\n", encoding="utf-8")
    (root / "tools").mkdir()
    (root / "tools" / "budget-history.json").write_text("{}\n", encoding="utf-8")
    (root / "skills" / "s").mkdir(parents=True)
    (root / "skills" / "s" / "SKILL.md").write_text("skill\n", encoding="utf-8")
    (root / "commands").mkdir()
    (root / "commands" / "c.md").write_text("cmd\n", encoding="utf-8")
    (root / ".credentials.json").write_text('{"token": "SECRET"}\n', encoding="utf-8")
    (home / ".claude.json").write_text('{"mcp": "SECRET"}\n', encoding="utf-8")
    return home


class TestAccountRootIsResolvedNotHardcoded:
    """#838: every account-level entry followed a root the script NAMED.

    After the 2026-09-18 move to `~/.claude` the `[ -f ]` / glob-miss guards turned
    each of `history.jsonl`, `CLAUDE.md`, `settings.json`, `tools/`, `skills/` and
    `commands/` into a silent skip, and the legacy trees still matching kept the
    snapshot looking populated. The roots now come from `tools/claude_home.py`.
    """

    def test_account_entries_follow_the_root_holding_this_projects_tree(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        home = _windows_style_home(tmp_path, _fixture_slug(tmp_path))

        r = _run(fake_repo, tmp_path, home=home)

        assert r.returncode == 0, r.stdout + r.stderr
        snap = sorted((tmp_path / "backups" / "daily").iterdir())[-1] / "_external"
        assert (snap / "claude" / "history.jsonl").exists()
        assert (snap / "claude" / "CLAUDE.md").exists()
        assert (snap / "claude" / "settings.json").exists()
        assert (snap / "claude" / ".claude" / "tools" / "budget-history.json").exists()
        assert (snap / "claude" / ".claude" / "skills" / "s" / "SKILL.md").exists()
        assert (snap / "claude" / ".claude" / "commands" / "c.md").exists()
        slug = _fixture_slug(tmp_path)
        assert (snap / "claude" / "projects" / slug / "memory" / "MEMORY.md").exists()

    def test_credentials_are_never_copied(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """The exclusion is deliberate: the off-site leg syncs this root to a third
        party. Resolving the root dynamically must not widen what is copied."""
        home = _windows_style_home(tmp_path, _fixture_slug(tmp_path))

        r = _run(fake_repo, tmp_path, home=home)

        assert r.returncode == 0, r.stdout + r.stderr
        snap = sorted((tmp_path / "backups" / "daily").iterdir())[-1]
        names = {p.name for p in snap.rglob("*")}
        assert ".credentials.json" not in names
        assert ".claude.json" not in names
        assert "SECRET" not in "".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in (snap / "_external").rglob("*")
            if p.is_file()
        )

    def test_the_other_profile_is_not_adopted_for_account_files(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """Both profiles exist, only `.claude` holds this project. A work-account
        `CLAUDE.md` in `.claude-personal` (or the reverse) must not be copied just
        because that root sorts first or exists."""
        home = _windows_style_home(tmp_path, _fixture_slug(tmp_path))
        other = home / ".claude-personal"
        other.mkdir()
        (other / "CLAUDE.md").write_text("OTHER PROFILE\n", encoding="utf-8")

        r = _run(fake_repo, tmp_path, home=home)

        assert r.returncode == 0, r.stdout + r.stderr
        snap = sorted((tmp_path / "backups" / "daily").iterdir())[-1] / "_external"
        assert (snap / "claude" / "CLAUDE.md").read_text(
            encoding="utf-8"
        ) == "account rules\n"
        assert not (snap / "claude-personal" / "CLAUDE.md").exists()
        # ...and leaving it out is never silent.
        assert f"uncopied   {other.as_posix()}" in r.stdout

    def test_an_opted_in_root_is_copied_under_its_own_label(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """BUIBUI_BACKUP_EXTRA_CLAUDE_ROOTS keeps a legacy root's account files -- the
        laptop's pre-migration history.jsonl and book distillations live only there --
        without the active root's files being displaced."""
        home = _windows_style_home(tmp_path, _fixture_slug(tmp_path))
        other = home / ".claude-personal"
        (other / "skills" / "book").mkdir(parents=True)
        (other / "skills" / "book" / "SKILL.md").write_text("ch1\n", encoding="utf-8")
        (other / "history.jsonl").write_text('{"old": 1}\n', encoding="utf-8")

        r = _run(
            fake_repo,
            tmp_path,
            home=home,
            env_extra={"BUIBUI_BACKUP_EXTRA_CLAUDE_ROOTS": f" {other} ,"},
        )

        assert r.returncode == 0, r.stdout + r.stderr
        snap = sorted((tmp_path / "backups" / "daily").iterdir())[-1] / "_external"
        assert (snap / "claude-personal" / "history.jsonl").read_text(
            encoding="utf-8"
        ) == '{"old": 1}\n'
        legacy_skill = snap / "claude-personal" / ".claude-personal" / "skills"
        assert (legacy_skill / "book" / "SKILL.md").exists()
        assert (snap / "claude" / "CLAUDE.md").read_text(
            encoding="utf-8"
        ) == "account rules\n"
        assert "uncopied" not in r.stdout

    def test_budget_py_is_found_in_an_opted_in_root(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """The tracker stayed in the legacy root on the laptop; a lookup pinned to the
        active root reported it ABSENT and stopped refreshing the index."""
        home = _windows_style_home(tmp_path, _fixture_slug(tmp_path))
        other_tools = home / ".claude-personal" / "tools"
        other_tools.mkdir(parents=True)
        (other_tools / "budget.py").write_text(STUB_BUDGET, encoding="utf-8")

        r = _run(
            fake_repo,
            tmp_path,
            "--dry-run",
            home=home,
            env_extra={
                "BUIBUI_BACKUP_EXTRA_CLAUDE_ROOTS": str(home / ".claude-personal")
            },
        )

        assert r.returncode == 0, r.stdout + r.stderr
        line = next(ln for ln in r.stdout.splitlines() if "spend-idx" in ln)
        assert "ABSENT" not in line, line
        assert ".claude-personal" in line, line

    def test_an_opted_in_root_that_is_missing_warns(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        home = _windows_style_home(tmp_path, _fixture_slug(tmp_path))

        r = _run(
            fake_repo,
            tmp_path,
            home=home,
            env_extra={"BUIBUI_BACKUP_EXTRA_CLAUDE_ROOTS": str(tmp_path / "nope")},
        )

        assert r.returncode == 0, r.stdout + r.stderr
        assert "BUIBUI_BACKUP_EXTRA_CLAUDE_ROOTS names" in r.stderr

    def test_the_legacy_layout_keeps_its_destination_names(
        self, fake_repo: Path, tmp_path: Path, fake_home: Path
    ) -> None:
        """Existing snapshots line up: `~/.claude-personal` still lands under
        `_external/claude-personal/`."""
        (fake_home / ".claude-personal" / "history.jsonl").write_text(
            '{"p": 1}\n', encoding="utf-8"
        )

        r = _run(fake_repo, tmp_path, home=fake_home)

        assert r.returncode == 0, r.stdout + r.stderr
        snap = sorted((tmp_path / "backups" / "daily").iterdir())[-1] / "_external"
        assert (snap / "claude-personal" / "history.jsonl").exists()

    def test_claude_config_dir_overrides_the_probe(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """An explicit setting collapses the candidates, as it does for Claude Code."""
        custom = tmp_path / "elsewhere" / "cfg"
        (custom / "projects" / _fixture_slug(tmp_path) / "memory").mkdir(parents=True)
        (custom / "history.jsonl").write_text('{"p": 1}\n', encoding="utf-8")
        bare_home = tmp_path / "bare-home"
        bare_home.mkdir()

        r = _run(
            fake_repo,
            tmp_path,
            home=bare_home,
            env_extra={"CLAUDE_CONFIG_DIR": str(custom)},
        )

        assert r.returncode == 0, r.stdout + r.stderr
        snap = sorted((tmp_path / "backups" / "daily").iterdir())[-1] / "_external"
        assert (snap / "cfg" / "history.jsonl").exists()

    def test_the_dry_run_reports_the_resolved_root(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """Both loops must resolve alike: the report may not promise a root the copy
        does not read."""
        home = _windows_style_home(tmp_path, _fixture_slug(tmp_path))

        r = _run(fake_repo, tmp_path, "--dry-run", home=home)

        assert r.returncode == 0, r.stdout + r.stderr
        assert (
            f"external   claude/history.jsonl <- {(home / '.claude').as_posix()}"
            in r.stdout
        )
        assert "ABSENT" not in "".join(
            ln for ln in r.stdout.splitlines() if "history.jsonl" in ln
        )

    def test_an_unimportable_resolver_warns_and_still_backs_up(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """Never silent: with `tools/claude_home.py` gone the script says so on stderr
        and falls back to `$CLAUDE_CONFIG_DIR` / `~/.claude` rather than skipping."""
        (fake_repo / "tools" / "claude_home.py").unlink()
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "history.jsonl").write_text('{"p": 1}\n', encoding="utf-8")

        r = _run(fake_repo, tmp_path, home=home)

        assert r.returncode == 0, r.stdout + r.stderr
        assert "could not import tools.claude_home" in r.stderr
        snap = sorted((tmp_path / "backups" / "daily").iterdir())[-1] / "_external"
        assert (snap / "claude" / "history.jsonl").exists()

    def test_the_script_names_no_config_root_literal(self) -> None:
        """The structural guard: a hardcoded profile directory is the defect.

        Comments may discuss `.claude-personal`; no executable line may spell it.
        """
        offenders = _code_lines_naming_a_config_root(SCRIPT.read_text(encoding="utf-8"))
        assert offenders == [], offenders

    def test_mutation_the_old_hardcoded_entry_is_caught_both_ways(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        """Proves the two guards above bite: re-introduce the pre-#838 line and (a) the
        literal scan flags it, (b) on a `~/.claude`-only host the history file is
        silently not copied, which is the defect as it presented."""
        script = fake_repo / "deploy" / "backup-analytics.sh"
        text = script.read_text(encoding="utf-8")
        resolved = '        "$_root/history.jsonl:$_label/history.jsonl"\n'
        assert resolved in text, "the entry to mutate is not present"
        old = '        "$HOME/.claude-personal/history.jsonl:claude-personal/history.jsonl"\n'
        mutated = text.replace(resolved, old)
        script.write_text(mutated, encoding="utf-8")

        assert _code_lines_naming_a_config_root(mutated), "the literal scan is blind"

        home = _windows_style_home(tmp_path, _fixture_slug(tmp_path))
        r = _run(fake_repo, tmp_path, home=home)
        assert r.returncode == 0, r.stdout + r.stderr
        snap = sorted((tmp_path / "backups" / "daily").iterdir())[-1] / "_external"
        assert not (snap / "claude" / "history.jsonl").exists()
        assert not (snap / "claude-personal" / "history.jsonl").exists(), (
            "the mutated script copied history.jsonl from a host that has no "
            "`.claude-personal`; the behavioural assertion above would guard nothing"
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
