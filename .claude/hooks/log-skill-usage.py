#!/usr/bin/env python3
"""Skill-usage ledger (#885) -- append one row per skill invocation.

Advisory only: it NEVER blocks and NEVER speaks. It appends a JSON line to a
gitignored ledger and exits 0, whatever goes wrong. Wired via
.claude/settings.json on two events, because a skill reaches a session two ways:

  PreToolUse[Skill]  -> the model invoked it: `tool_input.skill`
  UserPromptSubmit   -> the operator TYPED it: `/name args`, which the harness
                        expands in place and never routes through the Skill tool

The second leg is why this is not a one-event hook. A ledger fed only by the
Skill tool undercounts exactly the skills the operator drives by hand (`/card`,
`/post-branch`), and a skill reported as unused for that reason is a skill
someone deletes. `telegram-notify.py` meets the same split in the transcript
(`<command-name>/x</command-name>`), so both spellings are accepted here.

A typed `/x` is logged only when `x` is a skill: a `.claude/skills/x/SKILL.md`
exists, or the name is plugin-namespaced (`plugin:skill`). That keeps built-in
commands (`/compact`, `/clear`) out of the ledger without listing them.

Row: {ts, session_id, skill, args_len, source}. `args_len`, not the args: the
ledger is backed up off-machine (`docs/plans/*` is in the backup's LEDGERS glob)
and a skill's arguments can carry anything the operator typed.

Ledger: `docs/plans/skill-usage.jsonl` under the project, or
$SKILL_USAGE_LEDGER when set (tests). Report: `make skill-usage`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_TYPED = re.compile(r"^\s*/([A-Za-z0-9][A-Za-z0-9:_.-]*)(.*)", re.DOTALL)
_COMMAND_TAG = re.compile(
    r"<command-name>/?([A-Za-z0-9][A-Za-z0-9:_.-]*)</command-name>"
)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)


def project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def ledger_path(project: Path) -> Path:
    env = os.environ.get("SKILL_USAGE_LEDGER")
    return Path(env) if env else project / "docs" / "plans" / "skill-usage.jsonl"


def is_skill(name: str, project: Path) -> bool:
    return ":" in name or (project / ".claude" / "skills" / name / "SKILL.md").is_file()


def typed_skill(prompt: str, project: Path) -> tuple[str, int] | None:
    """(skill, args_len) for a prompt that invokes a skill by slash, else None."""
    tag = _COMMAND_TAG.search(prompt)
    if tag:
        name = tag.group(1)
        args = _COMMAND_ARGS.search(prompt)
        rest = args.group(1) if args else ""
    else:
        m = _TYPED.match(prompt)
        if not m:
            return None
        name, rest = m.group(1), m.group(2)
    if not is_skill(name, project):
        return None
    return name, len(rest.strip())


def row_for(payload: dict[str, Any], project: Path) -> dict[str, Any] | None:
    event = payload.get("hook_event_name")
    if event == "PreToolUse":
        if payload.get("tool_name") != "Skill":
            return None
        tool_input = payload.get("tool_input") or {}
        name = tool_input.get("skill")
        if not isinstance(name, str) or not name:
            return None
        args = tool_input.get("args")
        skill, args_len, source = (
            name,
            len(args) if isinstance(args, str) else 0,
            "tool",
        )
    elif event == "UserPromptSubmit":
        prompt = payload.get("prompt")
        hit = typed_skill(prompt, project) if isinstance(prompt, str) else None
        if hit is None:
            return None
        (skill, args_len), source = hit, "typed"
    else:
        return None
    return {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": str(payload.get("session_id") or ""),
        "skill": skill,
        "args_len": args_len,
        "source": source,
    }


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        project = project_dir()
        row = row_for(payload, project)
        if row is None:
            return 0
        path = ledger_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:  # noqa: BLE001 -- a logger must never cost the session a tool call
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
