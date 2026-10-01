#!/usr/bin/env python3
"""Stop hook: push to Telegram when an allowlisted skill finishes (#828, ST113).

The contract is "notify me when done", and that cannot be carried by prose: this
repo measured prose cadence rules at 0-for-15 against a mechanical check's
15-for-15 (ST108). So the harness fires this on every Stop, and the operator's
opt-in decides whether anything is sent.

Order of work, cheapest first, so a disabled feature costs every session almost
nothing:

1. Read ``docs/plans/telegram-notify.toml``. Missing, ``enabled = false`` or an
   empty list -> exit 0 before the transcript is opened.
2. Find the skills the LAST turn ran (``tools/notify_telegram.last_turn``). None
   allowlisted -> exit 0.
3. Skip a turn that already pushed (Stop re-fires on resume and compaction).
4. Render, mark sent, and hand the message to a DETACHED sender, so Telegram's
   retries (up to ~3s) never hold up the session.

Never blocks and never fails the session: exit 0 always, one stderr line on error.
Stdlib only, like every hook here, because its suite runs in CI's dependency-free
markdownlint job.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# Guarded: this runs on EVERY Stop of every session, so a broken import must cost
# one stderr line from main(), never a traceback per turn.
_IMPORT_ERROR: Exception | None = None
try:
    from tools.notify_telegram import (
        ALLOWLIST_PATH,
        STATE_PATH,
        already_sent,
        compose_html,
        last_turn,
        load_allowlist,
        mark_sent,
        render,
    )
except Exception as exc:  # noqa: BLE001
    _IMPORT_ERROR = exc
    ALLOWLIST_PATH = STATE_PATH = REPO  # placeholders; run() is never reached

SENDER = REPO / "tools" / "notify_telegram.py"

Spawner = Callable[[str], None]


def spawn_sender(message: str) -> None:
    """Start ``notify_telegram.py send`` detached, message on stdin, and return."""
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(SENDER), "send"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(REPO),
        # POSIX: a new session, so the sender outlives the hook. Windows ignores
        # that flag, so detach there explicitly (both constants are 0 elsewhere).
        start_new_session=True,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    assert proc.stdin is not None
    proc.stdin.write(message.encode("utf-8"))
    proc.stdin.close()


def run(
    payload: dict[str, object],
    *,
    allowlist_path: Path = ALLOWLIST_PATH,
    state_path: Path = STATE_PATH,
    spawn: Spawner = spawn_sender,
    now: datetime | None = None,
) -> str | None:
    """Send at most one push for this Stop. Returns the message sent, or None."""
    allow = load_allowlist(allowlist_path)
    if not allow.enabled or not allow.skills:
        return None
    transcript = payload.get("transcript_path")
    session_id = str(payload.get("session_id", ""))
    if not isinstance(transcript, str) or not transcript:
        return None
    path = Path(transcript)
    if not path.is_file():
        return None
    with path.open(encoding="utf-8", errors="replace") as fh:
        turn = last_turn(fh)
    if turn is None:
        return None
    wanted = [s for s in turn.skills if allow.wants(s)]
    if not wanted:
        return None
    if already_sent(session_id, turn.key, state_path):
        return None
    head, body = render(turn, wanted, now or datetime.now(UTC))
    message = compose_html(head, body)
    mark_sent(session_id, turn.key, state_path)
    spawn(message)
    return message


def main() -> int:
    if _IMPORT_ERROR is not None:
        print(
            f"telegram-notify: skipped (import failed: {_IMPORT_ERROR})",
            file=sys.stderr,
        )
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if isinstance(payload, dict):
            run(payload)
    except Exception as exc:  # noqa: BLE001 - a notifier must never fail a session
        print(
            f"telegram-notify: skipped ({type(exc).__name__}: {exc})", file=sys.stderr
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
