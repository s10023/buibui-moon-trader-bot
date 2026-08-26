"""Report the posts in an `x_fetch --resolve --json` bundle set whose text is TRUNCATED.

`/ingest-x` step 1b runs this and HALTS when it exits 1, so the operator can paste the
long-form bodies before the extractor ever sees a cut-off post.

Why a halt rather than a note: the syndication endpoint's `note_tweet` proves a longer
body exists and hands back only an opaque ID stub, so **no re-fetch and no re-ingest can
recover the tail — only a human paste can.** The skill's older rule (name the truncation
in `gap_note`) records the damage without offering the one repair that exists. Measured
on the 2026-08-25 tranche: 14 of 18 posts truncated across 5 of 7 bundles.

Deliberately stdlib-only, with NO `sys.path` bootstrap, because it imports nothing from
the repo — a bare `python3 tools/x_truncated.py` therefore works, and
`test_bare_invocation_works` pins that. If a future edit adds an `analytics.*` import it
will break under the bare form (which puts `tools/` on the path, not the repo root) and
that test is what fails; add the bootstrap then, the way `distil_power.py` had to.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_TAIL_CHARS = 90


def _posts(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Posts in a bundle, tolerating both emitted shapes.

    `--resolve` emits `posts: [...]`; the plain batch path emits a single `post`. A
    single-post bundle is still worth reporting, so accept both rather than silently
    returning nothing for half the tool's callers.
    """
    posts = bundle.get("posts")
    if isinstance(posts, list):
        return [p for p in posts if isinstance(p, dict)]
    one = bundle.get("post")
    return [one] if isinstance(one, dict) else []


def find_truncated(bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One record per truncated post, in bundle order, carrying its bundle id.

    A post can appear in more than one bundle (a leaf in one is a quoted parent in
    another), so the record keeps `bundle_url` and callers dedupe on `url` for the
    operator-facing link list — the operator pastes a post once, not once per bundle.
    """
    out: list[dict[str, Any]] = []
    for bundle in bundles:
        if not isinstance(bundle, dict):
            continue
        burl = str(bundle.get("url") or "")
        for post in _posts(bundle):
            if not post.get("text_truncated"):
                continue
            text = str(post.get("text") or "")
            out.append(
                {
                    "bundle_url": burl,
                    "bundle_id": burl.rsplit("/", 1)[-1],
                    "url": str(post.get("url") or ""),
                    "author": str(post.get("author") or ""),
                    "post_ts_utc": str(post.get("post_ts_utc") or ""),
                    "role": str(post.get("role") or "bookmarked"),
                    "depth": int(post.get("depth") or 0),
                    "edited": bool(post.get("edited")),
                    "tail": text[-_TAIL_CHARS:],
                }
            )
    return out


def summarise(bundles: list[dict[str, Any]], found: list[dict[str, Any]]) -> str:
    """The SHAPE of the damage, not just its existence.

    A flat list of URLs does not tell an operator whether to paste four links or abandon
    the round; counts per bundle do, which is why this leads the report.
    """
    n_posts = sum(len(_posts(b)) for b in bundles if isinstance(b, dict))
    hit = {r["bundle_id"] for r in found}
    return (
        f"{len(found)} of {n_posts} post(s) truncated "
        f"across {len(hit)} of {len(bundles)} bundle(s)"
    )


def render(bundles: list[dict[str, Any]], found: list[dict[str, Any]]) -> str:
    if not found:
        n_posts = sum(len(_posts(b)) for b in bundles if isinstance(b, dict))
        return f"no truncated posts ({n_posts} post(s) across {len(bundles)} bundle(s))"

    lines = [
        f"TRUNCATED POSTS — {summarise(bundles, found)}",
        "",
        "The tail of each body is UNRECOVERABLE by re-fetch; only a paste restores it.",
        "★ marks a bookmarked post — it drives its item, so it pays most to paste.",
        "",
    ]
    seen: set[str] = set()
    for bundle_id in dict.fromkeys(r["bundle_id"] for r in found):
        rows = [r for r in found if r["bundle_id"] == bundle_id]
        lines.append(f"### bundle {bundle_id}  (@{rows[0]['author']})")
        for r in rows:
            star = "★" if r["role"] == "bookmarked" else " "
            role = (
                "BOOKMARKED"
                if r["role"] == "bookmarked"
                else f"{r['role']} d{r['depth']}"
            )
            dupe = "  [also in an earlier bundle]" if r["url"] in seen else ""
            edited = "  (edited)" if r["edited"] else ""
            seen.add(r["url"])
            lines.append(f"  {star} [{role}] {r['post_ts_utc'][:16]}Z{edited}{dupe}")
            lines.append(f"    {r['url']}")
            lines.append(f"    ...ends: {r['tail']!r}")
        lines.append("")
    lines.append(f"{len(seen)} distinct URL(s) to paste.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        default="-",
        help="x_fetch --resolve --json output; '-' (default) reads stdin",
    )
    parser.add_argument("--json", action="store_true", help="emit the records as JSON")
    args = parser.parse_args(argv)

    if args.source == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(args.source).read_text(encoding="utf-8")
    try:
        loaded: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"not JSON: {exc}", file=sys.stderr)
        return 2
    bundles = loaded if isinstance(loaded, list) else [loaded]

    found = find_truncated(bundles)
    if args.json:
        print(
            json.dumps(
                {"summary": summarise(bundles, found), "truncated": found}, indent=2
            )
        )
    else:
        print(render(bundles, found))
    # Exit 1 on findings so the skill's HALT is a MECHANISM rather than a prose rule.
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
