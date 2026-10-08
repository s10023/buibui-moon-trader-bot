#!/usr/bin/env python3
"""Hand-run test harness for the hook WRAPPERS in .claude/settings.json.

Wired into CI's dependency-free markdownlint job beside test_context_guard.py,
test_guard_shell_hygiene.py and test_guard_destructive.py.

WHY THIS TESTS THE WRAPPER AND NOT A MODULE
-------------------------------------------
On 2026-09-23 every PreToolUse hook on the Windows host was dead: each wrapper
launched `python3`, which there resolves to the Store App Execution Alias and
exits 126. Only exit 2 blocks, so guard-destructive.py -- the BLOCKING hook --
failed OPEN on every command for five days.

No hook MODULE was broken. A module test passed identically before and after,
which is why this file reads the wrapper string out of settings.json instead.

It lives in .claude/hooks/ rather than tests/ ON PURPOSE. The heavy CI leg is
paths-filtered to `**/*.py`, pyproject, poetry.lock and lint.yaml -- and
`.claude/settings.json` is in none of them. A gate for settings.json placed in
tests/ would not run on the very diff that can break it. The markdownlint job
carries no paths filter, so this runs unconditionally.

THE MUTATIONS RUN AGAINST A FIXTURE, NOT THE HOST
-------------------------------------------------
The defect is invisible on Linux, where `python3` works -- so a mutation that
merely reverted the ordering would pass on the only platform CI runs. Each
end-to-end case therefore builds a throwaway project dir with a WORKING venv
shim and a BROKEN `python3` (exit 126) first on PATH, reproducing the Windows
failure on any host.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SETTINGS = REPO / ".claude" / "settings.json"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASS.append(name)
        print(f"  ok    {name}")
    else:
        FAIL.append(f"{name}{(' -- ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(' -- ' + detail) if detail else ''}")


def all_commands() -> list[tuple[str, str, str]]:
    """(event, matcher, command) for every configured command hook."""
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    out: list[tuple[str, str, str]] = []
    for event, groups in data.get("hooks", {}).items():
        for g in groups:
            for hk in g.get("hooks", []):
                if hk.get("type") == "command":
                    out.append((event, g.get("matcher", ""), hk.get("command", "")))
    return out


def python_wrappers() -> list[tuple[str, str]]:
    """(hook script name, wrapper command) for every wrapper that runs a .py hook."""
    out: list[tuple[str, str]] = []
    for _event, _matcher, cmd in all_commands():
        m = re.search(r"\.claude/hooks/([a-z0-9-]+\.py)", cmd)
        if m:
            out.append((m.group(1), cmd))
    return out


# --------------------------------------------------------------------------
# 1. static assertions against the real settings.json
# --------------------------------------------------------------------------
print("static wiring:")

wrappers = python_wrappers()
check(
    "settings.json declares python hook wrappers",
    len(wrappers) >= 5,
    f"found {len(wrappers)}",
)

for name, cmd in wrappers:
    check(
        f"{name}: does not launch with bare python3",
        not cmd.startswith("python3 "),
        cmd[:60],
    )
    venv_at = cmd.find(".venv/")
    py3_at = cmd.find("python3")
    check(
        f"{name}: a venv interpreter is tried BEFORE python3",
        venv_at != -1 and (py3_at == -1 or venv_at < py3_at),
        f"venv@{venv_at} python3@{py3_at}",
    )
    check(
        f"{name}: guards a missing hook file with an explicit exit 0",
        "[ -f " in cmd and "exit 0" in cmd,
        "missing-file guard absent -> CPython exits 2 and the hook fails CLOSED",
    )
    # The flip side of that guard: a MISSPELT path also fails open, silently, so
    # a wrapper naming a file that is not there is a hook that never runs.
    check(
        f"{name}: the wrapped hook file exists",
        (REPO / ".claude" / "hooks" / name).is_file(),
        "the [ -f ] guard turns a wrong path into a hook that silently never fires",
    )

# #855: the host has no `jq`. The inline `jq | head -1 | grep` post-branch
# reminders failed there, `|| true` swallowed it, and neither ever fired -- the
# same fail-open shape as the exit-126 interpreter. Generalised past jq: EVERY
# configured command must be a .py-hook wrapper, so no hook can depend on a
# binary the box may lack (jq, grep, a stray python3) outside the venv resolver.
commands = all_commands()
for event, matcher, cmd in commands:
    where = f"{event}[{matcher or '*'}]"
    check(
        f"{where}: does not call jq",
        re.search(r"(?:^|[\s;|&(])jq(?:\s|$)", cmd) is None,
        cmd[:80],
    )
    check(
        f"{where}: is a .py-hook wrapper, not an inline one-liner",
        re.search(r"\.claude/hooks/[a-z0-9-]+\.py", cmd) is not None,
        cmd[:80],
    )

# The lifecycle advisories replace the jq pair, so each leg must stay wired:
# a dropped leg is exactly as silent as the jq failure it replaced.
for event, matcher in (
    ("PreToolUse", "Bash"),
    ("PostToolUse", "Bash"),
    ("UserPromptSubmit", ""),
):
    check(
        f"advise-lifecycle.py is wired on {event}[{matcher or '*'}]",
        any(
            e == event and m == matcher and "advise-lifecycle.py" in c
            for e, m, c in commands
        ),
    )


# #885: the skill-usage ledger needs BOTH legs. Drop the typed one and every
# operator-typed `/skill` vanishes from the counts, so the report names a skill
# the operator uses daily as unused -- a silent miss that reads as a finding.
for event, matcher in (("PreToolUse", "Skill"), ("UserPromptSubmit", "")):
    check(
        f"log-skill-usage.py is wired on {event}[{matcher or '*'}]",
        any(
            e == event and m == matcher and "log-skill-usage.py" in c
            for e, m, c in commands
        ),
    )

# --------------------------------------------------------------------------
# 2. end-to-end, against a fixture reproducing the Windows failure
# --------------------------------------------------------------------------
BASH = shutil.which("bash")

BLOCKING_HOOK = "import sys; sys.stderr.write('BLOCKED\\n'); sys.exit(2)\n"
BENIGN_HOOK = "import sys; sys.exit(0)\n"


def _write_exec(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def run_wrapper(cmd: str, project: Path, broken_python3: Path) -> int:
    """Run a wrapper string the way the harness does, with a broken python3 first."""
    if BASH is None:  # every caller sits inside the bash-present branch below
        raise RuntimeError("bash vanished from PATH between the check and the call")
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    env["PATH"] = str(broken_python3) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        [BASH, "-c", cmd],
        input="{}",
        text=True,
        capture_output=True,
        env=env,
    ).returncode


if BASH is None:
    check("bash is available to run the wrappers", False, "no bash on PATH")
else:
    print("\nend-to-end (fixture: working venv shim + python3 that exits 126):")
    tmp = Path(tempfile.mkdtemp(prefix="hookwire-"))
    try:
        project = tmp / "project"
        (project / ".claude" / "hooks").mkdir(parents=True)

        # a python3 that behaves like the Windows Store alias
        stub_dir = tmp / "stub"
        _write_exec(stub_dir / "python3", "#!/bin/sh\nexit 126\n")

        # a venv shim that reaches the real interpreter, at BOTH layouts
        shim = f'#!/bin/sh\nexec "{sys.executable}" "$@"\n'
        _write_exec(project / ".venv" / "bin" / "python", shim)
        _write_exec(project / ".venv" / "Scripts" / "python.exe", shim)

        (project / ".claude" / "hooks" / "blocker.py").write_text(
            BLOCKING_HOOK, encoding="utf-8"
        )
        (project / ".claude" / "hooks" / "benign.py").write_text(
            BENIGN_HOOK, encoding="utf-8"
        )

        template = next(cmd for _, cmd in wrappers if "guard-destructive" in cmd)

        blocking = template.replace("guard-destructive.py", "blocker.py")
        benign = template.replace("guard-destructive.py", "benign.py")
        missing = template.replace("guard-destructive.py", "not-a-real-hook.py")

        check(
            "a blocking hook still exits 2 although python3 is broken",
            run_wrapper(blocking, project, stub_dir) == 2,
            f"got {run_wrapper(blocking, project, stub_dir)}",
        )
        check(
            "a benign command exits 0",
            run_wrapper(benign, project, stub_dir) == 0,
        )
        check(
            "a MISSING hook file still fails OPEN (exit 0, never 2)",
            run_wrapper(missing, project, stub_dir) == 0,
            f"got {run_wrapper(missing, project, stub_dir)}",
        )

        # MUTATION 1 -- revert to bare python3 first. This is the shipped defect.
        mutated = 'h="$CLAUDE_PROJECT_DIR/.claude/hooks/blocker.py"; exec python3 "$h"'
        rc = run_wrapper(mutated, project, stub_dir)
        check(
            "MUTATION: bare python3 first -> the guard fails OPEN (126, not 2)",
            rc == 126,
            f"got {rc}; the fixture no longer reproduces the defect",
        )

        # MUTATION 2 -- drop the missing-file guard. CPython exits 2 on an
        # unreadable script, so absence would flip fail-OPEN to fail-CLOSED.
        no_guard = (
            'h="$CLAUDE_PROJECT_DIR/.claude/hooks/not-a-real-hook.py"; '
            'for p in "$CLAUDE_PROJECT_DIR/.venv/Scripts/python.exe" '
            '"$CLAUDE_PROJECT_DIR/.venv/bin/python"; do '
            '[ -x "$p" ] && exec "$p" "$h"; done; exec python3 "$h"'
        )
        rc = run_wrapper(no_guard, project, stub_dir)
        check(
            "MUTATION: no missing-file guard -> an absent hook BLOCKS (exit 2)",
            rc == 2,
            f"got {rc}; the guard in the real wrapper is then untested",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL  {f}")
sys.exit(1 if FAIL else 0)
