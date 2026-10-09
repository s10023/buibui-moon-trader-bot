#!/usr/bin/env python3
"""PreToolUse guard hook — blocks catastrophic / unrecoverable Bash commands.

Self-authored (no third-party dependency) so every rule is reviewable here.
Wired via .claude/settings.json -> hooks.PreToolUse (matcher "Bash").

Protocol: Claude Code pipes {"tool_name","tool_input":{"command":...}} on stdin.
Exit 0 = allow. Exit 2 = block (stderr reason is shown to Claude and the user).

Tune by editing HARD_DENY below. Disable by removing the hook from settings.json.
The OPTIONAL block (live signal daemon) is off by default — uncomment to enable.

TWO lists, matched differently. HARD_DENY sees the command verbatim, heredoc
bodies included — that is how it has always behaved and making a blocking guard
more permissive is not a side effect worth taking on while adding a rule to it.
HARD_DENY_OUTSIDE_HEREDOCS sees the command with heredoc bodies stripped, for a
rule whose own anti-pattern has to stay WRITEABLE: the rclone rule below exists
because prose failed to prevent a leak, so the docs that describe it get written
and re-written, and a guard that blocks its own documentation gets disabled.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path

# (regex, human reason) — matched case-insensitively against the full command.
HARD_DENY: list[tuple[str, str]] = [
    (
        r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r",
        "rm -rf (recursive force delete)",
    ),
    (
        r"\brm\b.*\.(duckdb|db)\b",
        "deleting a DuckDB/.db file (analytics.db is gitignored + not trivially recoverable)",
    ),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard (discards uncommitted work)"),
    (r"\bgit\s+clean\s+-[a-z]*[dx]", "git clean -fd/-fdx (deletes untracked files)"),
    (r"\bgit\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b)", "git force-push"),
    (
        r"\b(drop\s+table|drop\s+database|truncate\s+table)\b",
        "destructive SQL (DROP/TRUNCATE)",
    ),
    (r"\bclean-db\b", "clean-db (wipes the analytics DB)"),
]

# Same shape, but matched against the command with heredoc BODIES stripped.
HARD_DENY_OUTSIDE_HEREDOCS: list[tuple[str, str]] = [
    (
        # `rclone config create|update` PRINT the whole remote — client_secret,
        # access_token, refresh_token — to stdout ON SUCCESS, unprompted and
        # unflagged. Two live tokens leaked into transcripts on 2026-08-15 this
        # way, the second AFTER both repos had written up the first: the
        # mitigation was prose, and AGENTS.md's own reading is that "a rule that
        # needs a human to notice output they did not ask for is not a control".
        # So it blocks. The escape is the fix itself — append `>/dev/null` — and
        # `2>` deliberately does not satisfy it, since the secrets go to stdout.
        #
        # ANCHORED where a command can actually start (string start, newline, or
        # after ; & | (), past any env assignments and `sudo`) so that TALKING
        # about the anti-pattern is not blocked. Unanchored, this rule blocks its
        # own commit message and the grep that finds it in the docs — and a
        # blocking guard that stops honest work gets switched off, which leaves
        # the leak with no control at all. Same boundary settings.json already
        # uses for its `gh pr create` reminder.
        r"(?:^|[\n;&|(])\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:sudo\s+)?"
        r"rclone\s+config\s+(?:create|update)\b(?![^\n]*(?<!2)>\s*/dev/null)",
        "rclone config create/update with no `>/dev/null` — it prints the "
        "remote's client_secret, access_token and refresh_token to stdout on "
        "SUCCESS, so the token lands in the transcript. Append `>/dev/null` "
        "(`2>/dev/null` does NOT help — the secrets go to stdout). Verify with "
        "`rclone lsf <remote>:` or `rclone about <remote>:`, which print none",
    ),
]

# Kept in step with the copy in guard-shell-hygiene.py. Two standalone hook
# scripts with no shared module between them (CI runs each on a bare
# interpreter), so this is duplicated on purpose — edit both.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1.*?^\s*\2\s*$", re.DOTALL | re.MULTILINE)


def _strip(command: str) -> str:
    """Heredoc BODIES out (data, not commands), line continuations joined.

    The join is what the shell itself does, and it is load-bearing rather than
    tidiness: `deploy/README.md`'s own recipe puts the `>/dev/null` on a
    continued line, so a per-physical-line lookahead rejects the exact command
    the repo tells the operator to run. A blocking guard that stops the
    documented recipe is one that gets switched off.
    """
    return _HEREDOC.sub("<<STRIPPED", command).replace("\\\n", " ")


# `git worktree remove` on Windows FOLLOWS a directory junction inside the tree
# and deletes the TARGET's contents before failing "Invalid argument". Measured
# 2026-10-08 (#963): a review worktree's `.venv` junction took half the main
# checkout's `.venv` with it and every scheduled job died at import for ~9h.
# This rule needs the FILESYSTEM, not a regex: the same command is safe or
# catastrophic depending on what the tree holds. So it walks the target without
# following links and blocks on any directory link it finds. Anchored like the
# rclone rule, so naming the command in prose or a commit message passes.
_WORKTREE_REMOVE = re.compile(
    r"(?:^|[\n;&|(])\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"
    r"git\s+(?:-C\s+(?P<c>\"[^\"]*\"|'[^']*'|\S+)\s+)?worktree\s+remove\b"
    r"(?P<args>[^\n;&|)]*)",
    re.IGNORECASE,
)
_WALK_LIMIT = 200_000  # entries; past this, fail open rather than stall the session
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _unquote(token: str) -> str:
    return token[1:-1] if len(token) > 1 and token[0] == token[-1] in "\"'" else token


def _to_path(raw: str, base: Path) -> Path:
    raw = os.path.expanduser(_unquote(raw))
    # Git Bash spells C:\x as /c/x; Python on Windows reads that as \c\x.
    m = re.match(r"^/([a-zA-Z])(/.*)?$", raw)
    if os.name == "nt" and m:
        raw = f"{m.group(1)}:{m.group(2) or '/'}"
    p = Path(raw)
    return p if p.is_absolute() else base / p


def _is_dir_link(entry: os.DirEntry[str]) -> bool:
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    linked = entry.is_symlink() or bool(attrs & _REPARSE)
    return linked and os.path.isdir(entry.path)


def _dir_links(root: Path) -> list[str]:
    """Directory symlinks/junctions under root, without descending into any."""
    found: list[str] = []
    stack, seen = [str(root)], 0
    while stack:
        try:
            it = os.scandir(stack.pop())
        except OSError:
            continue
        with it:
            for entry in it:
                seen += 1
                if seen > _WALK_LIMIT:
                    return found
                if _is_dir_link(entry):
                    found.append(entry.path)
                elif entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
    return found


def worktree_remove_reason(command: str, cwd: Path) -> str | None:
    for m in _WORKTREE_REMOVE.finditer(_strip(command)):
        base = _to_path(m.group("c"), cwd) if m.group("c") else cwd
        try:
            args = shlex.split(m.group("args"), posix=False)
        except ValueError:
            continue
        targets = [a for a in args if not a.startswith("-")]
        if not targets:
            continue
        target = _to_path(targets[0], base)
        if not target.is_dir():
            continue
        links = _dir_links(target)
        if links:
            shown = ", ".join(links[:3]) + (" ..." if len(links) > 3 else "")
            return (
                f"git worktree remove on a tree holding {len(links)} directory "
                f"link(s) ({shown}). On Windows git FOLLOWS a junction and deletes "
                "the TARGET's contents (#963 emptied the main .venv this way). "
                'Remove each link first -- `cmd //c rmdir "<link>"` removes a '
                "junction without touching its target -- then re-run"
            )
    return None


# OPTIONAL — guard against the live signal daemon firing real Telegram + DB writes.
# Per MEMORY.md: `signal watch --once` fired real Telegram and wrote real analytics.db.
# Uncomment to require a conscious override.
# HARD_DENY.append(
#     (r"\bsignal\s+watch\b", "live signal daemon (sends real Telegram + writes real DB) — smoke-test deliberately")
# )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open — never break the session on a parse error

    tool = payload.get("tool_name")
    if tool not in ("Bash", "PowerShell"):
        return 0

    command = str(payload.get("tool_input", {}).get("command", ""))
    if not command:
        return 0

    reasons: list[str] = []
    # The regex lists are bash-shaped and stay Bash-only; the worktree rule is
    # shell-agnostic, and PowerShell is this host's PRIMARY shell (#963).
    if tool == "Bash":
        checks = [(HARD_DENY, command), (HARD_DENY_OUTSIDE_HEREDOCS, _strip(command))]
        reasons += [
            reason
            for rules, subject in checks
            for pattern, reason in rules
            if re.search(pattern, subject, re.IGNORECASE)
        ]
    if not reasons:
        cwd = Path(str(payload.get("cwd") or os.getcwd()))
        worktree = worktree_remove_reason(command, cwd)
        if worktree:
            reasons.append(worktree)

    if reasons:
        sys.stderr.write(
            f"BLOCKED by guard-destructive hook: {reasons[0]}.\n"
            f"Command: {command}\n"
            "If this is genuinely intended, run it yourself in a terminal, "
            "rephrase it, or temporarily disable the hook in .claude/settings.json.\n"
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
