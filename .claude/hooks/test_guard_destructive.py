#!/usr/bin/env python3
"""Hand-run test harness for .claude/hooks/guard-destructive.py.

Wired into CI's dependency-free markdownlint job beside test_context_guard.py
and test_guard_shell_hygiene.py -- the BLOCKING hook was the one of the three
with no suite at all, so nothing had ever checked that it still blocks.

Includes MUTATION cases: each asserts that breaking the intended mechanism
actually lets the command through, so a pass proves the rule under test is what
blocked it rather than a neighbouring one. And NEGATIVE cases, because a false
positive in a BLOCKING hook stops real work rather than costing a glance.

Written with the Write tool rather than a heredoc, deliberately: the hook under
test matches the WHOLE command string, so `cat > … <<EOF` carrying these very
cases trips it on its own fixtures. That is the self-trigger this file's
section 4 pins for the new rule and leaves standing for the old ones.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude/hooks/guard-destructive.py"

PASS: list[str] = []
FAIL: list[str] = []


def run(
    command: str, *, hook: Path = HOOK, tool: str = "Bash", cwd: Path | None = None
) -> tuple[int, str]:
    payload: dict[str, object] = {"tool_name": tool, "tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stderr


def check(name: str, got: tuple[int, str], *, blocked: bool, reason: str = "") -> None:
    code, stderr = got
    if blocked:
        ok = code == 2 and reason in stderr
        detail = f"expected exit 2 with {reason!r}, got exit {code}: {stderr[:110]!r}"
    else:
        ok = code == 0
        detail = f"expected exit 0 (allowed), got exit {code}: {stderr[:110]!r}"
    (PASS if ok else FAIL).append(name)
    print(
        f"  {'ok  ' if ok else 'FAIL'}  {name}"
        + ("" if ok else f"\n          {detail}")
    )


print("guard-destructive")

# --- 1. the standing rules still block --------------------------------------
_RF = "rm -" + "rf"  # split so this file can be edited through a shell heredoc
check("rm -rf is blocked", run(f"{_RF} /tmp/scratch"), blocked=True, reason="rm -rf")
check(
    "deleting a duckdb file is blocked",
    run("rm analytics.db"),
    blocked=True,
    reason="DuckDB",
)
check(
    "git reset --hard is blocked",
    run("git reset --hard origin/main"),
    blocked=True,
    reason="reset --hard",
)
check(
    "a force-push is blocked",
    run("git push --force-with-lease origin main"),
    blocked=True,
    reason="force-push",
)

# --- 2. rclone config printing a live token ---------------------------------
# AGENTS.md: `rclone config create`/`update` print the whole remote -- client
# secret, access token, refresh token -- to stdout ON SUCCESS, unprompted and
# unflagged. Two live tokens leaked into transcripts on 2026-08-15 that way, the
# SECOND after both repos had already written up the first. The mitigation
# shipped as prose, and AGENTS.md's own conclusion is that "a rule that needs a
# human to notice output they did not ask for is not a control". So this rule
# BLOCKS rather than advises: the safe form passes untouched and the leaking
# form is unreachable, which is the difference between a control and a warning.
check(
    "rclone config create without a redirect is blocked",
    run("rclone config create gdrive drive scope=drive"),
    blocked=True,
    reason="/dev/null",
)
check(
    "rclone config update without a redirect is blocked",
    run("rclone config update gdrive token '{...}'"),
    blocked=True,
    reason="/dev/null",
)
check(
    "the block names the leak, not just the command",
    run("rclone config create gdrive drive"),
    blocked=True,
    reason="token",
)
check(
    "2>/dev/null does NOT count -- the secrets go to stdout",
    run("rclone config create gdrive drive 2>/dev/null"),
    blocked=True,
    reason="/dev/null",
)

# --- 3. the SAFE forms must pass, or the rule just relocates the problem -----
check(
    "the documented safe form passes",
    run("rclone config create gdrive drive scope=drive >/dev/null"),
    blocked=False,
)
check(
    "a spaced redirect passes",
    run("rclone config update gdrive token '{...}' > /dev/null"),
    blocked=False,
)
check(
    "redirect + stderr dup passes",
    run("rclone config create gdrive drive > /dev/null 2>&1"),
    blocked=False,
)
check("&>/dev/null passes", run("rclone config create g d &>/dev/null"), blocked=False)
check(
    "the safe VERIFICATIONS pass -- rclone lsf",
    run("rclone lsf gdrive:"),
    blocked=False,
)
check("rclone about passes", run("rclone about gdrive:"), blocked=False)
check(
    "rclone config reconnect passes -- the preferred rotation path",
    run("rclone config reconnect gdrive:"),
    blocked=False,
)
check("a plain sync passes", run("rclone sync /backup gdrive:buibui"), blocked=False)
# `deploy/README.md`'s own recipe, verbatim: the redirect sits on a CONTINUED
# line. A per-physical-line lookahead blocks it -- so the rule would have
# rejected the very command the repo tells the operator to run, which is the
# fastest way to get a blocking guard switched off. Caught by the /post-branch
# rule-claim grep, not by the suite.
check(
    "the documented multi-line recipe passes -- redirect on a continued line",
    run(
        "rclone config create gdrive drive \\\n"
        "  client_id=<ID> client_secret=<SECRET> scope=drive >/dev/null"
    ),
    blocked=False,
)
check(
    "...and a continued line with NO redirect anywhere is still blocked",
    run("rclone config create gdrive drive \\\n  client_id=<ID> scope=drive"),
    blocked=True,
    reason="/dev/null",
)

# --- 3b. TALKING about the command is not RUNNING it ------------------------
# The rule fires only where an actual invocation can start. Without that anchor
# it blocks its own commit message, and a blocking hook that stops honest work
# gets switched off -- which is worse than the advisory it replaced. Same
# boundary shape settings.json already uses for its `gh pr create` reminder.
check(
    "a commit message naming the anti-pattern passes",
    run('git commit -m "fix: rclone config create leaks the token"'),
    blocked=False,
)
check(
    "grepping the docs for it passes",
    run('grep -n "rclone config create" AGENTS.md'),
    blocked=False,
)
check(
    "echoing it passes",
    run('echo "never run rclone config create bare"'),
    blocked=False,
)

# ...but every real invocation site still blocks.
check(
    "chained after && is blocked",
    run("cd /tmp && rclone config create gdrive drive"),
    blocked=True,
    reason="/dev/null",
)
check(
    "inside a command substitution is blocked",
    run("out=$(rclone config update gdrive token '{...}')"),
    blocked=True,
    reason="/dev/null",
)
check(
    "an env-assignment prefix does not smuggle it through",
    run("RCLONE_CONFIG=/tmp/r.conf rclone config create gdrive drive"),
    blocked=True,
    reason="/dev/null",
)
check(
    "sudo does not smuggle it through",
    run("sudo rclone config create gdrive drive"),
    blocked=True,
    reason="/dev/null",
)
check(
    "on its own line in a multi-line script is blocked",
    run("cd /tmp\nrclone config create gdrive drive\necho done"),
    blocked=True,
    reason="/dev/null",
)

# --- 4. documenting the anti-pattern must stay possible ---------------------
# The rclone rule is matched with heredoc BODIES stripped; the standing rules
# are NOT. A blocking guard does not get more permissive as a side effect of
# adding a rule to it -- so the two behaviours are pinned separately here.
#
# The body line below starts with the bare command, which is the ONLY shape that
# tests the stripping: prose quoting it mid-sentence is already allowed by the
# section-3b anchor, so a case written that way passes without the mechanism
# under test doing anything. It was written that way first.
check(
    "a heredoc DOCUMENTING the unsafe form is data, not a command",
    run(
        "cat > /tmp/note.md <<'EOF'\n"
        "Never write the recipe this way:\n"
        "rclone config create gdrive drive\n"
        "EOF"
    ),
    blocked=False,
)
check(
    "...but a REAL unsafe call after a heredoc is still blocked",
    run("cat <<'EOF' > /tmp/x\nnotes\nEOF\nrclone config create gdrive drive"),
    blocked=True,
    reason="/dev/null",
)
check(
    "...and the destructive rules still read heredoc bodies (unchanged)",
    run(f"cat > /tmp/s.sh <<'EOF'\n{_RF} /var/tmp/build\nEOF"),
    blocked=True,
    reason="rm -rf",
)

# --- 5. protocol -------------------------------------------------------------
check(
    "a non-Bash tool is ignored",
    run("rclone config create gdrive drive", tool="Edit"),
    blocked=False,
)
_bad = subprocess.run(
    [sys.executable, str(HOOK)], input="not json", capture_output=True, text=True
)
check("malformed stdin fails OPEN", (_bad.returncode, _bad.stderr), blocked=False)

# --- 6. MUTATION: prove which rule fired ------------------------------------
_mut = Path(tempfile.mkdtemp(prefix="guard-destructive-mut-"))
try:
    _m1 = _mut / "no-rclone.py"
    _m1.write_text(
        HOOK.read_text(encoding="utf-8").replace(
            r"rclone\s+config\s+(?:create|update)", r"__never__"
        ),
        encoding="utf-8",
    )
    check(
        "MUTATION: rclone pattern removed -> the unsafe call is allowed",
        run("rclone config create gdrive drive", hook=_m1),
        blocked=False,
    )
    check(
        "MUTATION: ...and the destructive rules are UNAFFECTED (scoped, not blanket)",
        run(f"{_RF} /tmp/x", hook=_m1),
        blocked=True,
        reason="rm -rf",
    )

    # The redirect escape is what tells the safe form from the leaking one.
    # Without it the rule blocks BOTH -- which passes every positive case above
    # while making the documented recipe unusable, so it needs its own mutation.
    # ...and the heredoc stripping is load-bearing for the case above rather
    # than incidental to it: put the body back and the doc line blocks again.
    _m1b = _mut / "no-strip.py"
    _m1b.write_text(
        HOOK.read_text(encoding="utf-8").replace(
            'return _HEREDOC.sub("<<STRIPPED", command)', "return command"
        ),
        encoding="utf-8",
    )
    check(
        "MUTATION: stop stripping heredocs -> documenting the recipe blocks again",
        run(
            "cat > /tmp/note.md <<'EOF'\n"
            "Never write the recipe this way:\n"
            "rclone config create gdrive drive\n"
            "EOF",
            hook=_m1b,
        ),
        blocked=True,
        reason="/dev/null",
    )

    # ...and joining line continuations is load-bearing for the documented
    # recipe, not incidental: unjoined, the redirect on the next physical line
    # is invisible to a `[^\n]*` lookahead and the recipe blocks.
    _m1c = _mut / "no-join.py"
    _m1c.write_text(
        HOOK.read_text(encoding="utf-8").replace(
            'return _HEREDOC.sub("<<STRIPPED", command).replace("\\\\\\n", " ")',
            'return _HEREDOC.sub("<<STRIPPED", command)',
        ),
        encoding="utf-8",
    )
    check(
        "MUTATION: stop joining continuations -> the documented recipe blocks",
        run(
            "rclone config create gdrive drive \\\n"
            "  client_id=<ID> client_secret=<SECRET> scope=drive >/dev/null",
            hook=_m1c,
        ),
        blocked=True,
        reason="/dev/null",
    )

    _m2 = _mut / "no-redirect-escape.py"
    _m2.write_text(
        HOOK.read_text(encoding="utf-8").replace(r"(?![^\n]*(?<!2)>\s*/dev/null)", ""),
        encoding="utf-8",
    )
    check(
        "MUTATION: drop the redirect escape -> the SAFE form is blocked too",
        run("rclone config create gdrive drive >/dev/null", hook=_m2),
        blocked=True,
        reason="/dev/null",
    )
finally:
    shutil.rmtree(_mut, ignore_errors=True)

# --- 7. git worktree remove through a directory link (#963) -----------------
# On Windows git FOLLOWS a junction inside the worktree and deletes the
# TARGET's contents; on 2026-10-08 that emptied half the main .venv. The rule
# reads the filesystem, so these cases build real trees. Only the HOOK runs --
# git never does, so a wrong verdict here cannot delete anything.
_SKIPPED: list[str] = []


def _make_dir_link(link: Path, target: Path) -> bool:
    """A junction on Windows (no Developer Mode needed), a symlink elsewhere."""
    if sys.platform == "win32":
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
        )
        return proc.returncode == 0
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        return False
    return True


def _unlink_dir_link(link: Path) -> None:
    # rmdir on a junction/symlink removes the LINK only -- the safe step the
    # block message names. Never rmtree a tree that still holds one.
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        os.rmdir(link)


_wt = Path(tempfile.mkdtemp(prefix="guard-destructive-wt-"))
_link = _wt / "linked" / "sub" / ".venv"
try:
    _venv = _wt / "real-venv"
    (_venv / "lib").mkdir(parents=True)
    (_venv / "lib" / "keep.txt").write_text("x", encoding="utf-8")
    _linked = _wt / "linked"
    (_linked / "sub").mkdir(parents=True)
    (_linked / "src.py").write_text("x", encoding="utf-8")
    _plain = _wt / "plain"
    (_plain / "pkg").mkdir(parents=True)

    if not _make_dir_link(_link, _venv):
        _SKIPPED.append("could not create a directory link on this host")
    else:
        check(
            "worktree remove over a nested directory link is blocked",
            run(f"git worktree remove --force {_linked}"),
            blocked=True,
            reason="directory link",
        )
        check(
            "...and the block names the safe first step",
            run(f"git worktree remove {_linked}"),
            blocked=True,
            reason="rmdir",
        )
        check(
            "a relative target resolves against the payload cwd",
            run("git worktree remove --force linked", cwd=_wt),
            blocked=True,
            reason="directory link",
        )
        check(
            "git -C <dir> resolves the target against that dir",
            run(f'git -C "{_wt}" worktree remove linked'),
            blocked=True,
            reason="directory link",
        )
        check(
            "chained after && is blocked",
            run(f"cd {_wt} && git worktree remove linked", cwd=_wt),
            blocked=True,
            reason="directory link",
        )
        check(
            "the PowerShell tool is guarded too -- it is this host's primary shell",
            run(f"git worktree remove --force '{_linked}'", tool="PowerShell"),
            blocked=True,
            reason="directory link",
        )
        check(
            "PowerShell does NOT inherit the bash-shaped rules",
            run("rm analytics.db", tool="PowerShell"),
            blocked=False,
        )
        check(
            "naming the command in a commit message passes",
            run(f'git commit -m "guard: git worktree remove {_linked}"', cwd=_wt),
            blocked=False,
        )
        check(
            "a tree with NO link passes",
            run(f"git worktree remove --force {_plain}"),
            blocked=False,
        )
        check(
            "a target that does not exist passes (nothing to traverse)",
            run(f"git worktree remove {_wt / 'gone'}"),
            blocked=False,
        )
        check(
            "git worktree list passes",
            run("git worktree list", cwd=_wt),
            blocked=False,
        )
        if sys.platform == "win32":
            _gb = "/" + str(_linked)[0].lower() + str(_linked)[2:].replace("\\", "/")
            check(
                "a Git Bash /c/... path is resolved, not read as \\c\\...",
                run(f"git worktree remove {_gb}"),
                blocked=True,
                reason="directory link",
            )

        _mw = Path(tempfile.mkdtemp(prefix="guard-destructive-mutwt-"))
        try:
            _m3 = _mw / "no-link-check.py"
            _m3.write_text(
                HOOK.read_text(encoding="utf-8").replace(
                    "return linked and os.path.isdir(entry.path)", "return False"
                ),
                encoding="utf-8",
            )
            check(
                "MUTATION: link detection off -> the junction tree is allowed",
                run(f"git worktree remove --force {_linked}", hook=_m3),
                blocked=False,
            )
            _m4 = _mw / "follows-links.py"
            _m4.write_text(
                HOOK.read_text(encoding="utf-8").replace(
                    "elif entry.is_dir(follow_symlinks=False):",
                    "if entry.is_dir(follow_symlinks=True):",
                ),
                encoding="utf-8",
            )
            check(
                "MUTATION: walk follows links -> still blocked (detection, not descent, decides)",
                run(f"git worktree remove --force {_linked}", hook=_m4),
                blocked=True,
                reason="directory link",
            )
        finally:
            shutil.rmtree(_mw, ignore_errors=True)

        check(
            "the link TARGET survived every case above",
            (0 if (_venv / "lib" / "keep.txt").exists() else 2, ""),
            blocked=False,
        )
finally:
    _unlink_dir_link(_link)
    shutil.rmtree(_wt, ignore_errors=True)

for s in _SKIPPED:
    print(f"  SKIP  {s}")
if _SKIPPED and os.environ.get("CI"):
    FAIL.append("section 7 SKIPPED under CI -- a skip is not a pass")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL  {f}")
sys.exit(1 if FAIL else 0)
