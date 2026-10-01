#!/usr/bin/env python3
"""PreToolUse advisory hook — shell habits that silently cost you a result.

Self-authored (no third-party dependency) so every rule is reviewable here, and
stdlib-only so CI's dependency-free job can run its suite. Wired via
.claude/settings.json -> hooks.PreToolUse on TWO matchers: "Bash" (rules 1-5,
beside guard-destructive.py) and "Edit|Write|NotebookEdit|MultiEdit" (rule 6,
beside context-guard.py). **Advisory, never blocking** — every pattern here has
honest uses, and the failure this guards is a wrong belief rather than a wrong
command.

Protocol: Claude Code pipes {"tool_name","tool_input":{...},"session_id":...} on
stdin. Exit 0 always; the note rides `hookSpecificOutput.additionalContext`, the
same channel context-guard.py uses.

Both rules come from one session (2026-09-03) in which the prose already in
context did not prevent either, three times over. `docs/plans/daily_check.py`'s
own inclusion-rule comment scores prose rules in this repo **0-for-15, against
15-for-15 for the marker check**, which is the whole argument for putting these
here instead of in another paragraph.

RULE 1 — a hand-rolled waiter (`until ... pgrep`).
    `run_in_background: true` already re-invokes the session when the job exits,
    so a second shell that watches the first is pure redundancy. Worse, it is
    redundancy that decays into a false report: `pgrep -f <name>` matches a CLASS
    of process, so the NEXT run of the same kind re-arms a waiter that already
    fired, while its payload still points at the OLD output file. Measured that
    day: a waiter for one `make preflight` re-armed against a later one and would
    have printed the earlier run's result as if it were the new one. Five such
    shells accumulated in a single session; not one was a real job.

RULE 2 — a gate whose exit status is SWALLOWED, by a pipe or by a trailing
    always-succeeds command (`make preflight | tail -8`, `... > f; echo "EXIT=$?"`,
    `... > f; rc=$?; tail -20 f`).
    A pipeline exits with the status of its LAST command, so `tail` returns 0 and
    the gate's failure disappears. `AGENTS.md` documents exactly this for
    `wait_ci.py` and `make preflight` ("read the printed banner"), but states it
    about `make` swallowing exit codes rather than about the pipeline the session
    itself writes. In one session this masked three: a `daily_check` run reported
    as exit 0 while three tier-1 legs had not run, a `make typecheck` failure that
    let `&&` proceed anyway, and a `make preflight` whose result was lost entirely.

    Extended 2026-09-22 after two more sightings in one session, both written
    with this rule already in context. The decisive one: THIS NOTE'S OWN
    remediation string ended in `tail`, and a negative test pinned that exact
    command as correct -- so the guard taught the defect and a test agreed.
    A `;`-sequence swallows a status exactly as a pipeline does; scoping to
    `; echo` would have been the symptom again.

RULE 3 — a DUPLICATE waiter on a target something is already waiting on.
    The gap rule 1 leaves open, found by the operator within the hour of it
    shipping: rule 1 matches the hand-rolled shape (`until ... pgrep`), so the
    SANCTIONED waiter walks straight through it. Two `make wait-ci PR=743` runs
    were live at once that day — double the CI API calls, both re-invoking the
    session on the same event. The mutation tests could not have revealed it,
    because they only probe rules that exist; the hook had been scoped to the
    SYMPTOM noticed rather than to the class.

    So this rule cannot be a regex over the command. It extracts the waiter's
    TARGET (`--pr 743`, `--branch main`) and asks pgrep whether one is already
    live for it — the check the operator's rule states: *before launching any
    waiter, check whether one is already running for the same target*. Unlike
    rules 1-2 it speaks once per TARGET rather than once per session: it fires
    only on a real live clash, so there is no honest use to go quiet about, and
    a second clash on a second target is a second mistake.

RULE 4 — more than one `/card` in a single exec.
    `AGENTS.md` states the rule as "run one `/card` per background exec — never
    `&&`-chain them", and the reason is not tidiness: a card CRASHES on the
    15-minute signal-watch DuckDB write lock, producing no verdict and no ledger
    row, and its exposure is only the first ~3.4s of the run. Under `&&` that
    crash stops the chain, so one unlucky 3-second window silently costs every
    card after it; under `;` they run but the failure scrolls past. Either way
    the operator reads a short batch as the batch they asked for.

    Matched as a CLASS — any two card invocations in one exec, whatever joins
    them — rather than as the `&&` the prose happens to name, because `;` and a
    newline lose the same cards. Anchored where a command can START, the fix
    guard-destructive.py already carries: unanchored, a note that merely
    mentions two cards trips it.

RULE 6 — editing the Python tree while a SUITE IS LIVE (ST120(b)).
    Fires on Edit/Write rather than on Bash, which is why it is not one of the
    regex rules above: there is no command string to match. Both `make test` and
    `make preflight` run the IDENTICAL argv, so argv cannot separate them — the
    discriminator is the pytest process's **cwd**, and preflight's is the clone
    under /tmp. That makes the exemption structural rather than a name match:
    preflight tests committed state in a clone, so a working-tree edit provably
    cannot reach that run. Anchored at an interpreter actually running pytest,
    because `pgrep -f` matches the whole cmdline and a shell's argv CONTAINS the
    command it was handed — the same trap rule 3 documents, re-confirmed live
    here (a probe for "pytest" matched the shell that had merely typed it).

RULE 5 — `gh auth switch`.
    It mutates gh's GLOBAL active account. This machine's gh state is shared
    with the operator's own terminal and every other session on it, nothing
    switches it back when a turn ends, and a session cannot see whose account it
    just changed. The scoped form is one env prefix and is what AGENTS.md's own
    visibility-flip block uses: `GH_TOKEN=$(gh auth token --user s10023) gh <cmd>`.
    ⚠ `gh auth token` is the SANCTIONED reader and must not match this rule.

Rules 1, 2, 4 and 5 speak ONCE PER SESSION. A hook that fires on every occurrence trains
its reader to skip it — the same failure the repo's always-red tier-2 line and
its always-amber monitor workflow already demonstrate. Rules 3 and 6 instead
dedup per TARGET — the waiter's target, the suite's pid — because each fires
only on a real live clash, so the readability argument does not apply and a
second genuine clash must still speak.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# A gate is a command whose EXIT CODE is the answer. Piping one into a truncating
# reader throws that answer away. Scoped to the gates rather than to every `make`,
# because the note has to be worth reading the first time to survive to the second.
_GATE = (
    r"(?:make\s+(?:preflight|test|test-regression|test-cov|typecheck|lint-py|lint-md"
    r"|sanity-checks|post-branch-checks|post-branch-text|wait-ci|wait-ci-main|db-update)"
    r"|(?:poetry\s+run\s+)?(?:python3?\s+\S*)?pytest"
    r"|python3?\s+\S*(?:daily_check|wait_ci|sanity_checks|post_branch_checks)\.py)"
)
_TRUNCATOR = r"(?:tail|head)\b"
# Commands that succeed on essentially any input. Putting one LAST in a
# `;`-sequence makes the shell's status THEIRS, which is the same swallow the
# pipe form performs -- `; echo "exit=$?"` prints the code for a human to read
# and still hands the CALLER a 0. Kept deliberately tight: `grep` and `diff`
# answer the caller's own question and can legitimately fail, so they are not
# swallows and adding them would cost this hook its reader.
_ALWAYS_OK = r"(?::|true|echo|printf|tail|head|cat|ls|wc)\b"

# Anchored where a command can actually START -- the fix guard-destructive.py
# already carries, after an unanchored rule there blocked its own commit message.
# Allows leading env assignments (`TG=1 make ...`), which are part of the command.
_CMD_START = r"(?:^|[\n;&|(])\s*(?:[A-Za-z_][A-Za-z0-9_]*=[^\s]*\s+)*"
# ...and, for the card, an optional interpreter, since `buibui.py card` is
# normally reached through one.
_CARD = (
    _CMD_START + r"(?:(?:poetry\s+run\s+)?python3?\s+)?"
    r"(?:make\s+buibui-card\b|(?:\./)?buibui(?:\.py)?\s+card\b)"
)

RULES: list[tuple[str, str, str]] = [
    (
        "waiter",
        # `until`/`while` in the same command as a process probe. `sleep` is not
        # required: `until ! pgrep ...; do :; done` is the same defect unslept.
        r"\b(?:until|while)\b[^\n]*\b(?:pgrep|pidof)\b",
        "hand-rolled waiter. A background job started with run_in_background "
        "already re-invokes you when it exits -- polling it is wasted, and a "
        "`pgrep` guard names a CLASS of process, so the next run of the same "
        "kind re-arms a waiter that already fired and it reports a STALE file as "
        "the fresh result. Use the completion notification; for state the harness "
        "cannot see (CI, a deploy), use Monitor. If a hand-rolled wait is truly "
        "unavoidable, gate on THIS run's own artifact (its pid, or a sentinel it "
        "writes) and make the condition name the same file the payload reads.",
    ),
    (
        "piped-gate",
        # Pipe half: scoped to the GATE'S OWN segment, so it stops at `;` (#856).
        # Unscoped it crossed into a later segment and flagged
        # `make test > f 2>&1; rc=$?; grep x f | head; exit $rc`, where the status
        # is captured and re-raised. `;` half: the LAST segment swallows when it is
        # an always-ok command OR a pipeline into tail/head -- the second
        # alternative is the case the scoping would otherwise drop
        # (`make preflight > f; grep -v x f | tail -3`).
        rf"{_GATE}(?:[^\n|;]*\|\s*{_TRUNCATOR}"
        rf"|[\s\S]*[;\n]\s*(?:{_ALWAYS_OK}[^\n;]*|[^\n;|]*\|\s*{_TRUNCATOR}[^\n;]*)$)",
        "a gate whose exit status is SWALLOWED. A pipeline exits with its LAST "
        "command's status and a `;`-sequence with its last segment's, so `| tail`, "
        '`; echo "exit=$?"` and `; tail -8 f` all turn a red gate green -- the '
        "same class AGENTS.md documents for wait_ci.py and preflight through "
        "`make`. "
        "Capture the status first and make it the LAST word: "
        "`make <gate> > /tmp/<name>.log 2>&1; rc=$?; tail -8 /tmp/<name>.log; "
        "exit $rc`.",
    ),
    (
        "card-chain",
        # Two card invocations in ONE exec, whatever joins them.
        rf"{_CARD}[\s\S]*?{_CARD}",
        "more than one card in a single exec. A card CRASHES on the 15-minute "
        "signal-watch write lock -- no verdict, no ledger row -- and its exposure "
        "is the first ~3.4s of the run. Under `&&` that crash stops the chain, so "
        "one unlucky 3-second window silently costs every card after it; under "
        "`;` the failure just scrolls past. Either way a short batch reads as the "
        "batch you asked for. Run ONE card per background exec and let the "
        "harness re-invoke you on each, retrying the one that crashed.",
    ),
    (
        "gh-auth-switch",
        rf"{_CMD_START}gh\s+auth\s+switch\b",
        "`gh auth switch`. It mutates gh's GLOBAL active account, which this "
        "machine shares with the operator's own terminal and every other session "
        "on it, and nothing switches it back when the turn ends. Scope it to the "
        "one command instead -- the form AGENTS.md's visibility-flip block "
        "already uses: `GH_TOKEN=$(gh auth token --user s10023) gh <cmd>`.",
    ),
]


# RULE 3 is two-stage: name the TARGET a proposed waiter would wait on, then ask
# whether a process is already waiting on that same one. `make wait-ci PR=743`
# and the direct `wait_ci.py --pr 743` are the same target by construction --
# the Makefile recipe IS that script -- so both spellings are matched here and
# only the script's own cmdline is ever probed.
# ANCHORED at an interpreter actually running the script, because `pgrep -f`
# matches the whole command line and a shell's argv CONTAINS the command it was
# handed -- so an unanchored probe reads any command that merely MENTIONS the
# target as a waiter running on it. Caught by this hook's own suite, live, while
# it was being written. POSIX classes rather than `\S`, since pgrep compiles the
# pattern as an ERE and the GNU shorthands are not guaranteed there.
_RUNNING_SCRIPT = r"^[^ ]*python[0-9.]*[ ][^ ]*wait_ci\.py[ ]"
_WAITER = re.compile(r"\bwait[-_]ci\b|\bwait_ci\.py\b")
_WAITER_PR = re.compile(r"(?:\bPR\s*=\s*|--pr[ =])(\d+)")
_WAITER_BRANCH = re.compile(r"wait[-_]ci-main\b|--branch[ =](\S+)")


def _waiter_target(command: str) -> tuple[str, str] | None:
    """(human label, pgrep pattern) for the CI waiter this command would start.

    None when the command starts no waiter -- which is the common case, so this
    returns before shelling out to anything.
    """
    if not _WAITER.search(command):
        return None
    pr = _WAITER_PR.search(command)
    if pr:
        n = pr.group(1)
        # The trailing class stops `--pr 743` matching a live `--pr 7431`.
        return f"PR #{n}", rf"{_RUNNING_SCRIPT}.*--pr[ =]{n}([^0-9]|$)"
    branch = _WAITER_BRANCH.search(command)
    if branch:
        # `make wait-ci-main` names no branch on the command line; the recipe
        # supplies `--branch main`, which is what the live process shows.
        name = branch.group(1) or "main"
        return (
            f"branch {name}",
            rf"{_RUNNING_SCRIPT}.*--branch[ =]{re.escape(name)}([^A-Za-z0-9_/-]|$)",
        )
    return None


def _live_pids(pattern: str) -> list[str]:
    """PIDs already waiting on this target. Empty on any doubt -- see below.

    A false positive here would flag an honest first waiter, which is the one
    failure this hook cannot afford, so a missing or unhappy `pgrep` stays
    silent rather than guessing.
    """
    try:
        found = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [pid for pid in found.stdout.split() if pid.isdigit()]


def _duplicate_waiter_note(command: str) -> tuple[str, str] | None:
    """(dedup key, note) when a live waiter already covers this target."""
    target = _waiter_target(command)
    if target is None:
        return None
    label, pattern = target
    pids = _live_pids(pattern)
    if not pids:
        return None
    return (
        f"waiter-dup:{label}",
        f"a DUPLICATE waiter -- {label} already has a live waiter "
        f"(pid {', '.join(pids)}). Two waiters on one target double the CI API "
        "calls and both re-invoke you on the same event; the sanctioned waiter "
        "walks straight through the hand-rolled-waiter rule, which is why this "
        "one probes for a LIVE process instead of matching a shell pattern. "
        "Read the running waiter's result, or kill it before starting another.",
    )


# RULE 6 — an edit to the Python tree while a suite is LIVE.
#
# ANCHORED at an interpreter actually running pytest, for the reason rule 3
# documents and this rule re-confirmed live: `pgrep -f pytest` matched the SHELL
# that had merely typed the word. Two spellings, because the repo produces both —
# `poetry run pytest` execs the console script (`<venv>/bin/python
# <venv>/bin/pytest ...`) while a hand-run uses `python -m pytest`. POSIX classes
# rather than the GNU shorthands, since pgrep compiles this as an ERE.
_RUNNING_PYTEST = r"^[^ ]*python[0-9.]*[ ](-m[ ]pytest|[^ ]*/pytest)([ ]|$)"

WATCHED_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}


def _repo_root() -> Path | None:
    """Repo root, from the harness env var if set, else by walking up to .git."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        with contextlib.suppress(OSError):
            return Path(env).resolve()
        return None
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def _live_suite_pids(root: Path) -> list[str]:
    """PIDs running a suite against THE WORKING TREE, never against a clone.

    `make preflight` runs byte-identical argv to `make test` — verified by
    reading `tools/clone_preflight.py`, which calls `subprocess.run(pytest_argv(),
    cwd=dest)` — so argv cannot separate them and cwd is the ONLY discriminator.
    That is what makes preflight's exemption structural: its pytest sits in
    /tmp/clone-preflight-*/clone, testing committed state, so an edit to the
    working tree cannot reach it.

    Unreadable /proc stays SILENT rather than guessing, the same asymmetry
    `_live_pids` carries: a false positive here costs the hook its reader.
    """
    live: list[str] = []
    for pid in _live_pids(_RUNNING_PYTEST):
        try:
            cwd = Path(os.readlink(f"/proc/{pid}/cwd"))
        except OSError:
            continue
        if cwd == root or root in cwd.parents:
            live.append(pid)
    return live


def _edit_during_suite_note(
    tool_input: dict[str, object], root: Path | None
) -> tuple[str, str] | None:
    """(dedup key, note) when this edit lands on the Python tree mid-suite."""
    if root is None:
        return None
    targets = [
        str(tool_input.get(key, ""))
        for key in ("file_path", "notebook_path")
        if tool_input.get(key)
    ]
    inside = []
    for raw in targets:
        if not raw.endswith(".py"):
            continue  # AGENTS.md names the safe overlaps: docs, memory, a PR body
        try:
            Path(raw).resolve().relative_to(root)
        except (OSError, ValueError):
            continue  # outside the repo -- pytest cannot import it
        inside.append(raw)
    if not inside:
        return None

    pids = _live_suite_pids(root)
    if not pids:
        return None
    return (
        f"suite-live:{pids[0]}",
        f"an edit to the Python tree while a SUITE IS LIVE (pid {', '.join(pids)}). "
        "pytest imports modules at COLLECTION, so a half-saved module errors the "
        "whole run rather than just its own file -- and a green result describing "
        "a tree that no longer exists is worse than no result, because it is a "
        "false VERIFIED. Let the run finish and re-run it, or edit something "
        "AGENTS.md names as safe to overlap: MEMORY.md and the memory topic "
        "files, anything under gitignored docs/plans/, a PR body under /tmp. "
        "`make preflight` is EXEMPT by construction and never triggers this -- it "
        "tests a CLONE of committed state, so the working tree is yours "
        "throughout, which is a second reason to prefer it on a branch.",
    )


_HEREDOC = re.compile(
    r"<<-?\s*(['\"]?)(\w+)\1.*?^\s*\2\s*$",
    re.DOTALL | re.MULTILINE,
)


def _strip_heredocs(command: str) -> str:
    """Drop heredoc BODIES before matching -- they are data, not commands.

    Found the moment this hook shipped: the commit message documenting these very
    rules contained "until ... pgrep", so `git commit -F - <<'MSG' ... MSG` tripped
    rule 1 on its own changelog. The repo already knew this class -- the
    `gh pr create` reminder (advise-lifecycle.py) reads only the first line
    precisely so "a grep or heredoc merely containing the string no longer
    self-triggers".

    Stripping the body rather than keeping only the first line is the stronger form
    of that fix: a first-line read would also blind the hook to a waiter on line 3 of a
    genuine multi-line script, which is exactly where one tends to be written.
    """
    return _HEREDOC.sub("<<STRIPPED", command)


def _already_spoken(session_id: str, rule_id: str) -> bool:
    """One utterance per rule per session; True if we have spoken already.

    Keyed per RULE rather than per command, so the second distinct habit still
    speaks while a run of the same one stays quiet. Same marker convention as
    context-guard.py.
    """
    tag = hashlib.sha256(f"{session_id}:{rule_id}".encode()).hexdigest()[:16]
    marker = Path(tempfile.gettempdir()) / f"claude-shell-hygiene-{tag}"
    if marker.exists():
        return True
    # Fail open: an un-deduped note is better than a silent one.
    with contextlib.suppress(OSError):
        marker.touch()
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open -- never break the session on a parse error

    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    session_id = str(payload.get("session_id", "nosession"))

    hits: list[str] = []
    # The label names the SURFACE, since rule 6 is not a shell habit; the owning
    # file is named in the trailer either way, so the note stays traceable.
    label = "shell-hygiene"

    if tool_name in WATCHED_EDIT_TOOLS:
        label = "edit-hygiene"
        editing = _edit_during_suite_note(tool_input, _repo_root())
        if editing is not None and not _already_spoken(session_id, editing[0]):
            hits.append(editing[1])
    elif tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        if not command:
            return 0
        command = _strip_heredocs(command)

        for rule_id, pattern, note in RULES:
            if re.search(pattern, command) and not _already_spoken(session_id, rule_id):
                hits.append(note)

        duplicate = _duplicate_waiter_note(command)
        if duplicate is not None and not _already_spoken(session_id, duplicate[0]):
            hits.append(duplicate[1])
    else:
        return 0

    if not hits:
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": (
                        f"{label}: "
                        + " || ".join(hits)
                        + " -- Advisory only, never blocking; said once per rule "
                        "per session. Owned by .claude/hooks/guard-shell-hygiene.py."
                    ),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
