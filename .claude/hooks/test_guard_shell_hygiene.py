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
import os
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
SKIP: list[str] = []

# Rules 3 and 6 probe LIVE processes, so their cases need the same host
# facilities the hook does: rule 3 shells out to `pgrep`, rule 6 also reads
# `/proc/<pid>/cwd`. A Windows host has neither, and there the hook stays silent
# by design -- so these cases cannot run, and must not be counted as passing.
_HAS_PGREP = shutil.which("pgrep") is not None
_HAS_PROC_CWD = Path("/proc/self/cwd").exists()


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


def run_edit(path: str, *, session: str, hook: Path = HOOK, tool: str = "Edit") -> str:
    """Rule 6's payload shape: an edit tool carries a file_path, never a command.

    CLAUDE_PROJECT_DIR is set because the harness sets it -- settings.json invokes
    every hook through `$CLAUDE_PROJECT_DIR`. Without it the hook falls back to
    walking up from its own `__file__` for a `.git`, which succeeds for the real
    hook and FAILS for a mutation copy in /tmp: both rule-6 mutations returned
    silence for that reason and passed as if the mechanism were intact. A fixture
    that cannot reach the code it mutates proves nothing.
    """
    payload = {
        "tool_name": tool,
        "tool_input": {"file_path": path},
        "session_id": f"{RUN}-{session}",
    }
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO)},
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


def skip(block: str, reason: str) -> None:
    """Record a whole block that could not RUN -- never as a pass.

    Until ST144 the rule-3 skip went through check(), so a host without `pgrep`
    reported `N passed, 0 failed` while rules 3 and 6 were never exercised, and
    rule 6's unguarded fixture then crashed the suite at check 45 of 82,
    discarding every mutation case after it. A SKIP is not a PASS.
    """
    SKIP.append(f"{block} ({reason})")
    print(f"  skip  {block} -- {reason}")


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
    must_contain="exit status is SWALLOWED",
)
check(
    "make typecheck | tail fires",
    run("make typecheck 2>&1 | tail -3", session="p2"),
    must_contain="exit status is SWALLOWED",
)
check(
    "daily_check.py | tail fires -- the run that started this",
    run("python3 docs/plans/daily_check.py 2>&1 | tail -100", session="p3"),
    must_contain="exit status is SWALLOWED",
)
check(
    "a bare pytest piped to tail fires",
    run("poetry run pytest tests/ -q | tail -4", session="p4"),
    must_contain="exit status is SWALLOWED",
)
check(
    '; echo "EXIT=$?" fires -- sighting 4, 2026-09-22',
    run('make preflight > f 2>&1; echo "EXIT=$?"', session="p6"),
    must_contain="exit status is SWALLOWED",
)
check(
    "a trailing `; tail` fires too -- sighting 5, the same day",
    run("make preflight > f 2>&1; rc=$?; tail -20 f", session="p7"),
    must_contain="exit status is SWALLOWED",
)
check(
    "any always-succeeds trailer counts, not just echo/tail",
    run("poetry run pytest tests/ -q > f; cat f", session="p8"),
    must_contain="exit status is SWALLOWED",
)
# The distinction the `[^\n;]*$` tail pins: an always-ok command MID-chain is
# fine when the status is preserved and exited with. Dropping that anchor makes
# the hook flag the very form it recommends, which is worse than the gap.
check(
    "an always-ok command mid-chain is silent when `exit $rc` is last",
    run('make test > f 2>&1; rc=$?; echo "rc=$rc" >> f; exit $rc', session="n7"),
    must_be_silent=True,
)
check(
    "a non-gate with a trailing echo is silent",
    run("git status --short; echo done", session="n8"),
    must_be_silent=True,
)
check(
    "head truncates just as tail does",
    run("make sanity-checks | head -20", session="p5"),
    must_contain="exit status is SWALLOWED",
)
# #856: the pipe half is scoped to the gate's OWN segment. Unscoped, it crossed a
# `;` into a later segment and flagged a status that was captured and re-raised.
check(
    "a pipe AFTER the status is captured is silent (#856)",
    run(
        "make post-branch-checks > f 2>&1; rc=$?; grep -v x f | head -80; exit $rc",
        session="n9",
    ),
    must_be_silent=True,
)
check(
    "...the Issue's own example is silent too",
    run("make test > f 2>&1; rc=$?; grep x f | head; exit $rc", session="n10"),
    must_be_silent=True,
)
# Positive control for that scoping: when the LAST segment is a pipeline into a
# truncator, the shell's status is the truncator's and the gate's is lost.
check(
    "a trailing pipeline into tail swallows the status",
    run("make preflight > f 2>&1; grep -v x f | tail -3", session="p9"),
    must_contain="exit status is SWALLOWED",
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
# THE FLIP, 2026-09-22. This was a NEGATIVE case asserting the hook's own
# remediation string was fine -- and that string ends in `tail`, so the compound
# exits 0 and the gate's failure is lost. A test pinned the defect as correct,
# which is why no gate ever caught it (same shape as ST39). It is now a POSITIVE.
check(
    "the OLD recommended form FIRES -- it ended in tail and swallowed the status",
    run(
        'make preflight > /tmp/pf.log 2>&1; echo "exit=$?"; tail -8 /tmp/pf.log',
        session="n3",
    ),
    must_contain="exit status is SWALLOWED",
)
check(
    "the CORRECTED recommended form is silent -- rc captured, exit $rc last",
    run(
        "make preflight > /tmp/pf.log 2>&1; rc=$?; tail -8 /tmp/pf.log; exit $rc",
        session="n3b",
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
    must_contain="SWALLOWED",
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
    must_contain="SWALLOWED",
)

# --- 5. both rules in one command -------------------------------------------
_both = run(
    "until ! pgrep -f x; do sleep 1; done; make preflight | tail -2", session="b1"
)
check("both rules fire together", _both, must_contain="hand-rolled waiter")
check("both rules fire together (second half)", _both, must_contain="SWALLOWED")

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
    script.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
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
def mention_only(text: str, probe: str = "wait_ci.py") -> Iterator[None]:
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
                ["pgrep", "-f", probe], capture_output=True, text=True
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


if not _HAS_PGREP:
    skip("rule 3 live-waiter cases", "no pgrep on this host")
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

# --- 5c. rule 4: more than one card in one exec ------------------------------
check(
    "&&-chained cards fire",
    run(
        "make buibui-card SYMBOL=BTCUSDT && make buibui-card SYMBOL=ETHUSDT",
        session="c1",
    ),
    must_contain="more than one card",
)
check(
    "a `;` chain is the SAME defect -- the rule is the class, not the `&&`",
    run("buibui card BTCUSDT; buibui card ETHUSDT", session="c2"),
    must_contain="more than one card",
)
check(
    "env prefixes and an interpreter do not hide the second card",
    run(
        "TG=1 poetry run python buibui.py card BTC "
        "&& DRY=1 poetry run python buibui.py card ETH",
        session="c3",
    ),
    must_contain="more than one card",
)
check(
    "NEGATIVE: ONE card is the sanctioned shape and stays silent",
    run("make buibui-card SYMBOL=BTCUSDT TG=1", session="c4"),
    must_be_silent=True,
)
check(
    "NEGATIVE: a heredoc naming two cards is data, not two runs",
    run(
        "git commit -F - <<'MSG'\n"
        "docs: never chain make buibui-card A && make buibui-card B\n"
        "MSG",
        session="c5",
    ),
    must_be_silent=True,
)

# --- 5d. rule 5: gh auth switch ----------------------------------------------
check(
    "gh auth switch fires",
    run("gh auth switch", session="g1"),
    must_contain="gh auth switch",
)
check(
    "...also mid-chain, where a command can start",
    run("git push && gh auth switch --user someone", session="g2"),
    must_contain="gh auth switch",
)
check(
    "NEGATIVE: `gh auth token` is the SANCTIONED reader and must stay silent",
    run(
        "GH_TOKEN=$(gh auth token --user s10023) gh repo view "
        "s10023/buibui-moon-trader-bot --json visibility",
        session="g3",
    ),
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

# --- 6b. rule 6: editing the Python tree while a SUITE is live ----------------


_script_dir = Path(tempfile.mkdtemp(prefix="hygiene-suite-bin-"))


@contextlib.contextmanager
def live_suite(workdir: Path) -> Iterator[str]:
    """A REAL process whose cmdline and cwd look like a running pytest.

    No injection seam: the hook shells out to pgrep and reads /proc for real. The
    cmdline is `<python> <dir>/pytest ...`, which is the shape `poetry run pytest`
    actually produces (verified live: `<venv>/bin/python <venv>/bin/pytest ...`),
    and `cwd` is what separates a working-tree run from a preflight clone.

    Yields the pid, and ASSERTS its own precondition rather than passing blind.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    _script_dir.mkdir(parents=True, exist_ok=True)
    # The script lives OUTSIDE the cwd on purpose: the working-tree case runs with
    # cwd=REPO, and a fixture that wrote its fake `pytest` there would be dropping
    # a file into the repo under test.
    fake = _script_dir / "pytest"
    fake.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(fake), "tests/", "-q"],
        cwd=str(workdir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            seen = subprocess.run(
                ["pgrep", "-f", f"{fake}"], capture_output=True, text=True
            )
            if str(proc.pid) in seen.stdout.split():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError(f"suite fixture never became visible: {workdir}")
        yield str(proc.pid)
    finally:
        proc.kill()
        proc.wait()


_RULE6_MISSING = ", ".join(
    m
    for m, have in (("no pgrep", _HAS_PGREP), ("no /proc/<pid>/cwd", _HAS_PROC_CWD))
    if not have
)
if _RULE6_MISSING:
    # "no suite running -> silent" is skipped too: on such a host the hook is
    # silent whatever runs, so that case would pass without testing anything.
    skip("rule 6 live-suite cases", f"{_RULE6_MISSING} on this host")
    shutil.rmtree(_script_dir, ignore_errors=True)
else:
    _suite_tmp = Path(tempfile.mkdtemp(prefix="hygiene-suite-"))
    try:
        # The working-tree case: cwd IS the repo, which is what `make test` produces.
        with live_suite(REPO):
            check(
                "editing a .py while a working-tree suite runs FIRES",
                run_edit(str(REPO / "analytics" / "whatever.py"), session="s1"),
                must_contain="SUITE IS LIVE",
            )
            check(
                "...and Write is covered too, not just Edit",
                run_edit(
                    str(REPO / "tools" / "whatever.py"), session="s2", tool="Write"
                ),
                must_contain="SUITE IS LIVE",
            )
            check(
                "a .md edit during the same run stays SILENT (AGENTS.md's safe overlap)",
                run_edit(str(REPO / "AGENTS.md"), session="s3"),
                must_be_silent=True,
            )
            check(
                "a .py OUTSIDE the repo stays silent -- pytest cannot import it",
                run_edit("/tmp/not-in-the-repo.py", session="s4"),
                must_be_silent=True,
            )
        # THE EXEMPTION. Identical argv, cwd in a preflight clone -> must stay silent.
        _clone_cwd = _suite_tmp / "clone-preflight-xyz" / "clone"
        with live_suite(_clone_cwd):
            check(
                "a suite running in a PREFLIGHT CLONE does not fire (the exemption)",
                run_edit(str(REPO / "analytics" / "whatever.py"), session="s5"),
                must_be_silent=True,
            )

        # The argv trap rule 3 documents, re-confirmed for this rule.
        with mention_only("poetry run pytest tests/ -q", probe="poetry run pytest"):
            check(
                "a shell merely MENTIONING pytest is not a live suite",
                run_edit(str(REPO / "analytics" / "whatever.py"), session="s6"),
                must_be_silent=True,
            )

        check(
            "no suite running -> silent",
            run_edit(str(REPO / "analytics" / "whatever.py"), session="s7"),
            must_be_silent=True,
        )
    finally:
        shutil.rmtree(_suite_tmp, ignore_errors=True)
        shutil.rmtree(_script_dir, ignore_errors=True)


# --- 7. MUTATION: prove the regexes are what fired ---------------------------
_mut = Path(tempfile.mkdtemp(prefix="shell-hygiene-mut-"))
try:
    # Drop `pgrep` from the waiter pattern -> rule 1 must go silent, rule 2 must not.
    _m1 = _mut / "no-pgrep.py"
    _m1.write_text(
        HOOK.read_text(encoding="utf-8").replace(
            r"\b(?:pgrep|pidof)\b", r"\b(?:__never__)\b"
        ),
        encoding="utf-8",
    )
    check(
        "MUTATION: waiter regex without pgrep -> silent",
        run("until ! pgrep -f x; do sleep 1; done", session="m1", hook=_m1),
        must_be_silent=True,
    )
    check(
        "MUTATION: ...and the piped-gate rule is UNAFFECTED (scoped, not blanket)",
        run("make preflight | tail -1", session="m2", hook=_m1),
        must_contain="SWALLOWED",
    )

    # Drop the truncator -> rule 2 must go silent, rule 1 must not.
    _m2 = _mut / "no-truncator.py"
    _m2.write_text(
        HOOK.read_text(encoding="utf-8").replace(
            r'_TRUNCATOR = r"(?:tail|head)\b"', '_TRUNCATOR = r"(?:__never__)\\b"'
        ),
        encoding="utf-8",
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
    # Drop the always-ok set -> the `;` half of rule 2 goes silent, the PIPE half
    # must not. Without the second assertion this would pass for a rule that had
    # simply stopped working, which is the failure mode mutation tests exist for.
    _m2b = _mut / "no-always-ok.py"
    _m2b.write_text(
        HOOK.read_text(encoding="utf-8").replace(
            r'_ALWAYS_OK = r"(?::|true|echo|printf|tail|head|cat|ls|wc)\b"',
            '_ALWAYS_OK = r"(?:__never__)\\b"',
        ),
        encoding="utf-8",
    )
    check(
        "MUTATION: no always-ok set -> the `; echo` swallow is silent",
        run('make preflight > f 2>&1; echo "EXIT=$?"', session="m2b", hook=_m2b),
        must_be_silent=True,
    )
    check(
        "MUTATION: ...and the PIPE half still fires (scoped, not blanket)",
        run("make preflight | tail -1", session="m2c", hook=_m2b),
        must_contain="SWALLOWED",
    )

    # #856, mutation 1: let the pipe half cross `;` again -> the captured-status
    # form fires. Proves the `;` in the class is what silences it.
    _m2d = _mut / "pipe-half-unscoped.py"
    _m2d.write_text(
        HOOK.read_text(encoding="utf-8").replace(
            r'rf"{_GATE}(?:[^\n|;]*\|\s*{_TRUNCATOR}"',
            r'rf"{_GATE}(?:[^\n|]*\|\s*{_TRUNCATOR}"',
        ),
        encoding="utf-8",
    )
    check(
        "MUTATION: pipe half crosses `;` -> a captured status FIRES (#856)",
        run(
            "make post-branch-checks > f 2>&1; rc=$?; grep -v x f | head -80; exit $rc",
            session="m2d",
            hook=_m2d,
        ),
        must_contain="SWALLOWED",
    )

    # #856, mutation 2: drop the trailing-pipeline alternative -> the last-segment
    # pipeline goes silent, while the `; echo` swallow must still fire.
    _m2e = _mut / "no-trailing-pipeline.py"
    _m2e.write_text(
        HOOK.read_text(encoding="utf-8").replace(
            r"|[^\n;|]*\|\s*{_TRUNCATOR}[^\n;]*)$)", ")$)"
        ),
        encoding="utf-8",
    )
    check(
        "MUTATION: no trailing-pipeline alternative -> `; grep | tail` is silent",
        run("make preflight > f 2>&1; grep -v x f | tail -3", session="m2e", hook=_m2e),
        must_be_silent=True,
    )
    check(
        "MUTATION: ...and the `; echo` swallow still fires (scoped, not blanket)",
        run('make preflight > f 2>&1; echo "EXIT=$?"', session="m2f", hook=_m2e),
        must_contain="SWALLOWED",
    )

    # Rule 3's mutations must prove SCOPE, not merely reach: a rule that fires
    # on any live wait_ci.py would pass every positive case above while flagging
    # every honest first waiter -- the one failure this hook cannot afford.
    if not _HAS_PGREP:
        skip("rule 3 mutation cases", "no pgrep on this host")
    else:
        # The trailing digit class is what stops `--pr 743` matching a live
        # `--pr 7431`. Unmutated: silent. Mutated: fires. That is the boundary
        # doing the work, not the PR number appearing somewhere in the cmdline.
        _m3 = _mut / "no-digit-boundary.py"
        _m3.write_text(
            HOOK.read_text(encoding="utf-8").replace(
                r"--pr[ =]{n}([^0-9]|$)", r"--pr[ =]{n}"
            ),
            encoding="utf-8",
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
            HOOK.read_text(encoding="utf-8").replace(
                r'_RUNNING_SCRIPT = r"^[^ ]*python[0-9.]*[ ][^ ]*wait_ci\.py[ ]"',
                '_RUNNING_SCRIPT = r"wait_ci\\.py"',
            ),
            encoding="utf-8",
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
            HOOK.read_text(encoding="utf-8").replace(
                r'_WAITER_PR = re.compile(r"(?:\bPR\s*=\s*|--pr[ =])(\d+)")',
                '_WAITER_PR = re.compile(r"(?:__never__)(\\d+)")',
            ),
            encoding="utf-8",
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
                must_contain="SWALLOWED",
            )
        with live_waiter("--branch main --min-jobs 5"):
            check(
                "MUTATION: ...and the BRANCH clash still fires (targets are independent)",
                run("make wait-ci-main", session="m9", hook=_m4),
                must_contain="branch main",
            )

    _m5 = _mut / "no_card.py"
    _m5.write_text(
        HOOK.read_text(encoding="utf-8").replace(
            r"(?:make\s+buibui-card\b|(?:\./)?buibui(?:\.py)?\s+card\b)",
            r"(?:__never__)",
        ),
        encoding="utf-8",
    )
    check(
        "MUTATION: card invocation unrecognised -> the chain note is silent",
        run(
            "make buibui-card SYMBOL=BTCUSDT && make buibui-card SYMBOL=ETHUSDT",
            session="m10",
            hook=_m5,
        ),
        must_be_silent=True,
    )
    check(
        "MUTATION: ...and gh auth switch is UNAFFECTED (scoped, not blanket)",
        run("gh auth switch", session="m11", hook=_m5),
        must_contain="gh auth switch",
    )

    # The anchor is the half that keeps rule 5 from reading its own documentation
    # as a command -- the defect that made guard-destructive.py block its own
    # commit message. Drop it and prose about the rule trips the rule.
    _m6 = _mut / "unanchored.py"
    _m6.write_text(
        HOOK.read_text(encoding="utf-8").replace(
            'rf"{_CMD_START}gh\\s+auth\\s+switch\\b"',
            'r"gh\\s+auth\\s+switch\\b"',
        ),
        encoding="utf-8",
    )
    check(
        "MUTATION: unanchored rule 5 fires on prose that merely NAMES the command",
        run(
            "echo 'never run gh auth switch, export GH_TOKEN instead'",
            session="m12",
            hook=_m6,
        ),
        must_contain="gh auth switch",
    )
    check(
        "...which the ANCHORED rule correctly stays silent on",
        run("echo 'never run gh auth switch, export GH_TOKEN instead'", session="m13"),
        must_be_silent=True,
    )

    if _RULE6_MISSING:
        skip("rule 6 mutation cases", f"{_RULE6_MISSING} on this host")
    else:
        # Rule 6, mutation 1: kill the cwd discriminator -> the PREFLIGHT CLONE,
        # which the real hook exempts, must now fire. This is the case that proves
        # the exemption is the cwd read and not something incidental about the
        # fixture.
        _m7 = _mut / "no-cwd-check.py"
        _m7.write_text(
            HOOK.read_text(encoding="utf-8").replace(
                'cwd = Path(os.readlink(f"/proc/{pid}/cwd"))', "cwd = Path(str(root))"
            ),
            encoding="utf-8",
        )
        _mut_clone = Path(tempfile.mkdtemp(prefix="hygiene-mutclone-"))
        try:
            _cwd = _mut_clone / "clone-preflight-abc" / "clone"
            with live_suite(_cwd):
                check(
                    "MUTATION: without the cwd read, a CLONE suite fires",
                    run_edit(str(REPO / "analytics" / "x.py"), session="m14", hook=_m7),
                    must_contain="SUITE IS LIVE",
                )
                check(
                    "...which the real hook correctly stays silent on",
                    run_edit(str(REPO / "analytics" / "x.py"), session="m15"),
                    must_be_silent=True,
                )
        finally:
            shutil.rmtree(_mut_clone, ignore_errors=True)
            shutil.rmtree(_script_dir, ignore_errors=True)

        # Rule 6, mutation 2: unanchor the pytest probe -> a shell that merely
        # NAMES pytest reads as a live suite, which is the trap rule 3 documents.
        _m8 = _mut / "unanchored-pytest.py"
        _m8.write_text(
            HOOK.read_text(encoding="utf-8").replace(
                r'_RUNNING_PYTEST = r"^[^ ]*python[0-9.]*[ ](-m[ ]pytest|[^ ]*/pytest)([ ]|$)"',
                '_RUNNING_PYTEST = r"pytest"',
            ),
            encoding="utf-8",
        )
        with mention_only("poetry run pytest tests/ -q", probe="poetry run pytest"):
            check(
                "MUTATION: an unanchored probe reads a MENTION as a running suite",
                run_edit(str(REPO / "analytics" / "x.py"), session="m16", hook=_m8),
                must_contain="SUITE IS LIVE",
            )
            check(
                "...which the ANCHORED probe correctly stays silent on",
                run_edit(str(REPO / "analytics" / "x.py"), session="m17"),
                must_be_silent=True,
            )
finally:
    shutil.rmtree(_mut, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed, {len(SKIP)} block(s) skipped")
for f in FAIL:
    print(f"  FAIL  {f}")
for s in SKIP:
    print(f"  skip  {s}")
# CI's runner has both probes, so a skip THERE means the runner changed under us
# and rules 3/6 went unexercised -- which must read red, never green.
_CI_SKIP = bool(SKIP) and bool(os.environ.get("CI"))
if _CI_SKIP:
    print("  FAIL  a block was SKIPPED under CI, where every probe must exist")
sys.exit(1 if FAIL or _CI_SKIP else 0)
