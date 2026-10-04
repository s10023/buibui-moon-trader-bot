#!/usr/bin/env python3
"""Hand-run test harness for .claude/hooks/open-issues.py (the SessionStart digest).

Wired into CI's dependency-free markdownlint job beside the other hook suites.
Stdlib only, no network: `gh` and `git` are replaced by fakes injected into the
module, so every failure path is exercised on any host.

What it pins, in the order the digest's docstring promises it:
  1. it ALWAYS exits 0, on every failure path;
  2. a failure is LOUD and never renders like an empty queue;
  3. a page that fills up says it is truncated, judged on the RAW page length;
  4. gh is scoped to the origin owner's token, and an explicit GH_TOKEN wins.
Plus the layout: p1/p2 in full, p3 as a count, untriaged in full.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude/hooks/open-issues.py"

_spec = importlib.util.spec_from_file_location("open_issues", HOOK)
assert _spec and _spec.loader
oi: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oi)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASS.append(name)
        print(f"  ok    {name}")
    else:
        FAIL.append(f"{name}{(' -- ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(' -- ' + detail) if detail else ''}")


def issue(n: int, title: str, *labels: str) -> dict[str, Any]:
    return {"number": n, "title": title, "labels": [{"name": lb} for lb in labels]}


def done(
    rc: int = 0, out: str = "", err: str = ""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], rc, out.encode(), err.encode())


def run_main(fake_run: Any, *, gh_present: bool = True) -> tuple[int, str]:
    """main() with `gh`/`git` faked; returns (exit code, stdout)."""
    orig_run, orig_which = oi._run, oi.shutil.which
    oi._run = fake_run
    oi.shutil.which = lambda name: "/fake/gh" if gh_present else None
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = oi.main()
    finally:
        oi._run, oi.shutil.which = orig_run, orig_which
    return rc, buf.getvalue()


def page(issues: list[dict[str, Any]], raw: int | None = None) -> str:
    """What the hook's --jq emits: the filtered issues plus the raw page length."""
    return json.dumps({"raw": len(issues) if raw is None else raw, "issues": issues})


def fake(list_result: Any) -> Any:
    """A `_run` that answers git/auth normally and `gh issue list` with list_result."""

    def _f(args: list[str], env: dict[str, str] | None = None) -> Any:
        if args[:2] == ["git", "remote"]:
            return done(out="https://github.com/owner1/repo1.git\n")
        if args[:3] == ["gh", "auth", "token"]:
            return done(out="tok\n")
        if isinstance(list_result, BaseException):
            raise list_result
        return list_result

    return _f


QUEUE = [
    issue(12, "p3 thing", "p3"),
    issue(5, "second p1", "p1", "research"),
    issue(3, "first p1", "p1"),
    issue(7, "a p2", "p2", "shower-thought"),
    issue(9, "no label at all"),
]

# ---------------------------------------------------------------- layout
print("layout:")
lines = oi.render(QUEUE, "owner1/repo1")
text = "\n".join(lines)
check("header counts every open issue", "Open issues (5)" in lines[0], lines[0])
p1_at = [i for i, ln in enumerate(lines) if ln.strip().startswith("P1")]
check(
    "p1 rows sorted by number",
    ["#3" in lines[p1_at[0]], "#5" in lines[p1_at[1]]] == [True, True],
    text,
)
check(
    "p1 precedes p2",
    max(p1_at) < next(i for i, ln in enumerate(lines) if "P2 #7" in ln),
)
check(
    "topic labels shown beside the title",
    "[research]" in text and "[shower-thought]" in text,
)
check(
    "p3 collapses to a count, title NOT listed",
    "P3 x1" in text and "p3 thing" not in text,
    text,
)
check(
    "untriaged listed in full",
    "UNTRIAGED" in text and "#9 no label at all" in text,
    text,
)
check("detail hint carries -R <slug>", "-R owner1/repo1" in text)
check("no truncation warning under the limit", "TRUNCATED" not in text)

full = [issue(i, f"t{i}", "p3") for i in range(oi.LIMIT)]
check(
    "a list AT the page limit says it is truncated",
    "TRUNCATED" in "\n".join(oi.render(full, None)),
)

empty = "\n".join(oi.render([], None))
check(
    "empty queue says fetched-and-empty", "NONE" in empty and "genuinely empty" in empty
)

# ---------------------------------------------------------------- failure paths
print("failure paths (always exit 0, always loud):")
cases: dict[str, tuple[Any, bool]] = {
    "gh missing from PATH": (fake(done()), False),
    "gh exits non-zero": (fake(done(1, err="HTTP 401: Bad credentials")), True),
    "gh returns unparsable JSON": (fake(done(0, out="{not json")), True),
    "gh times out": (fake(subprocess.TimeoutExpired("gh", 20)), True),
    "gh cannot be launched": (fake(OSError("exec format error")), True),
}
for name, (fn, present) in cases.items():
    rc, out = run_main(fn, gh_present=present)
    check(f"{name}: exit 0", rc == 0, f"rc={rc}")
    check(f"{name}: prints NOT FETCHED", "NOT FETCHED" in out, out[:80])
    check(
        f"{name}: does NOT look like an empty queue",
        "NONE" not in out and "genuinely empty" not in out,
    )

rc, out = run_main(fake(done(1, err="line one\nHTTP 401: Bad credentials\n")))
check("failure reason quotes gh's last stderr line", "HTTP 401" in out, out)

rc, out = run_main(fake(done(0, out=page(QUEUE))))
check(
    "success path: exit 0 and renders the queue",
    rc == 0 and "Open issues (5)" in out,
    out[:80],
)
check("an untruncated page carries no warning", "TRUNCATED" not in out)

# PRs share the issues endpoint and count toward per_page, so a full page of
# mostly PRs must still warn even though few issues survive the filter.
rc, out = run_main(fake(done(0, out=page(QUEUE, raw=oi.LIMIT))))
check(
    "a FULL raw page warns though few issues survive the PR filter",
    "TRUNCATED" in out,
    out[:200],
)

rc, out = run_main(fake(done(0, out=json.dumps(QUEUE))))
check(
    "a bare list (the old gh issue list shape) fails LOUD, not empty",
    rc == 0 and "NOT FETCHED" in out and "NONE" not in out,
    out[:120],
)

# ---------------------------------------------------------------- gh scoping
print("gh scoping:")
_seen: dict[str, Any] = {}


def _capture(args: list[str], env: dict[str, str] | None = None) -> Any:
    if args[:2] == ["git", "remote"]:
        return done(out="git@github.com:owner1/repo1.git\n")
    if args[:3] == ["gh", "auth", "token"]:
        _seen["auth_user"] = args[-1]
        return done(out="tok-owner1\n")
    _seen["list_args"], _seen["list_env"] = args, env
    return done(out=page([]))


_saved = os.environ.pop("GH_TOKEN", None)
try:
    run_main(_capture)
    check(
        "ssh remote parsed to owner/name",
        "repos/owner1/repo1/issues" in _seen["list_args"],
        str(_seen["list_args"]),
    )
    # `gh issue list` is GraphQL, which cloud sessions refuse with a 403.
    check(
        "lists over REST (`gh api`), never GraphQL `gh issue list`",
        _seen["list_args"][:2] == ["gh", "api"] and "issue" not in _seen["list_args"],
        str(_seen["list_args"]),
    )
    check("token requested for the REMOTE's owner", _seen.get("auth_user") == "owner1")
    check(
        "list runs under that token",
        (_seen["list_env"] or {}).get("GH_TOKEN") == "tok-owner1",
    )

    os.environ["GH_TOKEN"] = "explicit"
    _seen.clear()
    run_main(_capture)
    check(
        "an explicit GH_TOKEN wins (no auth lookup)",
        "auth_user" not in _seen and _seen["list_env"] is None,
    )
finally:
    os.environ.pop("GH_TOKEN", None)
    if _saved is not None:
        os.environ["GH_TOKEN"] = _saved

check(
    "https remote parsed",
    oi._REMOTE_RE.search("https://github.com/a/b.git").groups() == ("a", "b"),
)
check(
    "non-github remote -> no slug",
    oi._REMOTE_RE.search("https://gitlab.com/a/b.git") is None,
)

# ---------------------------------------------------------------- end to end
# The real script, real interpreter, whatever `gh` this host has (none, logged
# out, or live). Every outcome is legal EXCEPT a non-zero exit or silence --
# the two ways a SessionStart hook can fail without anyone noticing.
print("end to end:")
proc = subprocess.run(
    [sys.executable, str(HOOK)],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=60,
    check=False,
)
check("real run exits 0", proc.returncode == 0, proc.stderr[-200:])
check(
    "real run is never silent",
    "Open issues" in proc.stdout or "NOT FETCHED" in proc.stdout,
    proc.stdout[:120],
)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL  {f}")
sys.exit(1 if FAIL else 0)
