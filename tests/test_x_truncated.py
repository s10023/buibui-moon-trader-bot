"""Tests for tools/x_truncated.py — the /ingest-x step-1b truncation halt."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.x_truncated import find_truncated, main, render, summarise

REPO = Path(__file__).resolve().parent.parent


def _post(**kw: Any) -> dict[str, Any]:
    base = {
        "url": "https://x.com/a/status/1",
        "author": "a",
        "post_ts_utc": "2026-08-24T10:00:00.000Z",
        "text": "body",
        "text_truncated": False,
        "role": "bookmarked",
        "depth": 0,
        "edited": False,
    }
    return {**base, **kw}


def _bundle(url: str, posts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"url": url, "posts": posts, "notes": []}


class TestFindTruncated:
    def test_returns_only_truncated_posts(self) -> None:
        bundles = [
            _bundle(
                "https://x.com/a/status/1",
                [_post(text_truncated=True), _post(url="https://x.com/a/status/2")],
            )
        ]
        found = find_truncated(bundles)
        assert [r["url"] for r in found] == ["https://x.com/a/status/1"]

    def test_empty_when_nothing_truncated(self) -> None:
        assert find_truncated([_bundle("https://x.com/a/status/1", [_post()])]) == []

    def test_accepts_the_batch_single_post_shape(self) -> None:
        """The plain batch path emits `post`, not `posts`. Returning nothing for it
        would make the halt silently inapplicable to half the tool's callers."""
        bundles = [
            {"url": "https://x.com/a/status/9", "post": _post(text_truncated=True)}
        ]
        assert len(find_truncated(bundles)) == 1

    def test_carries_bundle_id_so_one_post_in_two_bundles_is_visible(self) -> None:
        """A leaf in one bundle is a quoted parent in another. Both occurrences are
        reported; the operator-facing count dedupes on url."""
        shared = _post(url="https://x.com/a/status/7", text_truncated=True)
        found = find_truncated(
            [
                _bundle("https://x.com/a/status/7", [shared]),
                _bundle(
                    "https://x.com/a/status/8", [_post(**{**shared, "role": "quoted"})]
                ),
            ]
        )
        assert len(found) == 2
        assert {r["bundle_id"] for r in found} == {"7", "8"}
        assert len({r["url"] for r in found}) == 1


class TestSummary:
    def test_counts_posts_and_bundles(self) -> None:
        bundles = [
            _bundle("https://x.com/a/status/1", [_post(text_truncated=True), _post()]),
            _bundle("https://x.com/a/status/2", [_post()]),
        ]
        assert summarise(bundles, find_truncated(bundles)) == (
            "1 of 3 post(s) truncated across 1 of 2 bundle(s)"
        )

    def test_render_names_the_distinct_url_count(self) -> None:
        bundles = [_bundle("https://x.com/a/status/1", [_post(text_truncated=True)])]
        assert "1 distinct URL(s) to paste." in render(bundles, find_truncated(bundles))

    def test_render_stars_the_bookmarked_post_only(self) -> None:
        bundles = [
            _bundle(
                "https://x.com/a/status/1",
                [
                    _post(text_truncated=True),
                    _post(
                        url="https://x.com/a/status/2",
                        text_truncated=True,
                        role="quoted",
                        depth=1,
                    ),
                ],
            )
        ]
        out = render(bundles, find_truncated(bundles))
        assert "★ [BOOKMARKED]" in out
        assert "[quoted d1]" in out


class TestExitCodes:
    """Three distinct codes: a malformed input must NOT read as 'nothing truncated'."""

    def test_exit_1_on_findings(self, tmp_path: Path, capsys: Any) -> None:
        p = tmp_path / "b.json"
        p.write_text(
            json.dumps(
                [_bundle("https://x.com/a/status/1", [_post(text_truncated=True)])]
            ),
            encoding="utf-8",
        )
        assert main([str(p)]) == 1

    def test_exit_0_when_clean(self, tmp_path: Path, capsys: Any) -> None:
        p = tmp_path / "b.json"
        p.write_text(
            json.dumps([_bundle("https://x.com/a/status/1", [_post()])]),
            encoding="utf-8",
        )
        assert main([str(p)]) == 0

    def test_exit_2_on_malformed_json(self, tmp_path: Path, capsys: Any) -> None:
        p = tmp_path / "b.json"
        p.write_text("not json", encoding="utf-8")
        assert main([str(p)]) == 2


class TestBareInvocation:
    """CI runs tools with NO PYTHONPATH, so a green Make target proves nothing about
    the bare form. This is the ST101 lesson (`route_dedup.py`, `distil_power.py`)
    pinned before the fact: if a future edit adds an `analytics.*` import, this fails."""

    def test_bare_invocation_works(self, tmp_path: Path) -> None:
        p = tmp_path / "b.json"
        p.write_text(
            json.dumps([_bundle("https://x.com/a/status/1", [_post()])]),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, "tools/x_truncated.py", str(p)],
            cwd=REPO,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert "no truncated posts" in proc.stdout
