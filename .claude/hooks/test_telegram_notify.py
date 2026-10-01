#!/usr/bin/env python3
"""Hand-run test harness for .claude/hooks/telegram-notify.py (#828).

Wired into CI's dependency-free markdownlint job beside the other hook suites.
Stdlib only: the hook and ``tools/notify_telegram.py`` import nothing third-party
at module level, and the sender is replaced by a recorder, so nothing reaches
Telegram.

It pins what the operator ruled on 2026-10-01: an opt-in allowlist (silent when
absent or off), detection of the skills the LAST turn ran, one push per turn, and
every line of the push within 46 columns. The MUTATION cases prove that turn
scoping and the dedup marker are what decide the outcome, not the fixtures.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude/hooks/telegram-notify.py"
TOOL = REPO / "tools/notify_telegram.py"
NOW = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)


def load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod


hook: Any = load(HOOK, "telegram_notify_hook")
tool: Any = load(TOOL, "notify_telegram_tool")

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASS.append(name)
        print(f"  ok    {name}")
    else:
        FAIL.append(f"{name}{(' -- ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(' -- ' + detail) if detail else ''}")


# --- transcript fixtures ---------------------------------------------------------


def prompt(uuid: str, text: str, ts: str = "2026-10-01T12:00:00Z") -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": ts,
        "gitBranch": "feat/x",
        "message": {"role": "user", "content": text},
    }


def skill_call(name: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Skill",
                    "input": {"skill": name},
                }
            ]
        },
    }


def tool_result() -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]
        },
    }


def say(text: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    }


class Env:
    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="tgn-"))
        self.allow = self.dir / "telegram-notify.toml"
        self.state = self.dir / "state.json"
        self.transcript = self.dir / "t.jsonl"
        self.sent: list[str] = []

    def allowlist(self, text: str) -> None:
        self.allow.write_text(text, encoding="utf-8")

    def rows(self, *rows: dict[str, Any]) -> None:
        self.transcript.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )

    def fire(self, mod: Any = None, session: str = "s1") -> str | None:
        m = mod or hook
        result: str | None = m.run(
            {"session_id": session, "transcript_path": str(self.transcript)},
            allowlist_path=self.allow,
            state_path=self.state,
            spawn=self.sent.append,
            now=NOW,
        )
        return result

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def pre_lines(message: str) -> list[str]:
    body = message.split("<pre>", 1)[1].rsplit("</pre>", 1)[0]
    return body.splitlines()


CARD_TURN = (
    prompt("u1", "run a card for BTC"),
    skill_call("card"),
    tool_result(),
    say(
        "**Verdict:** TRADE long BTCUSDT\n\n| entry | sl |\n| --- | --- |\n| 64000 | 63000 |"
    ),
)

print("allowlist gate")
e = Env()
try:
    e.rows(*CARD_TURN)
    check("no allowlist file -> silent", e.fire() is None and not e.sent)
    e.allowlist('enabled = false\nskills = ["card"]\n')
    check("enabled = false -> silent", e.fire() is None and not e.sent)
    e.allowlist("enabled = true\nskills = []\n")
    check("empty skill list -> silent", e.fire() is None and not e.sent)
    e.allowlist('enabled = true\nskills = ["brief"]\n')
    check("skill not on the list -> silent", e.fire() is None and not e.sent)
    e.allowlist('enabled = true\nskills = ["/card"]\n')
    msg = e.fire()
    check("allowlisted skill -> exactly one push", msg is not None and len(e.sent) == 1)
    check(
        "headline names the skill",
        msg is not None and "<b>buibui: /card finished</b>" in msg,
    )
finally:
    e.close()

print("detection and turn scoping")
e = Env()
try:
    e.allowlist('skills = ["card", "brief"]\n')  # enabled defaults to true
    e.rows(prompt("u1", "<command-name>/brief</command-name>"), say("Brief done."))
    check(
        "typed slash command is detected",
        e.fire() is not None and "/brief" in e.sent[-1],
    )
    e.rows(prompt("u2", "hi"), skill_call("superpowers:card"), say("done"))
    check("plugin:skill matches its bare name", e.fire() is not None)
    e.rows(*CARD_TURN, prompt("u9", "thanks"), say("You're welcome."))
    check("a skill from an EARLIER turn does not push", e.fire() is None)
    e.rows(
        prompt("u3", "x"),
        skill_call("card"),
        skill_call("brief"),
        skill_call("card"),
        say("ok"),
    )
    m = e.fire()
    check(
        "two skills in one turn -> one push naming both once",
        m is not None and "/card, /brief" in m,
    )
finally:
    e.close()

print("dedup")
e = Env()
try:
    e.allowlist('skills = ["card"]\n')
    e.rows(*CARD_TURN)
    first = e.fire()
    again = e.fire()
    check(
        "Stop re-firing on the same turn pushes once",
        first is not None and again is None and len(e.sent) == 1,
    )
    e.rows(*CARD_TURN, prompt("u5", "again"), skill_call("card"), say("second card"))
    check(
        "a NEW turn with the skill pushes again",
        e.fire() is not None and len(e.sent) == 2,
    )
    e.rows(*CARD_TURN)
    check("dedup is per session", e.fire(session="s2") is not None)
finally:
    e.close()

print("rendering")
e = Env()
try:
    e.allowlist('skills = ["card"]\n')
    long_text = (
        "Result: "
        + "word " * 300
        + "\nhttps://example.com/"
        + "x" * 120
        + "\nTraceback line 33, in <module> -- it's broken & bad"
    )
    e.rows(
        prompt("u1", "go", ts="2026-10-01T12:28:30Z"),
        skill_call("card"),
        say(long_text),
    )
    m = e.fire() or ""
    lines = pre_lines(m)
    widest = max((len(x) for x in lines), default=0)
    check("every <pre> line is at most 46 columns", widest <= 46, f"widest={widest}")
    check(
        "facts lead: finish time, elapsed, branch",
        lines[:3] == ["done: 2026-10-01 12:30 UTC", "took: 1m", "branch: feat/x"],
        repr(lines[:3]),
    )
    check("summary is truncated with an ellipsis", lines[-1] == "…")
    check(
        "body stays under the byte cap", len("\n".join(lines).encode()) <= tool.BODY_CAP
    )
    e.rows(
        prompt("u2", "go"),
        skill_call("card"),
        say("line 33, in <module> -- it's fine & done"),
    )
    m2 = e.fire() or ""
    check(
        "HTML is escaped (&lt;module&gt;, &amp;)",
        "&lt;module&gt;" in m2 and "&amp;" in m2,
    )
    check(
        "apostrophes are NOT escaped (Telegram shows &#x27; literally)",
        "&#x27;" not in m2 and "it's" in m2,
    )
    e.rows(
        prompt("u3", "go"),
        skill_call("card"),
        say("| entry | sl |\n| --- | --- |\n| 64000 | 63000 |"),
    )
    m3 = e.fire() or ""
    check(
        "table separators dropped, cells joined",
        "---" not in m3 and "64000 · 63000" in m3,
    )
finally:
    e.close()

print("never fails the session")
for name, stdin in [
    ("garbage stdin", "not json"),
    (
        "missing transcript",
        json.dumps({"transcript_path": "/nope.jsonl", "session_id": "s"}),
    ),
]:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
    )
    check(
        f"{name}: exit 0 and no stdout",
        proc.returncode == 0 and proc.stdout == "",
        proc.stderr,
    )
e = Env()
try:
    e.allowlist("skills = not-a-list\n")
    e.rows(*CARD_TURN)
    try:
        e.fire()
        raised = False
    except Exception:  # noqa: BLE001
        raised = True
    check("a malformed allowlist raises inside run() ...", raised)
    real_run, real_stdin = hook.run, sys.stdin
    err = io.StringIO()

    def boom(payload: dict[str, Any]) -> None:
        raise ValueError("bad allowlist")

    hook.run, sys.stdin = boom, io.StringIO('{"transcript_path": "x"}')
    try:
        with contextlib.redirect_stderr(err):
            rc = hook.main()
    finally:
        hook.run, sys.stdin = real_run, real_stdin
    check(
        "... and main() turns any error into exit 0 plus one stderr line",
        rc == 0 and err.getvalue().startswith("telegram-notify: skipped (ValueError"),
        err.getvalue(),
    )
finally:
    e.close()

_lost = Path(tempfile.mkdtemp(prefix="tgn-lost-"))
try:
    nested = _lost / "a" / ".claude" / "hooks"
    nested.mkdir(parents=True)
    shutil.copy(HOOK, nested / "telegram-notify.py")
    proc = subprocess.run(
        [sys.executable, str(nested / "telegram-notify.py")],
        input="{}",
        capture_output=True,
        text=True,
        timeout=30,
        cwd=_lost,
    )
    check(
        "tools/ unimportable -> exit 0, one stderr line, no traceback",
        proc.returncode == 0
        and proc.stderr.startswith("telegram-notify: skipped (import failed")
        and "Traceback" not in proc.stderr,
        proc.stderr,
    )
finally:
    shutil.rmtree(_lost, ignore_errors=True)

print("mutations")
_mut = Path(tempfile.mkdtemp(prefix="tgn-mut-"))
try:
    src = TOOL.read_text(encoding="utf-8")
    scoped = "for row in rows[start + 1 :]:"
    assert scoped in src
    m1 = _mut / "notify_unscoped.py"
    m1.write_text(src.replace(scoped, "for row in rows:"), encoding="utf-8")
    unscoped: Any = load(m1, "notify_unscoped")
    hsrc = HOOK.read_text(encoding="utf-8")
    h1 = _mut / "hook_unscoped.py"
    h1.write_text(
        hsrc.replace(
            "from tools.notify_telegram import", "from notify_unscoped import"
        ),
        encoding="utf-8",
    )
    sys.path.insert(0, str(_mut))
    hook_unscoped: Any = load(h1, "hook_unscoped")
    e = Env()
    try:
        e.allowlist('skills = ["card"]\n')
        e.rows(*CARD_TURN, prompt("u9", "thanks"), say("welcome"))
        check("unmutated: an earlier turn's skill is silent", e.fire() is None)
        check(
            "MUTATION: unscoped walk -> the earlier skill PUSHES",
            e.fire(mod=hook_unscoped) is not None,
        )
    finally:
        e.close()

    dedup = (
        "    if already_sent(session_id, turn.key, state_path):\n        return None\n"
    )
    assert dedup in hsrc
    h2 = _mut / "hook_nodedup.py"
    h2.write_text(hsrc.replace(dedup, ""), encoding="utf-8")
    hook_nodedup: Any = load(h2, "hook_nodedup")
    e = Env()
    try:
        e.allowlist('skills = ["card"]\n')
        e.rows(*CARD_TURN)
        hook_nodedup_first = e.fire(mod=hook_nodedup)
        hook_nodedup_again = e.fire(mod=hook_nodedup)
        check(
            "MUTATION: no dedup check -> the same turn pushes TWICE",
            hook_nodedup_first is not None
            and hook_nodedup_again is not None
            and len(e.sent) == 2,
        )
    finally:
        e.close()
finally:
    shutil.rmtree(_mut, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL  {f}")
sys.exit(1 if FAIL else 0)
