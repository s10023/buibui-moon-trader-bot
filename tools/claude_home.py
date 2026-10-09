r"""Where this checkout's Claude Code config root and project directory live.

The ONE resolver. Ported from the sibling fork's ``tools/claude_home.py`` (#838) rather
than re-derived, and ``tools/memory_dir.py`` now delegates here, so the config-root
candidates and the slug rule each have exactly one spelling.

Call sites derived this independently and all of them drifted -- the Makefile, the
daily check, ``post_branch_checks`` and ``deploy/backup-analytics.sh``. The backup
script was the worst: it named ``~/.claude-personal`` as a tracked literal, so after
the 2026-09-18 move to ``~/.claude`` on the Windows laptop its account-level entries
(``history.jsonl``, ``CLAUDE.md``, ``settings.json``, ``tools/``, ``skills/``,
``commands/``) matched nothing.

**Each one fails silently, in the direction of absence.** The backup loops are
``[ -f ]`` / glob-miss guarded, so a moved root records nothing rather than failing,
and the snapshot still looks populated because the legacy trees keep matching. The
whole class reads as "nothing to do" on a host where the tree is present and merely
unlocated. Deduping rather than correcting each site is deliberate: fixing the values
in place would hide a missing key as well as the defect being fixed.

Imports nothing from the repo, so it needs no ``sys.path`` bootstrap -- a bare
``python3 tools/claude_home.py`` import works, and the backup script inserts its
directory on ``sys.path`` explicitly.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path, PurePath

#: Every character outside ``[A-Za-z0-9]`` becomes ``-``. That covers the path
#: separators and the Windows drive colon, and also ``.``, ``_`` and spaces, which
#: the harness folds the same way (a checkout under ``/x/.claude/worktrees/y``
#: slugs to ``-x--claude-worktrees-y``). The sibling fork folds only ``[/\\:]``,
#: which agrees on every path without a dot, underscore or space.
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")

#: Config-root candidates, most specific first. ``.claude-personal`` is a second
#: profile the operator used on a shared work machine to keep personal projects out of
#: the work account's config (and the old Linux box); a personal box normally has only
#: ``.claude``. Probing rather than hardcoding is what lets one tracked literal serve
#: every host, and ``CLAUDE_CONFIG_DIR`` overrides both because that is the variable
#: Claude Code itself honours.
#:
#: **Both can exist at once, which is why order alone cannot decide.** See
#: `project_dir`: selection is on ``projects/<slug>``, never on the root.
CONFIG_DIR_NAMES = (".claude-personal", ".claude")


def slugify_path(text: str) -> str:
    r"""Fold an absolute path into Claude Code's project-directory name.

    Pure and platform-independent on purpose: it takes the path as text rather than a
    ``Path``, so the Windows rule is testable from Linux CI and the POSIX rule from a
    Windows box. A ``Path``-typed argument would resolve against the running platform
    and make exactly one of those two assertions unwritable.

        ``/home/kng/repo/x``       -> ``-home-kng-repo-x``
        ``C:\Users\User\repo\x``   -> ``C--Users-User-repo-x``
    """
    return _NON_ALNUM.sub("-", text)


def main_checkout_from_common_dir[P: PurePath](checkout: P, common_dir: str) -> P:
    r"""The main working tree, given ``git rev-parse --git-common-dir`` run in `checkout`.

    Pure, and typed over ``PurePath`` so a Windows answer is testable from Linux CI
    (``PureWindowsPath``) exactly as `slugify_path` takes text for the same reason.
    Git answers RELATIVE in the main checkout (``.git``) and absolute in a linked
    worktree (``C:/Users/User/repo/x/.git``); both name the shared ``.git``, whose
    parent is the main checkout. ``--path-format=absolute`` would remove the relative
    case but needs git 2.31, and the Windows laptop runs 2.28 -- there it is echoed
    back as a literal line rather than rejected.
    """
    common = type(checkout)(common_dir)
    if not common.is_absolute():
        common = checkout / common
    return common.parent


def main_checkout(repo_root: Path) -> Path:
    """The main checkout `repo_root` belongs to: itself, or a linked worktree's owner.

    **The harness keys a session's memory on the MAIN checkout, not on the worktree
    it runs in** -- a session under ``.claude/worktrees/<name>`` reads
    ``projects/C--Users-User-repo-buibui-moon-trader-bot/memory``. Slugging the
    worktree's own path found nothing, and the resolver then fell through to a legacy
    fallback on another host's config root.

    A path with no ``.git`` entry, or one git cannot answer for, is returned as given:
    probing upward would let an enclosing repository answer for a directory that is
    not part of it.
    """
    if not (repo_root / ".git").exists():
        return repo_root
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "rev-parse", "--git-common-dir"],  # noqa: S607
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return repo_root
    out = proc.stdout.strip()
    if not out:
        return repo_root
    return main_checkout_from_common_dir(repo_root, out).resolve()


def project_slug(repo_root: Path) -> str:
    """``slugify_path`` over the resolved path of the checkout's MAIN working tree."""
    return slugify_path(str(main_checkout(repo_root.resolve())))


def config_roots(home: Path | None = None) -> tuple[Path, ...]:
    """Config roots to try, most specific first.

    ``CLAUDE_CONFIG_DIR`` collapses this to one entry, because an explicit setting
    must not be second-guessed by a probe. An injected `home` is a test seam and wins
    over the environment, so a hermetic caller is not at the mercy of the host's.
    """
    if home is None:
        env = os.environ.get("CLAUDE_CONFIG_DIR")
        if env:
            return (Path(env),)
        home = Path.home()
    return tuple(home / name for name in CONFIG_DIR_NAMES)


def project_dir(repo_root: Path) -> Path:
    """This checkout's Claude Code project directory.

    **Selection is on ``projects/<slug>``, never on the config root.** Probing whether
    ``~/.claude-personal`` exists and taking it if so is wrong: the directory can
    appear while both profiles are in use, so the probe would choose a root that never
    held this project and every consumer would read ABSENT. Both roots can exist; only
    one holds the tree. Root existence is a proxy; the project directory is the thing
    actually wanted, so it is what gets tested.

    Falls back to the last candidate when none holds the tree, so a fresh host still
    names a sensible destination rather than raising. Every consumer degrades to a
    printed note on an absent tree, and raising here would break the backup rather
    than the report.
    """
    slug = project_slug(repo_root)
    candidates = config_roots()
    for root in candidates:
        if (root / "projects" / slug).is_dir():
            return root / "projects" / slug
    return candidates[-1] / "projects" / slug


def claude_home(repo_root: Path) -> Path:
    """The config root that this checkout's project directory lives under.

    Derived from `project_dir` rather than computed alongside it, so the two can never
    disagree about which root won.
    """
    return project_dir(repo_root).parent.parent


def memory_dir(repo_root: Path) -> Path:
    """This checkout's memory tree -- the SoT to-do's home."""
    return project_dir(repo_root) / "memory"
