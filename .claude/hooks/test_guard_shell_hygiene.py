#!/usr/bin/env python3
"""Hand-run test harness for .claude/hooks/guard-shell-hygiene.py.

Wired into CI's dependency-free markdownlint job beside test_context_guard.py --
a self-check outside CI eventually reports failure to nobody, for days.

Includes MUTATION cases: each asserts that breaking the intended mechanism
actually silences the note, so a pass proves the regex is what fired it rather
than something incidental. And NEGATIVE cases, because the cost of a false
positive here is not a wasted glance -- it is an advisory nobody reads, which is
the failure mode the once-per-session dedup already exists to avoid.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude/hooks/guard-shell-hygiene.py"

# The hook dedups per (session_id, rule) via a /tmp marker that OUTLIVES the
# process, so hardcoded session ids would pass once and report phantom failures
# on every re-run. Every id below is namespaced by a fresh nonce.
RUN = uuid.uuid4().hex[:8]

PASS: list[str] = []
FAIL: list[str] = []


def run(command: str, *, session: str, hook: Path = HOOK, tool: str = "Bash") -> str:
    payload = {
        "tool_name": tool,
        "tool_input": {"command": command},
        "session_id": f"{RUN}-{session}",
    }
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return f"NONZERO_EXIT({proc.returncode})"
    out = proc.stdout.strip()
    if not out:
        return ""
    try:
        return str(json.loads(out)["hookSpecificOutput"]["additionalContext"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return f"UNPARSEABLE({out[:80]})"


def check(
    name: str, got: str, *, must_contain: str = "", must_be_silent: bool = False
) -> None:
    if must_be_silent:
        ok = got == ""
        detail = f"expected silence, got {got[:90]!r}"
    else:
        ok = must_contain in got and got != ""
        detail = f"expected {must_contain!r} in {got[:120]!r}"
    (PASS if ok else FAIL).append(name)
    print(
        f"  {'ok  ' if ok else 'FAIL'}  {name}"
        + ("" if ok else f"\n          {detail}")
    )


print("guard-shell-hygiene")

# --- 1. rule 1: the hand-rolled waiter --------------------------------------
check(
    "until+pgrep fires",
    run(
        "until ! pgrep -f clone_preflight >/dev/null; do sleep 10; done; tail -8 x.log",
        session="w1",
    ),
    must_contain="hand-rolled waiter",
)
check(
    "while+pgrep fires too",
    run(
        'while pgrep -f "pytest.*regression" >/dev/null; do sleep 5; done', session="w2"
    ),
    must_contain="hand-rolled waiter",
)
check(
    "pidof counts as a process probe",
    run("until ! pidof python3; do sleep 1; done", session="w3"),
    must_contain="hand-rolled waiter",
)
check(
    "an unslept waiter is the same defect",
    run("until ! pgrep -f make; do :; done", session="w4"),
    must_contain="hand-rolled waiter",
)

# --- 2. rule 2: a gate piped into a truncating reader ------------------------
check(
    "make preflight | tail fires",
    run("make preflight 2>&1 | tail -8", session="p1"),
    must_contain="exits with TAIL's status",
)
check(
    "make typecheck | tail fires",
    run("make typecheck 2>&1 | tail -3", session="p2"),
    must_contain="exits with TAIL's status",
)
check(
    "daily_check.py | tail fires -- the run that started this",
    run("python3 docs/plans/daily_check.py 2>&1 | tail -100", session="p3"),
    must_contain="exits with TAIL's status",
)
check(
    "a bare pytest piped to tail fires",
    run("poetry run pytest tests/ -q | tail -4", session="p4"),
    must_contain="exits with TAIL's status",
)
check(
    "head truncates just as tail does",
    run("make sanity-checks | head -20", session="p5"),
    must_contain="exits with TAIL's status",
)

# --- 3. NEGATIVE cases -- a false positive costs the whole hook its reader ---
check(
    "a non-gate piped to tail is silent",
    run("git log --oneline | tail -5", session="n1"),
    must_be_silent=True,
)
check(
    "ls piped to head is silent",
    run("ls -lt docs/plans/external-context/ | head", session="n2"),
    must_be_silent=True,
)
check(
    "the RECOMMENDED form is silent -- redirect, then read the file",
    run(
        'make preflight > /tmp/pf.log 2>&1; echo "exit=$?"; tail -8 /tmp/pf.log',
        session="n3",
    ),
    must_be_silent=True,
)
check(
    "a gate with no pipe is silent",
    run("make preflight", session="n4"),
    must_be_silent=True,
)
check(
    "pgrep on its own is not a waiter",
    run("pgrep -af clone_preflight", session="n5"),
    must_be_silent=True,
)
check(
    "a non-Bash tool is ignored",
    run("until ! pgrep -f x; do sleep 1; done", session="n6", tool="Edit"),
    must_be_silent=True,
)

check(
    "a heredoc BODY mentioning the pattern is data, not a command",
    run(
        "git commit -q -F - <<'MSG'\n"
        "feat: hook the waiter rule\n\n"
        "RULE 1, hand-rolled waiter (`until ... pgrep`): polling is wasted.\n"
        "Also never pipe `make preflight | tail -8`.\n"
        "MSG",
        session="h1",
    ),
    must_be_silent=True,
)
check(
    "...but a REAL waiter after a heredoc still fires",
    run(
        "cat <<'EOF' > /tmp/x\nsome text\nEOF\nuntil ! pgrep -f make; do sleep 1; done",
        session="h2",
    ),
    must_contain="hand-rolled waiter",
)
check(
    "...and a waiter on line 3 of a multi-line script still fires (head -1 would miss it)",
    run(
        "cd /repo\necho starting\nuntil ! pgrep -f make; do sleep 1; done", session="h3"
    ),
    must_contain="hand-rolled waiter",
)

# --- 4. dedup: once per RULE per session, and per rule INDEPENDENTLY ---------
check(
    "dedup: first utterance speaks",
    run("make preflight | tail -1", session="d1"),
    must_contain="TAIL",
)
check(
    "dedup: second identical command is silent",
    run("make preflight | tail -1", session="d1"),
    must_be_silent=True,
)
check(
    "dedup: a DIFFERENT gate on the same rule is also silent",
    run("make typecheck | tail -1", session="d1"),
    must_be_silent=True,
)
check(
    "dedup: the OTHER rule still speaks in that same session",
    run("until ! pgrep -f make; do sleep 1; done", session="d1"),
    must_contain="hand-rolled waiter",
)
check(
    "dedup: a fresh session speaks again",
    run("make preflight | tail -1", session="d2"),
    must_contain="TAIL",
)

# --- 5. both rules in one command -------------------------------------------
_both = run(
    "until ! pgrep -f x; do sleep 1; done; make preflight | tail -2", session="b1"
)
check("both rules fire together", _both, must_contain="hand-rolled waiter")
check("both rules fire together (second half)", _both, must_contain="TAIL")

# --- 6. fail-open ------------------------------------------------------------
_proc = subprocess.run(
    [sys.executable, str(HOOK)], input="not json at all", capture_output=True, text=True
)
check(
    "malformed stdin fails OPEN and silent",
    ""
    if (_proc.returncode == 0 and not _proc.stdout.strip())
    else f"rc={_proc.returncode}",
    must_be_silent=True,
)

# --- 7. MUTATION: prove the regexes are what fired ---------------------------
import shutil  # noqa: E402
import tempfile  # noqa: E402

_mut = Path(tempfile.mkdtemp(prefix="shell-hygiene-mut-"))
try:
    # Drop `pgrep` from the waiter pattern -> rule 1 must go silent, rule 2 must not.
    _m1 = _mut / "no-pgrep.py"
    _m1.write_text(
        HOOK.read_text().replace(r"\b(?:pgrep|pidof)\b", r"\b(?:__never__)\b")
    )
    check(
        "MUTATION: waiter regex without pgrep -> silent",
        run("until ! pgrep -f x; do sleep 1; done", session="m1", hook=_m1),
        must_be_silent=True,
    )
    check(
        "MUTATION: ...and the piped-gate rule is UNAFFECTED (scoped, not blanket)",
        run("make preflight | tail -1", session="m2", hook=_m1),
        must_contain="TAIL",
    )

    # Drop the truncator -> rule 2 must go silent, rule 1 must not.
    _m2 = _mut / "no-truncator.py"
    _m2.write_text(
        HOOK.read_text().replace(
            r'_TRUNCATOR = r"(?:tail|head)\b"', '_TRUNCATOR = r"(?:__never__)\\b"'
        )
    )
    check(
        "MUTATION: piped-gate regex without tail/head -> silent",
        run("make preflight | tail -1", session="m3", hook=_m2),
        must_be_silent=True,
    )
    check(
        "MUTATION: ...and the waiter rule is UNAFFECTED",
        run("until ! pgrep -f x; do sleep 1; done", session="m4", hook=_m2),
        must_contain="hand-rolled waiter",
    )
finally:
    shutil.rmtree(_mut, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL  {f}")
sys.exit(1 if FAIL else 0)
