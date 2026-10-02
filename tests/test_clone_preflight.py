"""Tests for `tools/clone_preflight.py` — the clean-clone pre-flight (ST45).

The gate exists because a *fresh clone* is the only mechanism that separates
"relative path to a committed asset" (fine) from "relative path to gitignored
operator data" (the defect). CI already is that clone, which is why it caught
#666; the gap this closes is TIMING, not detection.

The load-bearing test here is the *dirty tree* one. A clone only ever sees
committed state, so a pre-flight run against an uncommitted tree silently
tests stale code and reports GREEN — the same shape of invisible pass the
gate exists to kill. Refusing has to happen before any clone is taken, so the
test asserts on the absence of the clone rather than only on the exit code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.clone_preflight import (
    REFUSED,
    clone_argv,
    dirty_paths,
    main,
    seed_venv_argv,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _make_repo(tmp_path: Path) -> Path:
    """A real, minimal git repo — no mocks, the gate is all subprocess."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "committed.txt").write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "committed.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


class TestSeedVenv:
    """#871: the clone's venv is built on preflight's own interpreter.

    Poetry otherwise builds it on the python POETRY runs under, which on the
    cloud host is 3.11 against a project pinned to 3.13.
    """

    def test_inside_a_venv_it_seeds_from_this_interpreter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "prefix", "/proj/.venv")
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        assert seed_venv_argv() == [sys.executable, "-m", "venv", ".venv"]

    def test_a_bare_system_python_seeds_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No guarantee it is the right version, so leave poetry to choose."""
        monkeypatch.setattr(sys, "prefix", "/usr")
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        assert seed_venv_argv() is None


class TestCloneArgv:
    def test_passes_no_hardlinks(self, tmp_path: Path) -> None:
        """`git clone --local` fails `Invalid cross-device link` onto /tmp here."""
        argv = clone_argv(tmp_path / "src", tmp_path / "dest")
        assert "--no-hardlinks" in argv

    def test_clones_the_repo_into_the_destination(self, tmp_path: Path) -> None:
        argv = clone_argv(tmp_path / "src", tmp_path / "dest")
        assert argv[:2] == ["git", "clone"]
        assert argv[-2:] == [str(tmp_path / "src"), str(tmp_path / "dest")]


class TestDirtyPaths:
    def test_clean_tree_reports_nothing(self, tmp_path: Path) -> None:
        assert dirty_paths(_make_repo(tmp_path)) == []

    def test_modified_file_is_reported(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / "committed.txt").write_text("modified\n", encoding="utf-8")
        assert any("committed.txt" in line for line in dirty_paths(repo))

    def test_untracked_file_is_reported(self, tmp_path: Path) -> None:
        """An untracked new module is exactly the change a clone would miss."""
        repo = _make_repo(tmp_path)
        (repo / "brand_new.py").write_text("x = 1\n", encoding="utf-8")
        assert any("brand_new.py" in line for line in dirty_paths(repo))


class TestRefusesOnDirtyTree:
    def test_returns_the_refused_exit_code(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / "committed.txt").write_text("uncommitted edit\n", encoding="utf-8")
        clone_dir = tmp_path / "clone"
        assert (
            main(["--repo", str(repo), "--dest", str(clone_dir), "--dry-run"])
            == REFUSED
        )

    def test_refuses_before_taking_any_clone(self, tmp_path: Path) -> None:
        """A clone of a dirty tree would test stale HEAD and report green."""
        repo = _make_repo(tmp_path)
        (repo / "committed.txt").write_text("uncommitted edit\n", encoding="utf-8")
        clone_dir = tmp_path / "clone"
        main(["--repo", str(repo), "--dest", str(clone_dir), "--dry-run"])
        assert not clone_dir.exists()


class TestCleanTreeProceeds:
    def test_dry_run_succeeds(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        assert (
            main(["--repo", str(repo), "--dest", str(tmp_path / "clone"), "--dry-run"])
            == 0
        )

    def test_dry_run_really_clones(self, tmp_path: Path) -> None:
        """Dry-run stops before install+pytest, but proves the clone works."""
        repo = _make_repo(tmp_path)
        clone_dir = tmp_path / "clone"
        main(["--repo", str(repo), "--dest", str(clone_dir), "--dry-run"])
        assert (clone_dir / "committed.txt").exists()

    def test_clone_omits_gitignored_operator_data(self, tmp_path: Path) -> None:
        """The whole point: gitignored paths must be absent in the clone."""
        repo = _make_repo(tmp_path)
        (repo / ".gitignore").write_text("operator_only.json\n", encoding="utf-8")
        _git(repo, "add", ".gitignore")
        _git(repo, "commit", "-q", "-m", "ignore")
        (repo / "operator_only.json").write_text("{}\n", encoding="utf-8")
        clone_dir = tmp_path / "clone"
        assert main(["--repo", str(repo), "--dest", str(clone_dir), "--dry-run"]) == 0
        assert not (clone_dir / "operator_only.json").exists()


class TestWiredIntoTheWorkflow:
    """ST45's acceptance: mechanical, not prose. These pin the wiring."""

    def test_makefile_exposes_the_target(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        assert "preflight:" in makefile
        assert "tools/clone_preflight.py" in makefile

    def test_post_branch_step_7_names_the_gate(self) -> None:
        skill = (REPO_ROOT / ".claude/skills/post-branch/SKILL.md").read_text(
            encoding="utf-8"
        )
        assert "make preflight" in skill
