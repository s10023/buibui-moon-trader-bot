#!/usr/bin/env python3
"""PreToolUse advisory hook — two shell habits that silently fake a verified result.

Self-authored (no third-party dependency) so every rule is reviewable here, and
stdlib-only so CI's dependency-free job can run its suite. Wired via
.claude/settings.json -> hooks.PreToolUse (matcher "Bash"), beside
guard-destructive.py. **Advisory, never blocking** — both patterns have honest
uses, and the failure this guards is a wrong belief rather than a wrong command.

Protocol: Claude Code pipes {"tool_name","tool_input":{"command":...},"session_id":...}
on stdin. Exit 0 always; the note rides `hookSpecificOutput.additionalContext`,
the same channel context-guard.py uses.

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

RULE 2 — a gate piped into a truncating reader (`make preflight | tail -8`).
    A pipeline exits with the status of its LAST command, so `tail` returns 0 and
    the gate's failure disappears. `AGENTS.md` documents exactly this for
    `wait_ci.py` and `make preflight` ("read the printed banner"), but states it
    about `make` swallowing exit codes rather than about the pipeline the session
    itself writes. In one session this masked three: a `daily_check` run reported
    as exit 0 while three tier-1 legs had not run, a `make typecheck` failure that
    let `&&` proceed anyway, and a `make preflight` whose result was lost entirely.

Each rule speaks ONCE PER SESSION. A hook that fires on every occurrence trains
its reader to skip it — the same failure the repo's always-red tier-2 line and
its always-amber monitor workflow already demonstrate.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
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
        rf"{_GATE}[^\n|]*\|\s*{_TRUNCATOR}",
        "a gate piped into tail/head. The pipeline exits with TAIL's status, so "
        "the gate's failure is masked and a red run reads as green -- the same "
        "class AGENTS.md documents for wait_ci.py and preflight through `make`. "
        "Redirect instead, then read the file: "
        '`make <gate> > /tmp/<name>.log 2>&1; echo "exit=$?"; tail -8 /tmp/<name>.log`.',
    ),
]


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

    if payload.get("tool_name") != "Bash":
        return 0

    command = str(payload.get("tool_input", {}).get("command", ""))
    if not command:
        return 0

    session_id = str(payload.get("session_id", "nosession"))

    hits: list[str] = []
    for rule_id, pattern, note in RULES:
        if re.search(pattern, command) and not _already_spoken(session_id, rule_id):
            hits.append(note)

    if not hits:
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": (
                        "shell-hygiene: "
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
