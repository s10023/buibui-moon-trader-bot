#!/usr/bin/env python3
"""Hand-run test harness for .claude/hooks/advise-lifecycle.py (#855).

Wired into CI's dependency-free markdownlint job beside the other hook suites.
Stdlib only. It pins which events speak and which stay silent: the two
`gh pr create` reminders (Pre and Post), the post-merge reminder, the close-out
advisory, and the protocol (always exit 0, JSON carrying the event name).

The hook replaces a `jq | head -1 | grep` one-liner that never fired on a host
without jq. Its own correctness is a regex over the first line, so the MUTATION
cases prove the two parts that make it better than the grep -- quote blanking
and the env-assignment prefix -- are what decide the outcome.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude/hooks/advise-lifecycle.py"


def load(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(f"advise_{path.stem}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


al: Any = load(HOOK)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASS.append(name)
        print(f"  ok    {name}")
    else:
        FAIL.append(f"{name}{(' -- ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(' -- ' + detail) if detail else ''}")


def bash(event: str, command: str) -> dict[str, Any]:
    return {"hook_event_name": event, "tool_input": {"command": command}}


def prompt(text: str) -> dict[str, Any]:
    return {"hook_event_name": "UserPromptSubmit", "prompt": text}


TOKEN = "GH_TOKEN=$(gh auth token --user s10023) "

print("advise-lifecycle")

# --- 1. gh pr create: BOTH legs fire at a head position ----------------------
for cmd in (
    "gh pr create --repo s10023/x --body-file f",
    TOKEN + "gh pr create --repo s10023/buibui-moon-trader-bot --body-file f",
    "git push -u origin HEAD && gh pr create --fill",
    "cd repo; gh pr create --fill",
):
    check(
        f"PreToolUse fires on: {cmd[:50]}",
        al.advise(bash("PreToolUse", cmd)) == al.PRE_CREATE,
    )
    check(
        f"PostToolUse fires on: {cmd[:50]}",
        al.advise(bash("PostToolUse", cmd)) == al.POST_CREATE,
    )

# --- 2. a command that only QUOTES it is silent on both legs -----------------
for cmd in (
    "grep 'gh pr create' AGENTS.md",
    "echo gh pr create",
    "gh pr view 5",
    'grep -nE "^#|jq|gh pr create" .claude/settings.json',
    "git commit -m 'docs: the gh pr create; reminder'",
    "git commit -F - <<'MSG'\ngh pr create is documented here\nMSG",
):
    for event in ("PreToolUse", "PostToolUse"):
        got = al.advise(bash(event, cmd))
        check(f"{event} silent on: {cmd[:50]!r}", got is None, str(got)[:60])

# --- 3. gh pr merge: Post only, token prefix included ------------------------
merge = TOKEN + "gh pr merge 5 --squash --delete-branch"
check(
    "PostToolUse fires after gh pr merge (token prefix)",
    al.advise(bash("PostToolUse", merge)) == al.POST_MERGE,
)
check(
    "PreToolUse is silent before the merge",
    al.advise(bash("PreToolUse", merge)) is None,
)
check(
    "post-merge text keeps the flip CONDITIONAL and the re-verify by merge SHA",
    "ONLY if the repo was flipped public" in al.POST_MERGE
    and "head_sha=<merge-sha>" in al.POST_MERGE,
)

# --- 4. close-out ------------------------------------------------------------
for text in ("ok deleting sesh", "Deleting session now", "going to delete this sesh"):
    check(
        f"close-out fires on: {text!r}",
        al.advise(prompt(text)) == al.CLOSE_OUT_ADVICE,
    )
check("an ordinary prompt is silent", al.advise(prompt("whats next")) is None)
check(
    "close-out names THIS repo, never the wifey fork",
    "s10023/buibui-moon-trader-bot" in al.CLOSE_OUT_ADVICE
    and "wifey" not in al.CLOSE_OUT_ADVICE,
)
check(
    "a Bash command mentioning the phrase is not a close-out",
    al.advise(bash("PreToolUse", "echo deleting sesh")) is None,
)

# --- 5. protocol, end to end -------------------------------------------------


def run(stdin: str, hook: Path = HOOK) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(hook)],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


proc = run(json.dumps(bash("PreToolUse", "gh pr create")))
try:
    out = json.loads(proc.stdout)["hookSpecificOutput"]
except (json.JSONDecodeError, KeyError, TypeError):
    out = {}
check(
    "emits JSON carrying the event name and the advice",
    proc.returncode == 0
    and out.get("hookEventName") == "PreToolUse"
    and out.get("additionalContext") == al.PRE_CREATE,
    proc.stdout[:80],
)
proc = run(json.dumps(prompt("deleting sesh")))
check(
    "UserPromptSubmit emits on stdout with exit 0",
    proc.returncode == 0 and "UserPromptSubmit" in proc.stdout,
    proc.stdout[:80],
)
proc = run("not json")
check(
    "garbage stdin fails OPEN and silent",
    (proc.returncode, proc.stdout) == (0, ""),
    repr((proc.returncode, proc.stdout[:40])),
)
proc = run("[]")
check(
    "a non-object payload fails OPEN and silent",
    (proc.returncode, proc.stdout) == (0, ""),
)
proc = run(json.dumps(bash("PreToolUse", "git status")))
check("an unrelated command is silent", (proc.returncode, proc.stdout) == (0, ""))

# --- 6. MUTATION: prove each mechanism is what decides ------------------------
_mut = Path(tempfile.mkdtemp(prefix="advise-lifecycle-mut-"))
try:
    src = HOOK.read_text(encoding="utf-8")

    # Drop quote blanking -> the `|` inside a quoted regex reads as a pipe.
    m1 = _mut / "no_quote_blanking.py"
    m1.write_text(
        src.replace("_QUOTED.sub(\"''\", lines[0])", "lines[0]"), encoding="utf-8"
    )
    mod1 = load(m1)
    check(
        "MUTATION: no quote blanking -> a quoted `|gh pr create` FIRES",
        mod1.advise(bash("PreToolUse", 'grep -nE "^#|gh pr create" f')) is not None,
    )
    check(
        "MUTATION: ...and a plain gh pr create still fires (scoped, not blanket)",
        mod1.advise(bash("PreToolUse", "gh pr create")) == mod1.PRE_CREATE,
    )

    # Drop the env-assignment prefix -> a plain `VAR=value gh ...` goes silent.
    # Probed with a PLAIN value on purpose: in `GH_TOKEN=$(...) gh` the closing
    # `)` is itself a separator, so that spelling survives this mutation and
    # would prove nothing about `_ENV`.
    m2 = _mut / "no_env_prefix.py"
    m2.write_text(
        src.replace(
            '_SEP = r"(?:^|[;&|()]|&&)\\s*" + _ENV', '_SEP = r"(?:^|[;&|()]|&&)\\s*"'
        ),
        encoding="utf-8",
    )
    mod2 = load(m2)
    check(
        "unmutated: a plain GH_TOKEN=tok prefix fires",
        al.advise(bash("PreToolUse", "GH_TOKEN=tok gh pr create")) == al.PRE_CREATE,
    )
    check(
        "MUTATION: no env prefix -> the GH_TOKEN=tok form is SILENT",
        mod2.advise(bash("PreToolUse", "GH_TOKEN=tok gh pr create")) is None,
    )
    check(
        "MUTATION: ...and the bare form still fires (scoped, not blanket)",
        mod2.advise(bash("PreToolUse", "gh pr create")) == mod2.PRE_CREATE,
    )
finally:
    shutil.rmtree(_mut, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL  {f}")
sys.exit(1 if FAIL else 0)
