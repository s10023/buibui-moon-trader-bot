"""Tests for `tools.memory_dir` — the one resolver for the memory tree's location.

The defect it exists to prevent is not a crash. After the 2026-09-18 Windows
migration the path was hardcoded at four sites (three in `daily_check.py`, one in
the `Makefile`) to a Linux root and a Linux slug, so every leg that read it
reported "not on this machine" while the tree sat in `~/.claude/projects/`. Five
legs of the operator's daily integrity check — memory index, audit verdicts,
unfinalised SoT rows, stale SoT rows and memory wikilinks — went silently inert,
which is a SKIP wearing the face of a PASS.

So the property under test is BOTH directions: it must find a tree under either
layout, and it must still fail to find one when there genuinely is none.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tools.memory_dir import CONFIG_ROOTS, LEGACY_SLUG, memory_dir, slug_for

WINDOWS_REPO = Path(r"C:\Users\User\repo\buibui-moon-trader-bot")
LINUX_REPO = Path("/home/kng/repo/buibui-moon-trader-bot")


def _plant(home: Path, config_root: str, slug: str) -> Path:
    """Create a memory tree at `home/config_root/projects/slug/memory`."""
    tree = home / config_root / "projects" / slug / "memory"
    tree.mkdir(parents=True)
    return tree


class TestSlug:
    """The slug is derived from the repo's ABSOLUTE PATH, not from the OS."""

    def test_linux_path_reproduces_the_legacy_slug(self) -> None:
        assert slug_for(LINUX_REPO) == LEGACY_SLUG

    def test_windows_path_reproduces_the_observed_slug(self) -> None:
        """The spelling the harness actually used on the migrated laptop."""
        assert slug_for(WINDOWS_REPO) == "C--Users-User-repo-buibui-moon-trader-bot"

    def test_the_slug_tracks_the_checkout_location(self) -> None:
        """Two checkouts of one repo get different slugs -- which is why the slug
        cannot be a constant, and why a worktree resolves somewhere else."""
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


class TestItCanStillSayNo:
    """Teeth. A resolver that always returns something usable is worse than none."""

    def test_an_empty_home_resolves_to_a_path_that_does_not_exist(
        self, tmp_path: Path
    ) -> None:
        """Callers guard with `.exists()`, so the fallback must NOT exist.

        Without this the four call sites would report a healthy memory index on a
        machine with no memory tree at all -- the false-green this module was
        written to remove, arriving from the other side.
        """
        resolved = memory_dir(WINDOWS_REPO, home=tmp_path)

        assert not resolved.exists()
        assert LEGACY_SLUG in resolved.parts

    def test_a_tree_at_an_unrelated_slug_is_not_adopted(self, tmp_path: Path) -> None:
        """Scoping: another project's memory must never answer for this one."""
        _plant(tmp_path, ".claude", "C--Users-User-repo-some-other-project")

        resolved = memory_dir(WINDOWS_REPO, home=tmp_path)

        assert not resolved.exists(), (
            "resolved to another project's memory tree, which would hand this "
            "repo's checks another repo's rulings"
        )


class TestBareInvocation:
    """`memory_dir.py` now imports `tools.claude_home`, so the bare form needs the
    bootstrap. `make status` sets PYTHONPATH and cannot catch a missing one; this test
    is the guarantee, not the bootstrap line (ST129). The printed path is pinned to
    the in-process answer so the CLI output cannot drift."""

    def test_bare_invocation_works(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["CLAUDE_CONFIG_DIR"] = str(tmp_path / "cfg")
        repo_root = Path(__file__).resolve().parent.parent
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "tools/memory_dir.py"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

        assert proc.returncode == 0, proc.stderr
        assert (
            proc.stdout.strip()
            == (tmp_path / "cfg" / "projects" / LEGACY_SLUG / "memory").as_posix()
        )
