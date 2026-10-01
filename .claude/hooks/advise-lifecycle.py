#!/usr/bin/env python3
"""Lifecycle advisories -- PR create, PR merge, session close-out.

Advisory only: it NEVER blocks. It emits `additionalContext` and exits 0.
Wired via .claude/settings.json on three events: PreToolUse (Bash), PostToolUse
(Bash) and UserPromptSubmit. Ported from the wifey fork's PR #374.

  PreToolUse  + `gh pr create`  -> run /post-branch FIRST (the useful leg)
  PostToolUse + `gh pr create`  -> run /post-branch NOW (the catch)
  PostToolUse + `gh pr merge`   -> the post-merge chain, flip-back included
  UserPromptSubmit + "deleting sesh" -> write every memory surface, THEN archive

Why Python rather than the inline `jq | grep` one-liner it replaces (#855)
-------------------------------------------------------------------------
Both `gh pr create` reminders used to be `jq -r ... | head -1 | grep -qE ...`.
The Windows host has no `jq`, so jq failed, grep read empty input, `|| true`
swallowed the lot, and neither reminder ever fired there -- silently, the same
fail-open shape `test_hook_wiring.py` pins for the interpreter. A hook run on the
repo's own venv has no dependency the box can lack.

Head-anchored on the FIRST LINE, like the one-liner it replaces: a command merely
QUOTING `gh pr create` (a grep over AGENTS.md, a commit message, a heredoc body
below line 1) must not trip it, so the match sits at the start of the line or
after a shell separator. Quoted strings are blanked first -- a `|` inside
`grep -E "a|gh pr create"` is regex alternation, not a pipe -- which the grep
version could not do.

Protocol: Claude Code pipes {"hook_event_name", "tool_input" | "prompt", ...} on
stdin. Exit 0 always; advice is JSON on stdout.
"""

from __future__ import annotations

import json
import re
import sys

# A leading `GH_TOKEN=$(gh auth token --user s10023) ` prefix is still a head
# position -- it is how every gh call in this repo is written (AGENTS.md > CI quota).
_ENV = r"(?:[A-Za-z_][A-Za-z0-9_]*=(?:\$\([^)]*\)|\S*)\s+)*"
_SEP = r"(?:^|[;&|()]|&&)\s*" + _ENV
PR_CREATE = re.compile(_SEP + r"gh\s+pr\s+create\b")
PR_MERGE = re.compile(_SEP + r"gh\s+pr\s+merge\b")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
CLOSE_OUT = re.compile(r"\bdelet(?:e|ing)\s+(?:the\s+|this\s+)?ses", re.IGNORECASE)

# The two `gh pr create` texts are this repo's, verbatim from the one-liners they
# replace: only the MATCHING mechanism changed in #855.
PRE_CREATE = (
    "post-branch reminder: you are ABOUT TO run gh pr create. Invoke the"
    " /post-branch skill FIRST, while the branch is still local-only, so its doc"
    " fixes ship in the initial push and its Documentation-updates section goes into"
    " the PR body at creation. Steps 1-5b and 7 are commit-producing and belong"
    " HERE; only 6 and 10a/10c run after the PR exists. A hand walk is NOT the walk"
    " - run the three-source added-file check itself (committed adds, staged adds,"
    " untracked), because its whole value is catching the file you forgot existed."
    " Advisory only - never blocking. Rationale: AGENTS.md > Git Conventions;"
    " ported from wifey 2026-08-14g."
)

POST_CREATE = (
    "post-branch reminder: gh pr create just ran. Invoke the /post-branch skill"
    " NOW, before reporting the PR URL to the user. Its own Step 1 behaviour gate"
    " decides whether a docs sweep is warranted, so this is cheap even on a pure"
    " refactor. Advisory only - never blocking. Rationale: AGENTS.md > Git"
    " Conventions."
)

POST_MERGE = (
    "post-merge reminder: gh pr merge just ran. (1) Run make wait-ci-main in the"
    " BACKGROUND. (2) ONLY if the repo was flipped public for this PR - the flip is"
    " conditional on an EXHAUSTED Actions allowance, never a per-PR ritual: once no"
    " run on the merge SHA is in_progress (list every workflow by SHA over REST,"
    " never by branch), hand the operator the flip-back command - a session cannot run"
    " gh repo edit --visibility - then RE-VERIFY by the merge SHA over REST"
    " (actions/runs?head_sha=<merge-sha>), reading it as UNVERIFIED unless that"
    " SHA's runs appear. (3) Confirm the Issues the PR closes are closed, each with"
    " a one-line verdict. (4) Prune the merged local branch. (5) Append the"
    " handoff. Advisory only - never blocking. Rationale: AGENTS.md > CI quota -"
    " the visibility flip."
)

CLOSE_OUT_ADVICE = (
    "close-out: 'deleting sesh' means ARCHIVE this session, and only once every"
    " memory surface is written. Before answering: (1) new or changed to-dos become"
    " GitHub Issues on s10023/buibui-moon-trader-bot (screen the text with"
    " make post-branch-text FILE=<path> first), never memory-only; (2) prune and"
    " rewrite the handoff, docs/plans/next-conversation-prompt.md; (3) update"
    " MEMORY.md Current State (6-bullet cap; resolve the path with"
    " tools/memory_dir.py). Then ask the two close-out questions from the global"
    " CLAUDE.md - does a skill that ran need improving, and is anything left before"
    " the session is deleted - and archive only after that. Advisory only - never"
    " blocking. Rationale: CLAUDE.md > Session Memory Protocol."
)


def advise(payload: dict[str, object]) -> str | None:
    """The advice for this event, or None to stay silent."""
    event = payload.get("hook_event_name")
    if event == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "")
        return CLOSE_OUT_ADVICE if CLOSE_OUT.search(prompt) else None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    lines = str(tool_input.get("command") or "").splitlines()
    # Blank quoted strings first: a `|` inside `grep -E "a|gh pr create"` is regex
    # alternation, not a pipe, and must not read as a head position.
    first = _QUOTED.sub("''", lines[0]) if lines else ""
    if PR_CREATE.search(first):
        if event == "PreToolUse":
            return PRE_CREATE
        if event == "PostToolUse":
            return POST_CREATE
    if event == "PostToolUse" and PR_MERGE.search(first):
        return POST_MERGE
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail open: never break the session on a parse error
    if not isinstance(payload, dict):
        return 0
    advice = advise(payload)
    if advice:
        out = {
            "hookSpecificOutput": {
                "hookEventName": payload.get("hook_event_name"),
                "additionalContext": advice,
            }
        }
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
