#!/usr/bin/env python3
"""Hand-run test harness for .claude/hooks/context-guard.py.

Wired into CI's dependency-free markdownlint job, which is reachable only
because .claude/hooks/ is committed -- while it was gitignored this suite could
run by hand and report failure to nobody. Includes MUTATION cases: each asserts
that breaking the intended mechanism actually silences the card, so a pass
proves the glob/token is what fired it rather than something incidental.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

# Derived, never hardcoded: this file lives at <repo>/.claude/hooks/, and a
# machine-pinned path would break on the very reclone this suite now guards.
REPO = Path(__file__).resolve().parents[2]

# The hook dedups per (session_id, card) via a marker in /tmp that OUTLIVES the
# process. Hardcoded session ids therefore made this suite pass once and then
# report three phantom failures on every re-run -- a check that proves nothing.
# Every session id below is namespaced by a fresh nonce so each run starts cold.
RUN = uuid.uuid4().hex[:8]
HOOK = REPO / ".claude/hooks/context-guard.py"
MAP = REPO / ".claude/hooks/context-map.json"

PASS: list[str] = []
FAIL: list[str] = []


def run(payload: dict, *, hook: Path = HOOK) -> str:
    if "session_id" in payload:
        payload = {**payload, "session_id": f"{RUN}-{payload['session_id']}"}
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(REPO))
    out = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert out.returncode == 0, f"hook exited {out.returncode}: {out.stderr}"
    if not out.stdout.strip():
        return ""
    ctx = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    return str(ctx)


def edit(path: str, new_string: str = "x = 1", session: str = "s1") -> dict:
    """Build an Edit payload; `run` namespaces the session id with RUN."""
    return {
        "tool_name": "Edit",
        "session_id": session,
        "tool_input": {"file_path": str(REPO / path), "new_string": new_string},
    }


def check(
    name: str, got: str, *, must_contain: str = "", must_be_silent: bool = False
) -> None:
    if must_be_silent:
        ok = got == ""
        detail = f"expected silence, got {got[:90]!r}"
    else:
        ok = must_contain.lower() in got.lower()
        detail = f"expected {must_contain!r} in {got[:120]!r}"
    (PASS if ok else FAIL).append(name if ok else f"{name}: {detail}")


# --- 1. path trigger -------------------------------------------------------
check(
    "path: sizing.py fires",
    run(edit("portfolio/sizing.py", session="a1")),
    must_contain="snaps before it floors",
)
check(
    "path: _common.py fires",
    run(edit("analytics/store/_common.py", session="a2")),
    must_contain="malloc heap corruption",
)
check(
    "path: routing.py fires",
    run(edit("trade/routing.py", session="a3")),
    must_contain="post-only",
)
check(
    "path: nested web/ui fires (glob crosses /)",
    run(edit("web/ui/src/lib/Stats.svelte", session="a4")),
    must_contain="frontend-design",
)
check(
    "path: vendored skill dir fires",
    run(edit(".agents/skills/humanizer/SKILL.md", session="a4v")),
    must_contain="silently overwritten",
)
check(
    "path: the hash pin itself fires",
    run(edit("skills-lock.json", session="a4w")),
    must_contain="not a plain sha256",
)
check(
    "path: the .claude/skills symlink fires too",
    run(edit(".claude/skills/humanizer/SKILL.md", session="a4x")),
    must_contain="silently overwritten",
)
check(
    "mutation: a NON-vendored skill path stays silent",
    run(edit(".claude/skills/card/SKILL.md", session="a4y")),
    must_be_silent=True,
)
check(
    "path: signal_watch toml fires",
    run(edit("config/signal_watch_weekdays.toml", session="a5")),
    must_contain="working tree",
)

# --- 2. negative controls: the hook must stay quiet ------------------------
check(
    "quiet: unguarded source file",
    run(edit("analytics/zones_lib.py", session="b1")),
    must_be_silent=True,
)
check("quiet: README", run(edit("README.md", session="b2")), must_be_silent=True)
check(
    "quiet: non-watched tool",
    run({"tool_name": "Bash", "session_id": "b3", "tool_input": {"command": "ls"}}),
    must_be_silent=True,
)
check(
    "quiet: path outside the repo",
    run(
        {
            "tool_name": "Write",
            "session_id": "b4",
            "tool_input": {
                "file_path": "/tmp/elsewhere/portfolio/sizing.py",
                "content": "x",
            },
        }
    ),
    must_be_silent=True,
)

# --- 3. claim trigger ------------------------------------------------------
check(
    "claim: scratch .py asserting a powered null fires",
    run(
        edit(
            "docs/plans/scratch/multi_regime_x.py",
            "if abs(delta) < mde:  # powered null\n    pass",
            session="c1",
        )
    ),
    must_contain="CI CONTAINMENT",
)
check(
    "claim: gitignored scratch is NOT skipped (the ST28 inversion)",
    run(edit("docs/plans/scratch/foo.py", "verdict = 'NO-EDGE'", session="c2")),
    must_contain="negative claim detected",
)
check(
    "claim: audit verdict .md fires",
    run(edit("docs/audits/2026-08-15-x.md", "All cells NO-EDGE.", session="c3")),
    must_contain="negative claim detected",
)
check(
    "claim: separator-flexible (no_edge)",
    run(edit("docs/audits/2026-08-15-y.md", "cell is no_edge here", session="c4")),
    must_contain="negative claim detected",
)
check(
    "claim: Write tool content is read",
    run(
        {
            "tool_name": "Write",
            "session_id": "c5",
            "tool_input": {
                "file_path": str(REPO / "docs/audits/z.md"),
                "content": "CONFIRMED-BAD across the board",
            },
        }
    ),
    must_contain="negative claim detected",
)
check(
    "claim: MultiEdit edits[] are read",
    run(
        {
            "tool_name": "MultiEdit",
            "session_id": "c6",
            "tool_input": {
                "file_path": str(REPO / "tools/x.py"),
                "edits": [
                    {"new_string": "# nothing"},
                    {"new_string": "# ruled out by the test"},
                ],
            },
        }
    ),
    must_contain="negative claim detected",
)

# --- 4. claim precision: recollection is not a new claim -------------------
check(
    "precision: handoff mentioning NO-EDGE stays silent",
    run(
        edit(
            "docs/plans/next-conversation-prompt.md",
            "The closed NO-EDGE verdicts live in CLAUDE.md.",
            session="d1",
        )
    ),
    must_be_silent=True,
)
check(
    "precision: memory file mentioning powered null stays silent",
    run(
        {
            "tool_name": "Write",
            "session_id": "d2",
            "tool_input": {
                "file_path": "/tmp/anyuser/.claude-personal/x/memory/m.md",
                "content": "a powered null is CI containment",
            },
        }
    ),
    must_be_silent=True,
)
check(
    "precision: code with NO claim token stays silent",
    run(edit("docs/plans/scratch/quiet.py", "x = compute_mde(sample)", session="d3")),
    must_be_silent=True,
)
check(
    "precision: 'no edge' inside a longer word does not fire",
    run(edit("docs/audits/w.md", "the canoedgear was fine", session="d4")),
    must_be_silent=True,
)

# --- 5. dedup --------------------------------------------------------------
_first = run(edit("portfolio/sizing.py", session="e1"))
_second = run(edit("portfolio/sizing.py", session="e1"))
check("dedup: first utterance speaks", _first, must_contain="snaps before it floors")
check("dedup: second utterance in same session is silent", _second, must_be_silent=True)
check(
    "dedup: a DIFFERENT card in the same session still speaks",
    run(edit("card/state.py", session="e1")),
    must_contain="avg_atr_r",
)
check(
    "dedup: same card in a NEW session speaks again",
    run(edit("portfolio/sizing.py", session="e2")),
    must_contain="snaps before it floors",
)

# --- 6. fail open ----------------------------------------------------------
_env = dict(os.environ, CLAUDE_PROJECT_DIR=str(REPO))
for label, payload in (
    ("garbage stdin", "not json at all"),
    ("empty stdin", ""),
    ("json scalar", '"just a string"'),
):
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=_env,
        check=False,
    )
    check(f"fail-open: {label}", r.stdout.strip(), must_be_silent=True)
    if r.returncode != 0:
        FAIL.append(f"fail-open: {label} exited {r.returncode}")

# --- 7. MUTATION: break the mechanism, the card must go silent -------------
with tempfile.TemporaryDirectory() as td:
    mut_dir = Path(td)
    shutil.copy(HOOK, mut_dir / "context-guard.py")
    data = json.loads(MAP.read_text())

    # 7a. remove sizing.py's glob -> that card must stop firing
    for card in data["cards"]:
        if card["id"] == "sizing-round-down":
            card["globs"] = ["portfolio/NOTHING_HERE.py"]
    (mut_dir / "context-map.json").write_text(json.dumps(data))
    check(
        "MUTATION: glob removed -> sizing card silent",
        run(
            edit("portfolio/sizing.py", session="m1"), hook=mut_dir / "context-guard.py"
        ),
        must_be_silent=True,
    )

    # 7b. remove the claim tokens -> the claim card must stop firing
    data2 = json.loads(MAP.read_text())
    data2["claim"]["tokens"] = ["zzz never appears zzz"]
    (mut_dir / "context-map.json").write_text(json.dumps(data2))
    check(
        "MUTATION: tokens removed -> claim card silent",
        run(
            edit("docs/audits/m.md", "every cell is NO-EDGE", session="m2"),
            hook=mut_dir / "context-guard.py",
        ),
        must_be_silent=True,
    )

    # 7c. corrupt the map entirely -> hook must fail open, not crash
    (mut_dir / "context-map.json").write_text("{ this is not json")
    check(
        "MUTATION: corrupt map -> fail open, silent",
        run(
            edit("portfolio/sizing.py", session="m3"), hook=mut_dir / "context-guard.py"
        ),
        must_be_silent=True,
    )

# --- 8. injection cap ------------------------------------------------------
_msg = run(edit("signals/alert_formatter.py", session="f1"))
check(
    "cap: multi-card match stays under the ceiling",
    "ok" if len(_msg) <= 2600 + 200 else f"len={len(_msg)}",
    must_contain="ok",
)
check(
    "multi-card: alert_formatter hits both signals cards",
    _msg,
    must_contain="parse_mode=HTML",
)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL  {f}")
sys.exit(1 if FAIL else 0)
