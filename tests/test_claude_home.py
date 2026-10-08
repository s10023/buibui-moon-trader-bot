r"""The shared Claude-project-path derivation.

Several call sites had their own copy of this rule and drifted; the backup script
carried the old Linux box's `~/.claude-personal` as a tracked literal (#838). The class is worth a dedicated test file because of HOW it failed:
never by raising. Every consumer treats an unresolvable memory tree as "absent,
skip with a warning", so a wrong derivation reads as *nothing to back up* and
*nothing to check*, and a green suite says so too.

The platform rules are pinned as text, both of them, on every host.
`slugify_path` takes a `str` rather than a `Path` precisely so that Linux CI
asserts the Windows rule and a Windows box asserts the POSIX one. Had it taken
a `Path`, exactly one of the two assertions would be unwritable on any given
host — and the one you cannot write is the one that breaks.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.claude_home import (
    CONFIG_DIR_NAMES,
    claude_home,
    config_roots,
    memory_dir,
    project_dir,
    project_slug,
    slugify_path,
)


class TestSlugifyPath:
    """The rule itself, as text, independent of the running host."""

    def test_posix_absolute_path(self) -> None:
        assert (
            slugify_path("/home/kng/repo/buibui-moon-trader-bot")
            == "-home-kng-repo-buibui-moon-trader-bot"
        )

    def test_windows_absolute_path(self) -> None:
        """The drive colon folds too, which is why `C:` becomes `C-`.

        A rule folding separators alone leaves `C:\\Users\\...` untouched and
        yields a "slug" that is still a drive-absolute path — which then joins
        onto `projects/` as a second root and silently discards it. That was
        the live defect, and it is the reason this asserts the doubled `-`.
        """
        assert (
            slugify_path(r"C:\Users\User\repo\buibui-moon-trader-bot")
            == "C--Users-User-repo-buibui-moon-trader-bot"
        )

    def test_the_doubled_separator_is_the_drive_colon(self) -> None:
        """A negative control for the assertion above.

        `C--Users` could be read as an accident of some other rule, so pin that
        it is exactly the colon and the following separator, and that a path
        with no drive letter does not acquire one.
        """
        assert slugify_path("C:") == "C-"
        assert slugify_path(r"\Users\User") == "-Users-User"

    def test_mixed_separators_fold_the_same_way(self) -> None:
        """Windows accepts both, and `Path` normalises inconsistently enough
        that a real `resolve()` can hand back either."""
        assert slugify_path("C:/Users/User") == slugify_path(r"C:\Users\User")

    def test_a_relative_path_is_not_special_cased(self) -> None:
        """Documents the boundary: the rule is total over text. Callers that
        need an absolute path resolve first — that is `project_slug`'s job, and
        keeping it out of here is what makes this function testable."""
        assert slugify_path("repo/x") == "repo-x"


REPO = Path("/srv/demo")


def _make_tree(home: Path, profile: str, repo: Path = REPO) -> Path:
    """Create `<home>/<profile>/projects/<slug>` and return it."""
    tree = home / profile / "projects" / slugify_path(str(repo.resolve()))
    tree.mkdir(parents=True)
    return tree


class TestConfigRootSelection:
    """Which config root wins, and on what evidence.

    The discriminator is `projects/<slug>`, never the root's existence.
    A probe of the root fails: `.claude-personal` can appear on a box while both
    profiles are in use, the probe takes it, and every consumer reads ABSENT
    against a tree that is present under `.claude`.
    """

    def test_env_override_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
        assert claude_home(REPO) == tmp_path / "elsewhere"

    def test_env_override_wins_even_when_a_candidate_holds_the_tree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ordering control: evidence must not outrank an explicit setting."""
        _set_home(tmp_path, monkeypatch)
        _make_tree(tmp_path, ".claude")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
        assert claude_home(REPO) == tmp_path / "elsewhere"

    def test_the_root_holding_the_tree_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_home(tmp_path, monkeypatch)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        _make_tree(tmp_path, ".claude-personal")
        assert claude_home(REPO) == tmp_path / ".claude-personal"

    def test_REGRESSION_an_empty_personal_root_does_not_win(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact state that broke it: BOTH roots exist, only one has the tree.

        `.claude-personal` is more specific and sorts first, so an
        existence-of-root probe takes it and resolves to a directory holding
        nothing. Root existence is a PROXY; the project directory is the thing
        actually wanted.
        """
        (tmp_path / ".claude-personal").mkdir()
        _set_home(tmp_path, monkeypatch)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        tree = _make_tree(tmp_path, ".claude")

        assert claude_home(REPO) == tmp_path / ".claude"
        assert project_dir(REPO) == tree
        assert project_dir(REPO).is_dir()

    def test_personal_still_wins_when_BOTH_hold_a_tree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Order is the tie-break, and only the tie-break.

        With evidence on both sides the more specific profile wins, which is
        the original intent — narrowed to the case where it is actually a
        choice rather than a guess.
        """
        _set_home(tmp_path, monkeypatch)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        _make_tree(tmp_path, ".claude-personal")
        _make_tree(tmp_path, ".claude")
        assert claude_home(REPO) == tmp_path / ".claude-personal"

    def test_no_tree_anywhere_falls_back_rather_than_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deliberate: every consumer degrades to a printed note on an absent
        tree, so raising here would convert an advisory check into a blocking
        one — and would break the backup rather than the report."""
        _set_home(tmp_path, monkeypatch)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        assert claude_home(REPO) == tmp_path / ".claude"

    def test_a_candidate_that_is_a_FILE_does_not_win(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`~/.claude.json` sits beside `~/.claude` on a real box, so the probe
        has to test for a directory rather than for existence."""
        (tmp_path / ".claude-personal").write_text("not a dir\n", encoding="utf-8")
        _set_home(tmp_path, monkeypatch)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        _make_tree(tmp_path, ".claude")
        assert claude_home(REPO) == tmp_path / ".claude"


class TestDerivedPaths:
    """`project_dir` and `memory_dir` compose the two rules above."""

    def test_memory_dir_sits_under_the_project_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_home(tmp_path, monkeypatch)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
        repo = tmp_path / "repo" / "demo"
        repo.mkdir(parents=True)
        assert memory_dir(repo) == project_dir(repo) / "memory"
        assert project_dir(repo).parent == tmp_path / ".claude" / "projects"
        assert project_dir(repo).name == project_slug(repo)

    def test_claude_home_never_disagrees_with_project_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """They are one decision, so derive one from the other.

        Computing the root separately is how the two would drift — which is the
        whole shape of the defect this module exists to remove.
        """
        (tmp_path / ".claude-personal").mkdir()
        _set_home(tmp_path, monkeypatch)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        _make_tree(tmp_path, ".claude")
        assert project_dir(REPO).parent.parent == claude_home(REPO)

    def test_project_slug_resolves_before_folding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A relative path must not produce a relative slug — the whole point
        of the derivation is that two checkouts at different paths get
        different project directories."""
        repo = tmp_path / "repo" / "demo"
        repo.mkdir(parents=True)
        monkeypatch.chdir(repo)
        assert project_slug(Path(".")) == project_slug(repo)
        assert project_slug(Path(".")) == slugify_path(str(repo.resolve()))


class TestSlugRuleBreadth:
    """The slug folds EVERY non-alphanumeric, not just separators and the colon.

    The sibling fork folds `[/\\:]` only. A checkout path with a dot, underscore
    or space (a worktree under `.claude/worktrees/`, say) would then slug to a name
    the harness never wrote, and the resolver would read ABSENT.
    """

    def test_dot_underscore_and_space_fold(self) -> None:
        assert (
            slugify_path("/home/u/.claude/worktrees/agent_1 x")
            == "-home-u--claude-worktrees-agent-1-x"
        )

    def test_matches_memory_dir_slug_for(self) -> None:
        """`tools.memory_dir.slug_for` is the Path-typed spelling of this rule."""
        from tools.memory_dir import slug_for

        path = Path("/home/kng/repo/buibui_moon.trader")
        assert slug_for(path) == slugify_path(str(path))


class TestConfigRoots:
    """`config_roots` is the single source of the candidate list."""

    def test_default_candidates_are_the_two_profiles_in_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_home(tmp_path, monkeypatch)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        assert config_roots() == (
            tmp_path / ".claude-personal",
            tmp_path / ".claude",
        )
        assert CONFIG_DIR_NAMES == (".claude-personal", ".claude")

    def test_env_collapses_the_candidates_to_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_home(tmp_path, monkeypatch)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
        assert config_roots() == (tmp_path / "elsewhere",)

    def test_an_injected_home_wins_over_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The seam `tools.memory_dir` uses to stay hermetic under any host env."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
        assert config_roots(tmp_path / "h") == (
            tmp_path / "h" / ".claude-personal",
            tmp_path / "h" / ".claude",
        )


class TestMemoryDirAgrees:
    """`tools.memory_dir` and this module must never be two resolvers."""

    def test_memory_dir_honours_claude_config_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tools.memory_dir import memory_dir as legacy_memory_dir

        repo = (
            tmp_path / "srv" / "demo"
        )  # absolute on every host; /srv/demo has no drive on Windows
        elsewhere = tmp_path / "elsewhere"
        planted = elsewhere / "projects" / slugify_path(str(repo)) / "memory"
        planted.mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(elsewhere))
        assert legacy_memory_dir(repo) == planted
        assert legacy_memory_dir(repo) == memory_dir(repo)

    def test_env_set_and_tree_only_at_the_env_root_still_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Teeth: a resolver probing the two hardcoded profiles instead of
        `config_roots` would answer a non-existent path here."""
        from tools.memory_dir import memory_dir as legacy_memory_dir

        _set_home(tmp_path, monkeypatch)
        repo = (
            tmp_path / "srv" / "demo"
        )  # absolute on every host; /srv/demo has no drive on Windows
        elsewhere = tmp_path / "elsewhere"
        (elsewhere / "projects" / slugify_path(str(repo)) / "memory").mkdir(
            parents=True
        )
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(elsewhere))
        assert legacy_memory_dir(repo).is_dir()


class TestBareInvocation:
    """`claude_home` imports nothing from the repo, so the bare form must work.

    The backup script imports it by inserting the repo root on `sys.path`; this pins
    that the module has no other repo dependency to break that.
    """

    def test_bare_invocation_works(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        proc = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'tools'); import claude_home; "
                "print(claude_home.slugify_path('/a/b'))",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "-a-b"


def _set_home(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point `Path.home()` at `path` on either host.

    POSIX reads HOME; Windows reads USERPROFILE and ignores HOME entirely. A
    helper exists so no test here can set one and quietly leak the real home
    into an assertion on the other platform.
    """
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))
