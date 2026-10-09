"""Tests for `tools.memory_dir` — the one resolver for the memory tree's location.

The defect it exists to prevent is not a crash. After the 2026-09-18 Windows
migration the path was hardcoded at four sites (three in `daily_check.py`, one in
the `Makefile`) to a Linux root and a Linux slug, so every leg that read it
reported "not on this machine" while the tree sat in `~/.claude/projects/`. Five
legs of the operator's daily integrity check — memory index, audit verdicts,
unfinalised SoT rows, stale SoT rows and memory wikilinks — went silently inert,
which is a SKIP wearing the face of a PASS.

The same shape came back from a worktree: the resolver slugged the worktree's own
path, found nothing, and RETURNED the legacy `~/.claude-personal/.../-home-kng-...`
fallback — a path absent on the laptop, printed as the answer.

So the property under test is BOTH directions: it must find a tree under either
layout and from any checkout of the repo, and it must RAISE when there is none.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

from tools.claude_home import (
    main_checkout_from_common_dir,
    project_slug,
    slugify_path,
)
from tools.memory_dir import (
    CONFIG_ROOTS,
    LEGACY_SLUG,
    MemoryDirNotFoundError,
    memory_dir,
    slug_for,
)

WINDOWS_REPO = Path(r"C:\Users\User\repo\buibui-moon-trader-bot")
LINUX_REPO = Path("/home/kng/repo/buibui-moon-trader-bot")
REPO_ROOT = Path(__file__).resolve().parents[1]


def _plant(home: Path, config_root: str, slug: str) -> Path:
    """Create a memory tree at `home/config_root/projects/slug/memory`."""
    tree = home / config_root / "projects" / slug / "memory"
    tree.mkdir(parents=True)
    return tree


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _repo_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A real main checkout plus a linked worktree laid out the way the harness
    lays one out: under the main checkout's own `.claude/worktrees/`. Names are
    short because a slug of a pytest tmp path, nested under another tmp path,
    otherwise passes Windows' 260-character limit."""
    main = tmp_path / "m"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "t")
    (main / "committed.txt").write_text("committed\n", encoding="utf-8")
    _git(main, "add", "committed.txt")
    _git(main, "commit", "-q", "-m", "initial")
    worktree = main / ".claude" / "worktrees" / "w"
    _git(main, "worktree", "add", "-q", "-b", "wt", str(worktree))
    return main.resolve(), worktree.resolve()


class TestSlug:
    """The slug is derived from the repo's ABSOLUTE PATH, not from the OS."""

    def test_linux_path_reproduces_the_legacy_slug(self) -> None:
        assert slug_for(LINUX_REPO) == LEGACY_SLUG

    def test_windows_path_reproduces_the_observed_slug(self) -> None:
        """The spelling the harness actually used on the migrated laptop."""
        assert slug_for(WINDOWS_REPO) == "C--Users-User-repo-buibui-moon-trader-bot"

    def test_the_slug_tracks_the_checkout_location(self) -> None:
        """Two checkouts of one repo get different slugs -- which is why the slug
        cannot be a constant, and why a worktree must be mapped to its owner."""
        a = slug_for(Path("/home/kng/repo/buibui-moon-trader-bot"))
        b = slug_for(Path("/home/kng/worktrees/buibui-moon-trader-bot"))
        assert a != b


class TestResolution:
    def test_finds_the_tree_under_the_windows_config_root(self, tmp_path: Path) -> None:
        """The case that was broken: new root AND new slug at the same time."""
        planted = _plant(tmp_path, ".claude", slug_for(WINDOWS_REPO))

        assert memory_dir(WINDOWS_REPO, home=tmp_path) == planted

    def test_finds_the_tree_under_the_legacy_config_root(self, tmp_path: Path) -> None:
        planted = _plant(tmp_path, ".claude-personal", LEGACY_SLUG)

        assert memory_dir(LINUX_REPO, home=tmp_path) == planted

    def test_a_restored_legacy_tree_still_resolves_on_a_new_host(
        self, tmp_path: Path
    ) -> None:
        """A snapshot restore brings back the LEGACY slug. It must keep reading.

        Every backup on Drive carries the Linux-slug tree, so a resolver that only
        understood the derived slug would go quiet on exactly the machine someone
        restored onto.
        """
        planted = _plant(tmp_path, ".claude-personal", LEGACY_SLUG)

        assert memory_dir(WINDOWS_REPO, home=tmp_path) == planted

    def test_the_legacy_root_wins_when_both_exist(self, tmp_path: Path) -> None:
        """A host holding both must not be quietly repointed at the second tree."""
        legacy = _plant(tmp_path, ".claude-personal", LEGACY_SLUG)
        _plant(tmp_path, ".claude", slug_for(WINDOWS_REPO))

        assert memory_dir(WINDOWS_REPO, home=tmp_path) == legacy
        assert CONFIG_ROOTS[0] == ".claude-personal", "order is the contract here"


class TestLinkedWorktree:
    """The harness keys memory on the MAIN checkout, so a worktree must resolve to
    its owner's tree -- the case that printed `.claude-personal/.../-home-kng-...`."""

    def test_a_worktree_resolves_to_the_main_checkouts_tree(
        self, tmp_path: Path
    ) -> None:
        main, worktree = _repo_with_worktree(tmp_path)
        planted = _plant(tmp_path / "home", ".claude", slugify_path(str(main)))

        assert memory_dir(worktree, home=tmp_path / "home") == planted
        assert memory_dir(main, home=tmp_path / "home") == planted

    def test_a_tree_at_the_worktrees_own_slug_is_not_adopted(
        self, tmp_path: Path
    ) -> None:
        """Teeth for the mapping: the worktree's own slug holds the harness's
        TRANSCRIPTS for that session, never the memory tree. Resolving there is
        the pre-fix behaviour and must fail rather than pass."""
        _main, worktree = _repo_with_worktree(tmp_path)
        _plant(tmp_path / "home", ".claude", slugify_path(str(worktree)))

        with pytest.raises(MemoryDirNotFoundError):
            memory_dir(worktree, home=tmp_path / "home")

    def test_a_windows_worktree_maps_to_the_observed_main_slug(self) -> None:
        """The exact paths from the laptop, checkable from Linux CI.

        git 2.28 (the laptop's) answers `--git-common-dir` absolutely, with forward
        slashes, from a linked worktree; the slug must be the main checkout's.
        """
        worktree = PureWindowsPath(
            r"C:\Users\User\repo\buibui-moon-trader-bot\.claude\worktrees"
            r"\musing-haslett-9b032a"
        )
        main = main_checkout_from_common_dir(
            worktree, "C:/Users/User/repo/buibui-moon-trader-bot/.git"
        )

        assert slugify_path(str(main)) == "C--Users-User-repo-buibui-moon-trader-bot"

    def test_a_windows_main_checkouts_relative_answer_maps_to_itself(self) -> None:
        """In the main checkout git answers RELATIVE (`.git`) -- measured on the
        laptop, where `--path-format=absolute` is too new and is echoed back."""
        main = main_checkout_from_common_dir(PureWindowsPath(str(WINDOWS_REPO)), ".git")

        assert slugify_path(str(main)) == "C--Users-User-repo-buibui-moon-trader-bot"


class TestItRaisesWhenThereIsNone:
    """Teeth. A resolver that always returns something usable is worse than none."""

    def test_an_empty_home_raises_and_names_every_path_tried(
        self, tmp_path: Path
    ) -> None:
        """The pre-fix fallback RETURNED the legacy path; `make status` and every
        consumer then read a tree that does not exist as an empty one."""
        with pytest.raises(MemoryDirNotFoundError) as excinfo:
            memory_dir(WINDOWS_REPO, home=tmp_path)

        tried = excinfo.value.tried
        assert len(tried) == 2 * len(CONFIG_ROOTS)
        assert not any(p.exists() for p in tried)
        assert all(p.as_posix() in str(excinfo.value) for p in tried)
        assert isinstance(excinfo.value, FileNotFoundError)

    def test_a_tree_at_an_unrelated_slug_is_not_adopted(self, tmp_path: Path) -> None:
        """Scoping: another project's memory must never answer for this one."""
        _plant(tmp_path, ".claude", "C--Users-User-repo-some-other-project")

        with pytest.raises(MemoryDirNotFoundError):
            memory_dir(WINDOWS_REPO, home=tmp_path)


class TestBareInvocation:
    """`memory_dir.py` imports `tools.claude_home`, so the bare form needs the
    bootstrap. `make status` sets PYTHONPATH and cannot catch a missing one; this test
    is the guarantee, not the bootstrap line (ST129). The printed path is pinned to
    the in-process answer so the CLI output cannot drift."""

    def _run(self, config_dir: Path) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        return subprocess.run(  # noqa: S603
            [sys.executable, "tools/memory_dir.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_bare_invocation_prints_the_resolved_tree(self, tmp_path: Path) -> None:
        cfg = tmp_path / "cfg"
        planted = cfg / "projects" / project_slug(REPO_ROOT) / "memory"
        planted.mkdir(parents=True)

        proc = self._run(cfg)

        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == planted.as_posix()

    def test_bare_invocation_exits_nonzero_and_prints_nothing_on_a_miss(
        self, tmp_path: Path
    ) -> None:
        """Nothing on stdout: a `$(...)` consumer must not receive a path to use."""
        proc = self._run(tmp_path / "cfg")

        assert proc.returncode == 1
        assert proc.stdout == ""
        assert "no memory tree found" in proc.stderr
