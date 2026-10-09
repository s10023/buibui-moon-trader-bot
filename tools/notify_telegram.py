"""Push a Telegram message when an allowlisted skill finishes (#828, ST113).

Design: docs/superpowers/specs/2026-08-29-st113-telegram-notify-design.md, ruled
2026-10-01 (opt-in skill allowlist · one generic renderer · a toggled mode).

Two callers share this module:

- ``.claude/hooks/telegram-notify.py`` — the ``Stop`` hook. It reads the
  allowlist, finds the skills the turn ran in the session transcript, renders
  one message and hands it to a DETACHED ``python tools/notify_telegram.py send``
  so a slow or failing Telegram call never holds up the session.
- ``/telegram-mode`` — the toggle skill, through the ``mode`` subcommands below.

**The module is stdlib-only at import.** The hook suite runs in CI's
dependency-free markdownlint job, so ``requests`` (via ``utils.telegram``) and
``dotenv`` are imported inside ``send_html`` only.

**Why the transcript, not the hook payload.** A ``Stop`` payload carries
``session_id``, ``transcript_path`` and ``cwd`` but never the skill that ran — the
spec's D2. The transcript records both ways a skill starts: a ``Skill`` tool_use
whose input names it, and a typed slash command, stored as
``<command-name>/x</command-name>`` in the user row. If a future harness changes
that shape, detection finds nothing and the hook stays SILENT, which is the
disabled state rather than a wrong push; ``mode test`` exercises delivery on its
own so the two failures stay distinguishable.

Usage::

    python tools/notify_telegram.py mode status
    python tools/notify_telegram.py mode on | off
    python tools/notify_telegram.py mode add card | remove card
    python tools/notify_telegram.py mode test      # one synchronous sample push
    python tools/notify_telegram.py send           # HTML on stdin (hook only)
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import textwrap
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# A bare `python3 tools/notify_telegram.py` puts `tools/` on sys.path rather than
# the repo root, so `send_html`'s lazy `utils.telegram` import would fail. The
# guarantee is `test_bare_invocation_works`, not this line.
sys.path.insert(0, str(REPO))

ALLOWLIST_PATH = REPO / "docs" / "plans" / "telegram-notify.toml"
STATE_PATH = REPO / "docs" / "plans" / ".telegram-notify-state.json"

WIDTH = 46  # phone column inside Telegram's <pre>; deploy/run-job.sh TG_FOLD_WIDTH
BODY_CAP = 3400  # bytes; deploy/run-job.sh TG_BODY_CAP, under Telegram's 4096
SUMMARY_LINES = 14  # wrapped summary lines kept, after the fact lines
STATE_KEEP = 50  # sessions remembered by the dedup marker

_COMMAND_RE = re.compile(r"<command-name>/?([A-Za-z0-9_.:-]+)</command-name>")


# --- allowlist -----------------------------------------------------------------


@dataclass
class Allowlist:
    """The gitignored opt-in file. ``enabled = false`` keeps the list for later."""

    enabled: bool = False
    skills: list[str] = field(default_factory=list)

    def wants(self, skill: str) -> bool:
        """True when ``skill`` (or its bare name, for ``plugin:skill``) is listed."""
        if not self.enabled:
            return False
        bare = skill.rsplit(":", 1)[-1]
        return skill in self.skills or bare in self.skills


def load_allowlist(path: Path = ALLOWLIST_PATH) -> Allowlist:
    """Read the allowlist; a missing file is the disabled default, never an error."""
    if not path.is_file():
        return Allowlist()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    skills = raw.get("skills", [])
    if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
        raise ValueError(f"{path}: `skills` must be a list of strings")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"{path}: `enabled` must be true or false")
    return Allowlist(enabled=enabled, skills=[s.lstrip("/") for s in skills])


def save_allowlist(allow: Allowlist, path: Path = ALLOWLIST_PATH) -> None:
    """Write the allowlist back. The format is two keys, so no TOML writer is needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    listed = ", ".join(json.dumps(s) for s in sorted(set(allow.skills)))
    path.write_text(
        "# /telegram-mode allowlist (#828). Gitignored; read by the Stop hook.\n"
        f"enabled = {'true' if allow.enabled else 'false'}\n"
        f"skills = [{listed}]\n",
        encoding="utf-8",
    )


# --- transcript ----------------------------------------------------------------


@dataclass
class Turn:
    """The last turn of a session, as far as this feature needs it."""

    key: str  # uuid of the user row that opened the turn: the dedup key
    started: datetime | None
    skills: list[str]
    final_text: str
    branch: str


def _is_prompt_row(row: dict[str, object]) -> bool:
    """A user row that OPENS a turn — text from a person or the harness, not a tool result."""
    if row.get("type") != "user" or row.get("isMeta"):
        return False
    message = row.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        return "tool_result" not in kinds and bool(kinds)
    return False


def _row_text(row: dict[str, object]) -> str:
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return ""


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def last_turn(lines: Iterable[str]) -> Turn | None:
    """The newest turn in a transcript: who opened it, which skills ran, what it said.

    Walks the rows backwards to the newest prompt row. Malformed lines are skipped,
    because a transcript is written while the session runs.
    """
    rows: list[dict[str, object]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)

    start = None
    for i in range(len(rows) - 1, -1, -1):
        if _is_prompt_row(rows[i]):
            start = i
            break
    if start is None:
        return None

    opener = rows[start]
    skills: list[str] = []
    skills += _COMMAND_RE.findall(_row_text(opener))
    final_text = ""
    for row in rows[start + 1 :]:
        if row.get("type") != "assistant":
            continue
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Skill":
                tool_input = block.get("input")
                if isinstance(tool_input, dict) and isinstance(
                    tool_input.get("skill"), str
                ):
                    skills.append(tool_input["skill"].lstrip("/"))
            elif block.get("type") == "text" and str(block.get("text", "")).strip():
                final_text = str(block["text"])

    seen: list[str] = []
    for s in skills:
        if s not in seen:
            seen.append(s)
    return Turn(
        key=str(opener.get("uuid", "")),
        started=_parse_ts(opener.get("timestamp")),
        skills=seen,
        final_text=final_text,
        branch=str(opener.get("gitBranch", "") or ""),
    )


# --- renderer ------------------------------------------------------------------


def _plain(markdown: str) -> list[str]:
    """Markdown prose to plain lines: no emphasis, code ticks, headings or table rules."""
    out: list[str] = []
    for line in markdown.splitlines():
        s = line.strip()
        if re.fullmatch(r"\|?[\s:|-]+\|?", s) and "-" in s:
            continue  # table separator row
        if s.startswith("|") and s.endswith("|"):
            s = " · ".join(c.strip() for c in s.strip("|").split("|") if c.strip())
        s = re.sub(r"^#{1,6}\s*", "", s)
        s = s.replace("**", "").replace("__", "").replace("`", "")
        s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
        out.append(s)
    return out


def _wrap(text: str) -> list[str]:
    if not text:
        return [""]
    indent = re.match(r"^(\s*(?:[-*•]|\d+\.)\s+)", text)
    sub = " " * len(indent.group(1)) if indent else ""
    return textwrap.wrap(
        text,
        width=WIDTH,
        subsequent_indent=sub,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [""]


def _elapsed(started: datetime | None, now: datetime) -> str | None:
    if started is None:
        return None
    secs = max(0, int((now - started).total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


def render(turn: Turn, skills: Sequence[str], now: datetime) -> tuple[str, str]:
    """(headline, body) for one push. Every body line is at most ``WIDTH`` columns.

    Emitting within the width at the SOURCE is the ST114 lesson: a fold applied
    later shatters aligned lines, so the fold must be a no-op here.
    """
    names = ", ".join(f"/{s}" for s in skills)
    head = f"buibui: {names} finished"
    facts = [f"done: {now.astimezone(UTC):%Y-%m-%d %H:%M} UTC"]
    took = _elapsed(turn.started, now)
    if took:
        facts.append(f"took: {took}")
    if turn.branch:
        facts.append(f"branch: {turn.branch}")
    lines: list[str] = []
    for fact in facts:
        lines += _wrap(fact)
    summary: list[str] = []
    for para in _plain(turn.final_text):
        summary += _wrap(para)
    while summary and not summary[0]:
        summary.pop(0)
    if summary:
        lines.append("")
        if len(summary) > SUMMARY_LINES:
            summary = summary[:SUMMARY_LINES] + ["…"]
        lines += summary
    return head[:200], "\n".join(lines).rstrip()


def _esc(text: str) -> str:
    # quote=False: Telegram decodes only &lt; &gt; &amp;, so an escaped
    # apostrophe would render literally as &#x27; (AGENTS.md, buibui card).
    return html.escape(text, quote=False)


def compose_html(head: str, body: str) -> str:
    """The message as sent: bold headline, body in <pre>, capped from the BOTTOM.

    The cap drops trailing lines, unlike run-job.sh's `tail -c`: here the facts
    lead and the summary follows, so priority runs top-down.
    """
    lines = body.splitlines()
    while lines and len("\n".join(lines).encode("utf-8")) > BODY_CAP:
        lines = lines[:-2] + ["…"] if len(lines) > 1 else []
    return f"<b>{_esc(head)}</b>\n<pre>{_esc(chr(10).join(lines))}</pre>"


# --- dedup ---------------------------------------------------------------------


def already_sent(session_id: str, key: str, path: Path = STATE_PATH) -> bool:
    """True when this turn already pushed: Stop re-fires on resume and compaction."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(state, dict) and state.get(session_id) == key


def mark_sent(session_id: str, key: str, path: Path = STATE_PATH) -> None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, json.JSONDecodeError):
        state = {}
    state.pop(session_id, None)
    state[session_id] = key
    trimmed = dict(list(state.items())[-STATE_KEEP:])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trimmed, indent=1), encoding="utf-8")


# --- delivery ------------------------------------------------------------------


def send_html(message: str) -> None:
    """Send through ``utils.telegram``, which retries and redacts the bot token."""
    from dotenv import load_dotenv

    from utils.telegram import send_telegram_message

    load_dotenv(REPO / ".env")
    send_telegram_message(message)


def _cmd_mode(action: str, skill: str | None, path: Path) -> int:
    allow = load_allowlist(path)
    if action == "status":
        state = "ON" if allow.enabled else "OFF"
        listed = ", ".join(f"/{s}" for s in sorted(allow.skills)) or "(none)"
        print(f"telegram-mode: {state} · skills: {listed}")
        if not path.is_file():
            shown = path.relative_to(REPO) if path.is_relative_to(REPO) else path
            print(f"  ({shown} does not exist yet)")
        return 0
    if action in ("add", "remove"):
        if not skill:
            print(f"mode {action} needs a skill name", file=sys.stderr)
            return 2
        name = skill.lstrip("/")
        if action == "add" and name not in allow.skills:
            allow.skills.append(name)
            allow.enabled = True
        if action == "remove":
            allow.skills = [s for s in allow.skills if s != name]
    elif action == "on":
        allow.enabled = True
    elif action == "off":
        allow.enabled = False
    elif action == "test":
        now = datetime.now(UTC)
        turn = Turn(
            key="test",
            started=now,
            skills=["telegram-mode"],
            final_text="Test push from /telegram-mode. If this arrived, delivery works.",
            branch="",
        )
        head, body = render(turn, ["telegram-mode"], now)
        send_html(compose_html(head, body))
        print("test push sent (check the phone; failures log above)")
        return 0
    save_allowlist(allow, path)
    return _cmd_mode("status", None, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    mode = sub.add_parser("mode", help="show or change the allowlist")
    mode.add_argument(
        "action", choices=["status", "on", "off", "add", "remove", "test"]
    )
    mode.add_argument("skill", nargs="?")
    mode.add_argument("--allowlist", type=Path, default=ALLOWLIST_PATH)
    sub.add_parser("send", help="send the HTML message on stdin (used by the hook)")
    args = parser.parse_args(argv)
    if args.cmd == "send":
        send_html(sys.stdin.read())
        return 0
    return _cmd_mode(args.action, args.skill, args.allowlist)


if __name__ == "__main__":
    from utils.stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
