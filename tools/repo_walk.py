"""Markdown walks over `.claude` that do not descend into desktop-app worktrees.

`.claude/worktrees/<name>/` holds a whole checkout per worktree session, so a walk
that enters it reads another branch's files as this checkout's. CI never has the
directory, which made the doc-drift gate red on the laptop only (#1029). The
backup script's `LEDGER_DIR_EXCLUDES` drops the same subtree in bash (#1031).
"""

from __future__ import annotations

import os
from pathlib import Path


def is_worktrees_dir(path: Path) -> bool:
    """True for a `.claude/worktrees` directory, wherever the walk started."""
    return path.name == "worktrees" and path.parent.name == ".claude"


def md_files(root: Path) -> list[Path]:
    """Every `*.md` under `root`, sorted, pruning `.claude/worktrees` before entering it."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not is_worktrees_dir(here / d)]
        found += [here / f for f in filenames if f.endswith(".md")]
    return sorted(found)
