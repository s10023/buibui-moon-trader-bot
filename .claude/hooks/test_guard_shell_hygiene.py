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

import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
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

# --- 5b. rule 3: a DUPLICATE waiter on a target already being waited on ------
# This rule cannot be a regex over the command: the waiter it catches is the
# SANCTIONED one (`make wait-ci PR=743`), which walks straight through rule 1.
# It has to probe for a LIVE process, so these cases start real ones.


@contextlib.contextmanager
def live_waiter(args: str) -> Iterator[None]:
    """Run a REAL process whose cmdline looks like a live `wait_ci.py` run.

    No injection seam in the hook: it shells out to pgrep for real, and these
    cases give it something real to find. The readiness poll is not a
    hand-rolled waiter for a background JOB -- it is a fixture confirming its
    own precondition, and it FAILS the suite rather than passing blind.
    """
    workdir = Path(tempfile.mkdtemp(prefix="hygiene-waiter-"))
    script = workdir / "wait_ci.py"
    script.write_text("import time\ntime.sleep(120)\n")
    proc = subprocess.Popen(
        [sys.executable, str(script), *args.split()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            seen = subprocess.run(
                ["pgrep", "-f", f"wait_ci.py.*{args.split()[-1]}"],
                capture_output=True,
                text=True,
            )
            if str(proc.pid) in seen.stdout.split():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError(f"fixture never became visible to pgrep: {args}")
        yield
    finally:
        proc.kill()
        proc.wait()
        shutil.rmtree(workdir, ignore_errors=True)


@contextlib.contextmanager
def mention_only(text: str) -> Iterator[None]:
    """Hold a process whose argv MENTIONS a waiter without being one.

    `sh -c "<one command>"` EXECS that command, replacing its own argv -- a
    fixture written that way reproduces nothing and its case passes vacuously
    (this one did, on the first draft). Two commands keep the shell alive, and
    the precondition is ASSERTED rather than assumed.
    """
    proc = subprocess.Popen(
        ["sh", "-c", f"sleep 120; true  # {text}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            seen = subprocess.run(
                ["pgrep", "-f", "wait_ci.py"], capture_output=True, text=True
            )
            if str(proc.pid) in seen.stdout.split():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError(f"mention fixture never became visible: {text}")
        yield
    finally:
        proc.kill()
        proc.wait()


if shutil.which("pgrep") is None:
    check("SKIPPED: no pgrep on this platform", "", must_be_silent=True)
else:
    with live_waiter("--pr 743"):
        check(
            "a second `make wait-ci PR=743` while one is live fires",
            run("make wait-ci PR=743", session="dw1"),
            must_contain="already has a live waiter",
        )
        check(
            "...and it names the target",
            run("make wait-ci PR=743", session="dw2"),
            must_contain="PR #743",
        )
        check(
            "the direct script form is caught too",
            run(
                "PYTHONPATH=. poetry run python tools/wait_ci.py --pr 743",
                session="dw3",
            ),
            must_contain="already has a live waiter",
        )
        check(
            "a DIFFERENT PR is silent -- scoped to the target, not to waiters",
            run("make wait-ci PR=744", session="dw4"),
            must_be_silent=True,
        )
        check(
            "a main-branch waiter is a different target -- silent",
            run("make wait-ci-main", session="dw5"),
            must_be_silent=True,
        )
        check(
            "a heredoc BODY naming the live target is data, not a launch",
            run(
                "git commit -q -F - <<'MSG'\n"
                "docs: record that make wait-ci PR=743 was run twice\n"
                "MSG",
                session="dw6",
            ),
            must_be_silent=True,
        )
        check(
            "dedup: the same target speaks only once per session",
            run("make wait-ci PR=743", session="dw1"),
            must_be_silent=True,
        )

    with live_waiter("--branch main --min-jobs 5"):
        check(
            "a second `make wait-ci-main` while one is live fires",
            run("make wait-ci-main", session="dw7"),
            must_contain="branch main",
        )
        check(
            "a PR waiter is silent while only a branch waiter is live",
            run("make wait-ci PR=743", session="dw8"),
            must_be_silent=True,
        )

    # REGRESSION -- caught by this suite while it was being written, and it is
    # a live-fire false positive rather than a test artifact. `pgrep -f` matches
    # the whole command line, and a shell's argv CONTAINS the command it was
    # given, so an unanchored probe read any command that merely MENTIONS the
    # target as a waiter running on it.
    with mention_only("tools/wait_ci.py --pr 4242"):
        check(
            "a shell that merely MENTIONS the target is not a live waiter",
            run("make wait-ci PR=4242", session="dw12"),
            must_be_silent=True,
        )

    # Dedup is keyed per TARGET, not per session: a duplicate on a SECOND
    # target must still speak. Rule 3 only ever fires on a real live clash, so
    # the once-per-session policy that keeps rules 1-2 readable would here just
    # hide the second occurrence of a rare, specific mistake.
    with live_waiter("--pr 999"):
        check(
            "dedup: a SECOND target still speaks in a session that already fired",
            run("make wait-ci PR=999", session="dw1"),
            must_contain="PR #999",
        )

# no live waiter at all -> silence, whatever the command looks like
check(
    "the FIRST waiter on a target is silent -- this rule flags duplicates only",
    run("make wait-ci PR=31337", session="dw9"),
    must_be_silent=True,
)
check(
    "wait-ci-main with nothing live is silent",
    run("make wait-ci-main", session="dw10"),
    must_be_silent=True,
)
check(
    "a non-waiter command carrying a PR number is silent",
    run("gh pr view 743 --json state", session="dw11"),
    must_be_silent=True,
)

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
    # Rule 3's mutations must prove SCOPE, not merely reach: a rule that fires
    # on any live wait_ci.py would pass every positive case above while flagging
    # every honest first waiter -- the one failure this hook cannot afford.
    if shutil.which("pgrep") is not None:
        # The trailing digit class is what stops `--pr 743` matching a live
        # `--pr 7431`. Unmutated: silent. Mutated: fires. That is the boundary
        # doing the work, not the PR number appearing somewhere in the cmdline.
        _m3 = _mut / "no-digit-boundary.py"
        _m3.write_text(
            HOOK.read_text().replace(r"--pr[ =]{n}([^0-9]|$)", r"--pr[ =]{n}")
        )
        with live_waiter("--pr 7431"):
            check(
                "a live --pr 7431 does NOT make --pr 743 a duplicate",
                run("make wait-ci PR=743", session="m5"),
                must_be_silent=True,
            )
            check(
                "MUTATION: drop the digit boundary -> 743 now collides with 7431",
                run("make wait-ci PR=743", session="m6", hook=_m3),
                must_contain="already has a live waiter",
            )

        # ...and the anchor is what suppresses a mere mention. Drop it and the
        # regression case above comes straight back, which is the difference
        # between a probe for a PROCESS and a probe for a STRING.
        _m5 = _mut / "unanchored.py"
        _m5.write_text(
            HOOK.read_text().replace(
                r'_RUNNING_SCRIPT = r"^[^ ]*python[0-9.]*[ ][^ ]*wait_ci\.py[ ]"',
                '_RUNNING_SCRIPT = r"wait_ci\\.py"',
            )
        )
        with mention_only("tools/wait_ci.py --pr 4242"):
            check(
                "MUTATION: unanchor the probe -> a mere MENTION reads as a waiter",
                run("make wait-ci PR=4242", session="m10", hook=_m5),
                must_contain="already has a live waiter",
            )

        # Break PR target extraction -> the PR clash goes silent while the
        # BRANCH clash is untouched, so the two targets are independent rather
        # than one blanket "a waiter is running" match.
        _m4 = _mut / "no-pr-target.py"
        _m4.write_text(
            HOOK.read_text().replace(
                r'_WAITER_PR = re.compile(r"(?:\bPR\s*=\s*|--pr[ =])(\d+)")',
                '_WAITER_PR = re.compile(r"(?:__never__)(\\d+)")',
            )
        )
        with live_waiter("--pr 743"):
            check(
                "MUTATION: PR target extraction removed -> the PR clash is silent",
                run("make wait-ci PR=743", session="m7", hook=_m4),
                must_be_silent=True,
            )
            check(
                "MUTATION: ...and rules 1-2 are UNAFFECTED (scoped, not blanket)",
                run("make preflight | tail -1", session="m8", hook=_m4),
                must_contain="TAIL",
            )
        with live_waiter("--branch main --min-jobs 5"):
            check(
                "MUTATION: ...and the BRANCH clash still fires (targets are independent)",
                run("make wait-ci-main", session="m9", hook=_m4),
                must_contain="branch main",
            )
finally:
    shutil.rmtree(_mut, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL  {f}")
sys.exit(1 if FAIL else 0)
