#!/usr/bin/env python3
"""Print this repo's open GitHub Issues as a session-start digest.

This is the PULL MECHANISM for planning. Since 2026-09-29 the to-do list lives
in GitHub Issues rather than the memory SoT, because memory rows drifted: two
claimed "PR pending" months after their PRs merged. But an Issue nobody FETCHES
is less visible than memory, not more -- so a SessionStart hook in
.claude/settings.json runs this, and every session opens with the queue already
in context. Ported from the fund-management pilot's tools/open_issues.py.

It lives in .claude/hooks/ rather than tools/ on purpose: test_hook_wiring.py
only inspects wrappers that run a .claude/hooks/*.py script, so a digest in
tools/ launched by a bare `python3` (the exit-126 Store alias on the Windows
host) would die silently -- and a SessionStart hook that prints nothing reads
exactly like an empty queue.

Load-bearing properties:

1. It ALWAYS exits 0. A planning digest must never block a session.
2. A failure prints LOUDLY. "Could not fetch" and "no open issues" must not
   look alike -- a silent degrade teaches its reader to trust an empty list.
3. A fetch that fills its page says so. A check is only true about the scope it
   looked at, and a truncated list otherwise reads as the whole queue.
4. `gh` is scoped to the account that OWNS the origin remote, read from the
   remote rather than restated, and never by `gh auth switch` (which changes
   the operator's own terminal). Ambient auth is the fallback.

p1 and p2 print in full, each with its effort label (`effort:?` when unset); p3
prints as a count. This output is paid in context on
EVERY session, and the p3 tail is most of the list and least of the decisions.
Untriaged issues print in full, since they need a decision rather than a skim.

Stdlib only: CI runs its suite with a bare `python3` and no dependencies.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_S = 20
LIMIT = 100  # the REST per_page maximum

# One page, PRs dropped. `raw` is the UNFILTERED page length: PRs count toward
# per_page, so a page can be full while holding fewer than LIMIT issues.
_JQ = (
    "{raw: length, issues: [.[] | select(.pull_request | not)"
    " | {number, title, labels: [.labels[] | {name}]}]}"
)

# Ordered best-first. p1/p2 print in full; p3 collapses to a count.
PRIORITIES = ("p1", "p2", "p3")
FULL = {"p1", "p2"}

# The suggested `/effort` for picking an Issue up. Shown on every p1/p2 row, and
# shown MISSING rather than omitted, so an unlabelled row reads as undecided
# instead of as "no effort needed". Claude cannot change its own effort, so the
# label is a suggestion the operator acts on with /effort.
EFFORTS = ("effort:low", "effort:medium", "effort:high", "effort:max")

_REMOTE_RE = re.compile(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$")


def _run(
    args: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=TIMEOUT_S,
        env=env,
        check=False,
    )


def repo_slug() -> str | None:
    """`owner/name` of the origin remote, or None when it cannot be read."""
    try:
        proc = _run(["git", "remote", "get-url", "origin"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = _REMOTE_RE.search(proc.stdout.decode("utf-8", "replace").strip())
    return f"{m.group(1)}/{m.group(2)}" if proc.returncode == 0 and m else None


def gh_env(owner: str | None) -> dict[str, str] | None:
    """An env scoping `gh` to `owner`'s token; None keeps ambient auth.

    An explicit GH_TOKEN already in the environment wins -- the caller chose it.
    """
    if owner is None or os.environ.get("GH_TOKEN"):
        return None
    try:
        proc = _run(["gh", "auth", "token", "--user", owner])
    except (OSError, subprocess.TimeoutExpired):
        return None
    token = proc.stdout.decode("utf-8", "replace").strip()
    return {**os.environ, "GH_TOKEN": token} if proc.returncode == 0 and token else None


def _fail(reason: str) -> int:
    """Report a fetch failure as a FINDING, never as an empty list."""
    print("## !! OPEN ISSUES NOT FETCHED -- this is a FAILURE, not 'no issues'")
    print(f"   reason: {reason}")
    print("   Planning for this repo lives in GitHub Issues. Until this is fixed")
    print("   you are working blind. Check `gh auth status`, then re-run")
    print("   `python .claude/hooks/open-issues.py`.")
    return 0


def _labels(issue: dict[str, Any]) -> set[str]:
    return {lb["name"] for lb in issue.get("labels", [])}


def _prio(issue: dict[str, Any]) -> str | None:
    names = _labels(issue)
    return next((p for p in PRIORITIES if p in names), None)


def render(
    issues: list[dict[str, Any]], slug: str | None, truncated: bool | None = None
) -> list[str]:
    """The digest lines for a successfully fetched list."""
    if truncated is None:
        truncated = len(issues) >= LIMIT
    if not issues:
        return [
            "## Open issues: NONE",
            "   Fetched successfully -- the queue is genuinely empty.",
        ]
    out = [f"## Open issues ({len(issues)}) -- this repo's planning queue, by priority"]
    if truncated:
        out.append(f"   !! TRUNCATED at {LIMIT} per page: this is NOT the whole queue.")
    by_num = sorted(issues, key=lambda i: i["number"])
    for prio in ("p1", "p2"):
        for issue in (i for i in by_num if _prio(i) == prio):
            names = _labels(issue)
            effort = next((e for e in EFFORTS if e in names), "effort:?")
            topic = ",".join(sorted(names - set(PRIORITIES) - set(EFFORTS)))
            tags = f"{effort} | {topic}" if topic else effort
            out.append(
                f"   {prio.upper()} #{issue['number']} {issue['title']}  [{tags}]"
            )
    n_p3 = sum(1 for i in issues if _prio(i) == "p3")
    if n_p3:
        out.append(f"   P3 x{n_p3} not listed -- `gh issue list --label p3`")
    untriaged = [i for i in by_num if _prio(i) is None]
    if untriaged:
        out.append(
            f"   !! UNTRIAGED (no {'/'.join(PRIORITIES)} label) -- sorted by no decision:"
        )
        out.extend(f"      #{i['number']} {i['title']}" for i in untriaged)
    repo = f" -R {slug}" if slug else ""
    out.append(
        f"   Detail: `gh issue view <n>{repo}` | refresh: `python .claude/hooks/open-issues.py`"
    )
    return out


def main() -> int:
    # Fetched titles are arbitrary Unicode and a Windows console is not UTF-8 by
    # default. Degrade a character rather than taking the hook down.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if shutil.which("gh") is None:
        return _fail("the `gh` CLI is not on PATH")
    slug = repo_slug()
    # REST, not `gh issue list`: that is GraphQL, which cloud sessions refuse
    # with a 403 -- every cloud session opened NOT FETCHED until 2026-10-04.
    # The issues endpoint also returns PRs, hence the select.
    args = ["gh", "api", f"repos/{slug or '{owner}/{repo}'}/issues"]
    args += ["--method", "GET", "-f", "state=open", "-f", f"per_page={LIMIT}"]
    args += ["--jq", _JQ]
    try:
        proc = _run(args, env=gh_env(slug.split("/")[0] if slug else None))
    except subprocess.TimeoutExpired:
        return _fail(f"`gh api` timed out after {TIMEOUT_S}s")
    except OSError as exc:
        return _fail(f"could not run `gh`: {exc}")
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return _fail(
            f"`gh api` failed: {stderr[-1] if stderr else f'exit {proc.returncode}'}"
        )
    try:
        page = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
        issues, raw = page["issues"], page["raw"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return _fail(f"could not parse `gh` output: {exc!r}")
    print("\n".join(render(issues, slug, truncated=raw >= LIMIT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
