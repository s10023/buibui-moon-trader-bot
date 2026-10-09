"""Locate this repo's cross-session memory tree, wherever the harness put it.

TWO things vary by host, and BOTH were hardcoded at four sites -- three in
`docs/plans/daily_check.py` and one in the `Makefile`:

* the config ROOT -- ``~/.claude-personal`` on the Linux box, ``~/.claude`` on the
  Windows laptop;
* the project SLUG, which the harness derives from the repo's ABSOLUTE PATH, so it
  changes with the checkout location and not merely with the OS.

⚠ **The cost of getting this wrong is not a crash.** After the 2026-09-18 Windows
migration all five daily-check legs that read this tree printed "not on this
machine" while it sat in ``~/.claude/projects/`` -- a SKIP wearing the same face as
a PASS, which silently took the whole doc-drift and SoT-drift half of the operator's
daily integrity check offline. `sanity_checks.py` already documents that exact
failure shape; this is it arriving by a different route.

It lives in tracked ``tools/`` rather than in `daily_check.py` for the ST43 reason
the check itself states: that file is gitignored, so anything buried in it reaches
no reclone, no CI and not the wifey fork -- which has the same split and will need
the same resolver the day it migrates.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Runnable as a bare script (`python3 tools/memory_dir.py`): that puts `tools/` on
# sys.path rather than the repo root, so the `tools.*` import below would raise
# ModuleNotFoundError. The Makefile sets PYTHONPATH=., so a green `make status` cannot
# catch it -- `test_bare_invocation_works` is the guarantee, not this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.claude_home import (  # noqa: E402
    CONFIG_DIR_NAMES,
    config_roots,
    main_checkout,
    slugify_path,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# The harness's slug for the Linux box this repo was developed on. Kept as a
# literal because that tree still exists in every backup snapshot and on the old
# laptop -- a RESTORE should keep resolving, not silently start reading nothing.
LEGACY_SLUG = "-home-kng-repo-buibui-moon-trader-bot"

# Tried in order. `.claude-personal` first so a host that still has the original
# tree keeps reading it, rather than being quietly repointed at a second one.
# Re-exported from `tools.claude_home`, the one resolver (#838): it owns the
# candidates, the `CLAUDE_CONFIG_DIR` override and the slug rule, and this module adds
# only the restore-tolerant LEGACY_SLUG probe and the `home=` test seam.
CONFIG_ROOTS = CONFIG_DIR_NAMES


def slug_for(path: Path) -> str:
    """The harness's project-directory slug for an absolute repo path.

    Every character outside ``[A-Za-z0-9]`` becomes ``-``, which reproduces both
    observed spellings: ``/home/kng/repo/buibui-moon-trader-bot`` ->
    ``-home-kng-repo-buibui-moon-trader-bot``, and
    ``C:\\Users\\User\\repo\\buibui-moon-trader-bot`` ->
    ``C--Users-User-repo-buibui-moon-trader-bot``. The rule itself is
    `tools.claude_home.slugify_path`; this is its `Path`-typed spelling.
    """
    return slugify_path(str(path))


class MemoryDirNotFoundError(FileNotFoundError):
    """No candidate memory tree exists. Carries every path tried, so the miss is
    diagnosable from the message alone."""

    def __init__(self, tried: list[Path]) -> None:
        self.tried = tried
        listing = "\n".join(f"  {p.as_posix()}" for p in tried)
        super().__init__(f"no memory tree found; tried:\n{listing}")


def memory_dir(repo_root: Path | None = None, *, home: Path | None = None) -> Path:
    """The memory tree for `repo_root`. Raises `MemoryDirNotFoundError` when none exists.

    The slug is the MAIN checkout's (`tools.claude_home.main_checkout`), because that is
    where the harness keeps memory for every worktree of the repo. `repo_root` is NOT
    re-resolved: on Linux a Windows literal is a relative path, and `.resolve()` would
    prefix the cwd (CI caught exactly that). This used to slug
    `repo_root` itself and, finding nothing from a worktree, RETURN the legacy
    ``~/.claude-personal/projects/-home-kng-.../memory`` -- a path that does not exist
    on the Windows laptop, printed as though it were the answer. Every consumer then
    read "not on this machine". Raising makes the miss impossible to mistake for one.

    The candidate roots come from `tools.claude_home.config_roots`, so
    ``CLAUDE_CONFIG_DIR`` is honoured here exactly as it is by the backup script.
    `home` is injectable so the tests never depend on the developer's own tree (or
    environment): an explicit `home` wins over ``CLAUDE_CONFIG_DIR``.
    """
    roots = config_roots(home)
    root = REPO_ROOT if repo_root is None else repo_root
    derived = slug_for(main_checkout(root))

    tried: list[Path] = []
    for config_root in roots:
        for slug in dict.fromkeys((derived, LEGACY_SLUG)):
            candidate = config_root / "projects" / slug / "memory"
            if candidate.is_dir():
                return candidate
            tried.append(candidate)
    raise MemoryDirNotFoundError(tried)


if __name__ == "__main__":  # pragma: no cover - a Makefile entry point
    from utils.stdio import utf8_stdio

    utf8_stdio()
    # POSIX spelling deliberately: the only consumer is a `$(shell ...)` in the
    # Makefile, whose recipe shell EATS backslashes -- printing the native form
    # turned `C:\Users\User\...` into `C:UsersUser...` and `make status` reported
    # a missing file. Git Bash and every Python caller accept forward slashes.
    try:
        print(memory_dir().as_posix())
    except MemoryDirNotFoundError as exc:
        print(f"memory_dir.py: {exc}", file=sys.stderr)
        sys.exit(1)
