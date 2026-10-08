#!/usr/bin/env python3
"""Hand-run test harness for .claude/hooks/log-skill-usage.py (#885).

Wired into CI's dependency-free markdownlint job beside the other hook suites.
Stdlib only. Every case runs the hook as a SUBPROCESS, the way the harness does,
against a temp project and a temp ledger, so the exit status checked is the real
one and nothing touches this checkout's docs/plans/.

It pins: both entry legs log (a model-invoked Skill and an operator-typed slash),
a built-in slash command does NOT, the args are never stored, and every input --
garbage included -- exits 0. The MUTATION cases prove the skill-directory check
and the event routing are what decide the outcome, not the fixtures.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude/hooks/log-skill-usage.py"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASS.append(name)
        print(f"  ok    {name}")
    else:
        FAIL.append(f"{name}{(' -- ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(' -- ' + detail) if detail else ''}")


def load() -> Any:
    spec = importlib.util.spec_from_file_location("log_skill_usage_hook", HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(project: Path, ledger: Path, stdin: str) -> int:
    env = dict(
        os.environ, CLAUDE_PROJECT_DIR=str(project), SKILL_USAGE_LEDGER=str(ledger)
    )
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return proc.returncode


def rows(ledger: Path) -> list[dict[str, Any]]:
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text().splitlines() if line]


def tool_payload(skill: str, args: str | None = None) -> str:
    tool_input: dict[str, Any] = {"skill": skill}
    if args is not None:
        tool_input["args"] = args
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Skill",
            "session_id": "s1",
            "tool_input": tool_input,
        }
    )


def prompt_payload(prompt: str) -> str:
    return json.dumps(
        {"hook_event_name": "UserPromptSubmit", "session_id": "s2", "prompt": prompt}
    )


with tempfile.TemporaryDirectory() as tmp:
    project = Path(tmp) / "proj"
    (project / ".claude" / "skills" / "card").mkdir(parents=True)
    (project / ".claude" / "skills" / "card" / "SKILL.md").write_text("---\n")
    # a directory with no SKILL.md is not a skill (the dead-symlink shape, #vendored)
    (project / ".claude" / "skills" / "hollow").mkdir()

    def fresh(name: str) -> Path:
        return Path(tmp) / f"{name}.jsonl"

    # --- 1. the model leg -------------------------------------------------------
    led = fresh("tool")
    rc = run(project, led, tool_payload("card", "BTCUSDT long"))
    got = rows(led)
    check("Skill tool call exits 0", rc == 0, f"rc={rc}")
    check("Skill tool call appends one row", len(got) == 1, str(got))
    if got:
        r = got[0]
        check("row names the skill", r.get("skill") == "card", str(r))
        check("row source is 'tool'", r.get("source") == "tool", str(r))
        check("row carries the session id", r.get("session_id") == "s1", str(r))
        check("args_len is the length, not the args", r.get("args_len") == 12, str(r))
        check(
            "the args themselves are never stored",
            "BTCUSDT" not in led.read_text(),
            led.read_text(),
        )
        check(
            "row has exactly the five keys",
            set(r) == {"ts", "session_id", "skill", "args_len", "source"},
            str(r),
        )
        check("ts is UTC ISO-8601 with a Z", str(r.get("ts", "")).endswith("Z"), str(r))

    led = fresh("tool-noargs")
    run(project, led, tool_payload("mattpocock-skills:pr"))
    got = rows(led)
    check(
        "a plugin skill via the tool logs with args_len 0",
        len(got) == 1
        and got[0]["skill"] == "mattpocock-skills:pr"
        and got[0]["args_len"] == 0,
        str(got),
    )

    # the model leg trusts the harness's own Skill call -- it does not need a dir
    led = fresh("tool-unknown")
    run(project, led, tool_payload("frontend-design"))
    check(
        "a Skill tool call is logged whatever the name",
        len(rows(led)) == 1,
        str(rows(led)),
    )

    # --- 2. the typed leg -------------------------------------------------------
    led = fresh("typed")
    rc = run(project, led, prompt_payload("/card ETHUSDT short"))
    got = rows(led)
    check("typed slash exits 0", rc == 0, f"rc={rc}")
    check(
        "a typed local skill logs as source 'typed'",
        len(got) == 1 and got[0]["skill"] == "card" and got[0]["source"] == "typed",
        str(got),
    )
    check(
        "typed args_len counts the stripped args",
        bool(got) and got[0]["args_len"] == len("ETHUSDT short"),
        str(got),
    )

    led = fresh("typed-tag")
    run(
        project,
        led,
        prompt_payload(
            "<command-name>/card</command-name>\n<command-args>SOL</command-args>"
        ),
    )
    got = rows(led)
    check(
        "the expanded <command-name> spelling logs too",
        len(got) == 1 and got[0]["skill"] == "card" and got[0]["args_len"] == 3,
        str(got),
    )

    led = fresh("typed-plugin")
    run(project, led, prompt_payload("/superpowers:brainstorm"))
    check("a typed plugin skill logs", len(rows(led)) == 1, str(rows(led)))

    for prompt, why in (
        ("/compact", "a built-in command"),
        ("/hollow", "a skills dir with no SKILL.md"),
        ("please run /card for BTC", "a slash that does not open the prompt"),
        ("what does /card do?", "a question mentioning a skill"),
        ("", "an empty prompt"),
        ("/", "a bare slash"),
    ):
        led = fresh("neg")
        if led.exists():
            led.unlink()
        rc = run(project, led, prompt_payload(prompt))
        check(
            f"not logged: {why}",
            rc == 0 and rows(led) == [],
            f"rc={rc} rows={rows(led)}",
        )

    # --- 3. events it must ignore, and inputs it must survive ------------------
    for stdin, why in (
        (
            json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "/card"},
                }
            ),
            "a Bash call",
        ),
        (
            json.dumps(
                {
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Skill",
                    "tool_input": {"skill": "card"},
                }
            ),
            "PostToolUse",
        ),
        (
            json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Skill",
                    "tool_input": {},
                }
            ),
            "a Skill call with no name",
        ),
        ("not json at all", "garbage stdin"),
        ("[1, 2]", "a JSON array"),
        ("", "empty stdin"),
    ):
        led = fresh("ignore")
        if led.exists():
            led.unlink()
        rc = run(project, led, stdin)
        check(
            f"exit 0 and no row: {why}",
            rc == 0 and rows(led) == [],
            f"rc={rc} rows={rows(led)}",
        )

    # an unwritable ledger must still exit 0 (a directory where the file should be)
    blocked = Path(tmp) / "blocked.jsonl"
    blocked.mkdir()
    rc = run(project, blocked, tool_payload("card"))
    check("an unwritable ledger still exits 0", rc == 0, f"rc={rc}")

    # appends, never truncates
    led = fresh("append")
    run(project, led, tool_payload("card"))
    run(project, led, prompt_payload("/card"))
    check("two invocations leave two rows", len(rows(led)) == 2, str(rows(led)))

    # the ledger's parent is created when absent (a fresh clone has no docs/plans/)
    led = Path(tmp) / "new" / "deep" / "skill-usage.jsonl"
    run(project, led, tool_payload("card"))
    check("a missing ledger directory is created", len(rows(led)) == 1)

    # --- 4. MUTATIONS: the decision is the code's, not the fixture's -----------
    hook = load()
    check(
        "MUTATION: with the SKILL.md present /card is a skill",
        hook.typed_skill("/card", project) == ("card", 0),
    )
    (project / ".claude" / "skills" / "card" / "SKILL.md").unlink()
    check(
        "MUTATION: remove SKILL.md and the same /card is no longer logged",
        hook.typed_skill("/card", project) is None,
    )
    check(
        "MUTATION: the same payload under another event is ignored",
        hook.row_for(
            {
                "hook_event_name": "Stop",
                "tool_name": "Skill",
                "tool_input": {"skill": "card"},
            },
            project,
        )
        is None,
    )

    # --- 5. the default ledger lands under docs/plans/, inside the backup glob --
    os.environ.pop("SKILL_USAGE_LEDGER", None)
    check(
        "default ledger is docs/plans/skill-usage.jsonl (a file the LEDGERS glob carries)",
        hook.ledger_path(project) == project / "docs" / "plans" / "skill-usage.jsonl",
    )

print()
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
