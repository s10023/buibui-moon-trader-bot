"""#1029: the doc-drift walks skip `.claude/worktrees`, and nothing else.

A stale desktop-app worktree is a whole checkout under `.claude/worktrees/`, so a
walk that enters it reports another branch's files as findings here, red on the
laptop and green in CI, which never has the directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tools import post_branch_checks, sanity_checks
from tools.repo_walk import md_files


@pytest.fixture
def claude_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `.claude` with one doc of its own and one inside a stale worktree."""
    claude = tmp_path / ".claude"
    (claude / "skills" / "s").mkdir(parents=True)
    (claude / "skills" / "s" / "SKILL.md").write_text("own\n", encoding="utf-8")
    wt = claude / "worktrees" / "stale" / ".claude" / "skills" / "s"
    wt.mkdir(parents=True)
    (wt / "SKILL.md").write_text("another checkout\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return claude


def _posix(paths: list[Path]) -> list[str]:
    return [p.as_posix() for p in paths]


def test_md_files_skips_the_worktree_and_keeps_the_rest(claude_tree: Path) -> None:
    assert _posix(md_files(Path(".claude"))) == [".claude/skills/s/SKILL.md"]


def test_a_worktrees_dir_outside_claude_is_still_walked(tmp_path: Path) -> None:
    """Scoped to `.claude/worktrees`: a `worktrees` folder anywhere else is content."""
    (tmp_path / "docs" / "worktrees").mkdir(parents=True)
    (tmp_path / "docs" / "worktrees" / "note.md").write_text("x\n", encoding="utf-8")

    assert [p.name for p in md_files(tmp_path)] == ["note.md"]


def test_the_sanity_scan_does_not_read_the_worktree(
    claude_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sanity_checks, "sanity_surfaces", lambda: ())

    assert _posix(sanity_checks.surface_paths()) == [".claude/skills/s/SKILL.md"]


def test_the_stale_anchors_leg_does_not_read_the_worktree(
    claude_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []

    def fake_scan(sources: list[Path], resolve: Any, **_: Any) -> list[Any]:
        seen.extend(sources)
        return []

    monkeypatch.setattr(post_branch_checks, "scan", fake_scan)
    monkeypatch.setattr(post_branch_checks, "anchor_files", lambda: ())
    monkeypatch.setattr(post_branch_checks, "MEMORY_DIR", None)

    assert post_branch_checks._check_stale_anchors() == []
    assert _posix(seen) == [".claude/skills/s/SKILL.md"]
