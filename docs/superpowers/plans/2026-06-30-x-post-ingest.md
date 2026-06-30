# X-post Ingest (iteration 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paste an X/Twitter post URL → fetch its text + chart image with no auth/scraping → vision-extract the setup in a subagent → classify (content-type gate + parent 4-bucket taxonomy) → route into the existing three streams, with a human review gate.

**Architecture:** A pure-Python read-only fetcher (`tools/x_fetch.py`) hits X's public syndication endpoint and maps the JSON into an `XPost` (text + full-res photo URLs + media flags), downloading charts to a gitignored cache. A `/ingest-x` skill orchestrates: run the fetcher → dispatch a subagent that reads the chart via vision and returns a small JSON → present a review digest → on approval, append to the routed stream file. The fetcher is fully TDD'd with injected HTTP (no real network in tests); the skill is validated by a live dry-run.

**Tech Stack:** Python 3.11+, `requests` (already a dep), `dataclasses`, `argparse`, pytest + `unittest.mock`. The skill is markdown (`.claude/skills/ingest-x/SKILL.md`).

## Global Constraints

- Python 3.11+; **mypy strict** — every function fully annotated incl. `-> None` on test methods.
- **ruff** format + lint must pass (`make lint-py`).
- **Tests make no real network calls** — `fetch_x_post` / `download_photos` accept an injectable `get` callable; tests pass a fake. (Repo rule.)
- Read-only: the fetcher never writes to `analytics.db` or any DB; no schema/golden changes.
- Syndication endpoint: `https://cdn.syndication.twimg.com/tweet-result?id=<id>&lang=en&token=a` — `token` must be present + non-empty but its value is not validated (verified 2026-06-30); always send `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)`.
- Full-res chart URL = the photo base URL (strip any `?…`) + `?name=orig`.
- DoD before "done": `make lint-py` ✓, `make typecheck` ✓, `make test` green; for the skill markdown, `make lint-md` ✓.
- **Imports go at the top of the file.** The "add to …" snippets below show `import` lines inline for context; place each in the top-of-file import block (ruff flags mid-file imports as E402, and `make lint-py` at each task's commit step will catch it).
- Spec of record: `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md`.

---

### Task 1: Module scaffold + `parse_tweet_id`

**Files:**

- Create: `tools/x_fetch.py`
- Test: `tests/test_x_fetch.py`

**Interfaces:**

- Produces: `parse_tweet_id(url: str) -> str` (raises `ValueError` on a non-status URL); module constants `_SYNDICATION_URL`, `_TOKEN`, `_UA`; `HttpResponse` / `HttpGet` Protocols; `_requests_get(url: str, *, headers: dict[str, str]) -> HttpResponse`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_x_fetch.py
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools.x_fetch import parse_tweet_id


@dataclass
class FakeResp:
    status_code: int
    text: str = ""
    content: bytes = b""


def make_get(resp: "FakeResp"):
    def _get(url: str, *, headers: dict[str, str]) -> "FakeResp":
        return resp
    return _get


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://x.com/cryptic_heych/status/2071837700500644228?s=20", "2071837700500644228"),
        ("https://twitter.com/jack/status/20", "20"),
        ("https://x.com/foo/status/123/", "123"),
    ],
)
def test_parse_tweet_id(url: str, expected: str) -> None:
    assert parse_tweet_id(url) == expected


def test_parse_tweet_id_invalid() -> None:
    with pytest.raises(ValueError):
        parse_tweet_id("https://x.com/cryptic_heych")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_x_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.x_fetch'` (or import error).

- [ ] **Step 3: Write minimal implementation**

```python
# tools/x_fetch.py
"""Read-only X/Twitter post fetcher via the public syndication endpoint.

No auth, no scraping: hits cdn.syndication.twimg.com/tweet-result (the data path
that powers embedded tweets) and maps the JSON into an XPost. A non-empty `token`
query param is required but its value is not validated by the endpoint, so a fixed
dummy is used (verified 2026-06-30). Mirrors tools/journal_fetch.py: read-only,
HTTP injectable for tests, CLI for ad-hoc use.
"""

from __future__ import annotations

import re
from typing import Protocol

_SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"
_TOKEN = "a"  # any non-empty value works; the endpoint does not validate it
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_ID_RE = re.compile(r"(?:twitter|x)\.com/[^/]+/status/(\d+)")


class HttpResponse(Protocol):
    status_code: int
    text: str
    content: bytes


class HttpGet(Protocol):
    def __call__(self, url: str, *, headers: dict[str, str]) -> HttpResponse: ...


def _requests_get(url: str, *, headers: dict[str, str]) -> HttpResponse:
    import requests

    return requests.get(url, headers=headers, timeout=20)


def parse_tweet_id(url: str) -> str:
    match = _ID_RE.search(url)
    if not match:
        raise ValueError(f"not an X/Twitter status URL: {url!r}")
    return match.group(1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_x_fetch.py -v`
Expected: PASS (4 cases).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add tools/x_fetch.py tests/test_x_fetch.py
git commit -m "feat(x-ingest): tweet-id parser + fetcher scaffold"
```

---

### Task 2: `XPost` model + `fetch_x_post` (JSON mapping, tombstone, video flag)

**Files:**

- Modify: `tools/x_fetch.py`
- Test: `tests/test_x_fetch.py`

**Interfaces:**

- Consumes: `parse_tweet_id`, `HttpGet`, `_SYNDICATION_URL`, `_TOKEN`, `_UA`.
- Produces: frozen `XPost(source, author, author_name, url, post_ts_utc, text, photo_urls: tuple[str, ...], video_present: bool, is_thread: bool, is_quote: bool)`; frozen `Unavailable(reason: str)`; `_orig(url: str) -> str`; `fetch_x_post(url: str, *, get: HttpGet = _requests_get) -> XPost | Unavailable`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_x_fetch.py
from tools.x_fetch import fetch_x_post, XPost, Unavailable

_CRYPTIC = {
    "__typename": "Tweet",
    "text": "Today's statistical analysis\n$BTC https://t.co/x",
    "created_at": "2026-06-30T06:06:15.000Z",
    "user": {"name": "HeycH", "screen_name": "cryptic_heych"},
    "photos": [{"url": "https://pbs.twimg.com/media/ABC.jpg"}],
    "mediaDetails": [{"type": "photo", "media_url_https": "https://pbs.twimg.com/media/ABC.jpg"}],
}


def test_fetch_maps_fields() -> None:
    url = "https://x.com/cryptic_heych/status/2071837700500644228"
    post = fetch_x_post(url, get=make_get(FakeResp(200, json.dumps(_CRYPTIC))))
    assert isinstance(post, XPost)
    assert post.author == "cryptic_heych"
    assert post.author_name == "HeycH"
    assert post.post_ts_utc == "2026-06-30T06:06:15.000Z"
    assert post.text.startswith("Today's statistical analysis")
    assert post.photo_urls == ("https://pbs.twimg.com/media/ABC.jpg?name=orig",)
    assert post.video_present is False
    assert post.is_thread is False and post.is_quote is False


def test_fetch_video_flag() -> None:
    payload = dict(_CRYPTIC, photos=[], mediaDetails=[{"type": "video"}])
    post = fetch_x_post("https://x.com/a/status/9", get=make_get(FakeResp(200, json.dumps(payload))))
    assert isinstance(post, XPost)
    assert post.video_present is True
    assert post.photo_urls == ()


def test_fetch_tombstone() -> None:
    payload = {"__typename": "TweetTombstone"}
    res = fetch_x_post("https://x.com/a/status/9", get=make_get(FakeResp(200, json.dumps(payload))))
    assert isinstance(res, Unavailable)


def test_fetch_http_error() -> None:
    res = fetch_x_post("https://x.com/a/status/9", get=make_get(FakeResp(404)))
    assert isinstance(res, Unavailable)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_x_fetch.py -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_x_post'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to tools/x_fetch.py
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class XPost:
    source: str
    author: str  # @handle (screen_name)
    author_name: str
    url: str
    post_ts_utc: str
    text: str
    photo_urls: tuple[str, ...]  # full-res (?name=orig)
    video_present: bool
    is_thread: bool
    is_quote: bool


@dataclass(frozen=True)
class Unavailable:
    reason: str


def _orig(url: str) -> str:
    base = url.split("?", 1)[0]
    return f"{base}?name=orig"


def fetch_x_post(url: str, *, get: HttpGet = _requests_get) -> XPost | Unavailable:
    tweet_id = parse_tweet_id(url)
    api = f"{_SYNDICATION_URL}?id={tweet_id}&lang=en&token={_TOKEN}"
    resp = get(api, headers={"User-Agent": _UA})
    if resp.status_code != 200:
        return Unavailable(f"HTTP {resp.status_code}")
    try:
        data = json.loads(resp.text)
    except json.JSONDecodeError:
        return Unavailable("non-JSON response")
    if not data or data.get("__typename") == "TweetTombstone" or "text" not in data:
        return Unavailable("post unavailable (protected/deleted/age-gated)")
    media = data.get("mediaDetails") or []
    video_present = any(m.get("type") in ("video", "animated_gif") for m in media)
    photos = tuple(_orig(p["url"]) for p in (data.get("photos") or []))
    user = data.get("user") or {}
    return XPost(
        source="twitter",
        author=user.get("screen_name", ""),
        author_name=user.get("name", ""),
        url=url,
        post_ts_utc=data.get("created_at", ""),
        text=data.get("text", ""),
        photo_urls=photos,
        video_present=video_present,
        is_thread=data.get("parent") is not None,
        is_quote=data.get("quoted_tweet") is not None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_x_fetch.py -v`
Expected: PASS (all cases).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add tools/x_fetch.py tests/test_x_fetch.py
git commit -m "feat(x-ingest): fetch_x_post maps syndication JSON to XPost"
```

---

### Task 3: `download_photos` (full-res charts to cache)

**Files:**

- Modify: `tools/x_fetch.py`
- Test: `tests/test_x_fetch.py`

**Interfaces:**

- Consumes: `XPost`, `HttpGet`, `_UA`.
- Produces: `download_photos(post: XPost, dest_dir: Path, *, get: HttpGet = _requests_get) -> list[Path]`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_x_fetch.py
from tools.x_fetch import download_photos


def test_download_photos_writes_files(tmp_path: Path) -> None:
    post = XPost(
        source="twitter", author="a", author_name="A", url="u", post_ts_utc="t",
        text="x", photo_urls=("https://pbs.twimg.com/media/ABC.jpg?name=orig",),
        video_present=False, is_thread=False, is_quote=False,
    )
    paths = download_photos(post, tmp_path, get=make_get(FakeResp(200, content=b"\xff\xd8jpeg")))
    assert paths == [tmp_path / "0.jpg"]
    assert (tmp_path / "0.jpg").read_bytes() == b"\xff\xd8jpeg"


def test_download_photos_skips_errors(tmp_path: Path) -> None:
    post = XPost(
        source="twitter", author="a", author_name="A", url="u", post_ts_utc="t",
        text="x", photo_urls=("https://pbs.twimg.com/media/ABC.jpg?name=orig",),
        video_present=False, is_thread=False, is_quote=False,
    )
    paths = download_photos(post, tmp_path, get=make_get(FakeResp(500)))
    assert paths == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_x_fetch.py -k download -v`
Expected: FAIL — `ImportError: cannot import name 'download_photos'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to tools/x_fetch.py
from pathlib import Path


def download_photos(post: XPost, dest_dir: Path, *, get: HttpGet = _requests_get) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, photo_url in enumerate(post.photo_urls):
        resp = get(photo_url, headers={"User-Agent": _UA})
        if resp.status_code != 200:
            continue
        out = dest_dir / f"{i}.jpg"
        out.write_bytes(resp.content)
        paths.append(out)
    return paths
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_x_fetch.py -k download -v`
Expected: PASS (2 cases).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add tools/x_fetch.py tests/test_x_fetch.py
git commit -m "feat(x-ingest): download_photos writes full-res charts to cache"
```

---

### Task 4: CLI (`python tools/x_fetch.py <url> [--json]`)

**Files:**

- Modify: `tools/x_fetch.py`
- Test: `tests/test_x_fetch.py`

**Interfaces:**

- Consumes: `XPost`, `fetch_x_post`.
- Produces: `_format_human(post: XPost) -> str`; `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_x_fetch.py
from tools.x_fetch import _format_human


def test_format_human() -> None:
    post = XPost(
        source="twitter", author="cryptic_heych", author_name="HeycH", url="u",
        post_ts_utc="2026-06-30T06:06:15.000Z", text="Today's analysis $BTC",
        photo_urls=("https://pbs.twimg.com/media/ABC.jpg?name=orig",),
        video_present=False, is_thread=False, is_quote=False,
    )
    out = _format_human(post)
    assert "@cryptic_heych" in out
    assert "Today's analysis $BTC" in out
    assert "photos: 1" in out
    assert "ABC.jpg?name=orig" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_x_fetch.py -k format_human -v`
Expected: FAIL — `ImportError: cannot import name '_format_human'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to tools/x_fetch.py
import argparse
import sys
from dataclasses import asdict


def _format_human(post: XPost) -> str:
    lines = [
        f"@{post.author} ({post.author_name})  {post.post_ts_utc}",
        post.text,
        f"photos: {len(post.photo_urls)}  video: {post.video_present}  "
        f"thread: {post.is_thread}  quote: {post.is_quote}",
    ]
    lines.extend(f"  {u}" for u in post.photo_urls)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch an X post via the public syndication endpoint (read-only)."
    )
    parser.add_argument("url")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    result = fetch_x_post(args.url)
    if isinstance(result, Unavailable):
        print(f"UNAVAILABLE: {result.reason}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(result), indent=2) if args.json else _format_human(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_x_fetch.py -v`
Expected: PASS (entire file).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add tools/x_fetch.py tests/test_x_fetch.py
git commit -m "feat(x-ingest): CLI for tools/x_fetch.py"
```

---

### Task 5: Cache gitignore + Stream sinks

**Files:**

- Modify: `.gitignore`

**Interfaces:** none (scaffolding).

- [ ] **Step 1: Ensure the media/post cache is ignored**

Check `.gitignore` for a `.cache/` entry; if absent, append:

```gitignore
# X-post ingest (and research-ingestion) caches
.cache/
```

`docs/plans/` is already gitignored, so the Stream sinks (`mechanics-backlog.md`, `pundit-calls.jsonl`) need no ignore rule. The skill creates them on first use (Task 6).

- [ ] **Step 2: Verify**

Run: `git check-ignore .cache/x-media/1/0.jpg docs/plans/pundit-calls.jsonl`
Expected: both paths print (both ignored).

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore(x-ingest): gitignore .cache/ for downloaded media"
```

---

### Task 6: `route_target` — the testable routing decision

**Files:**

- Create: `tools/x_route.py`
- Test: `tests/test_x_route.py`

**Interfaces:**

- Produces: `route_target(content_type: str, verdict: str) -> str | None` — returns the destination stream-file path, or `None` for a drop; raises `ValueError` on an unroutable combination. Pure (no I/O).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_x_route.py
import pytest

from tools.x_route import route_target


@pytest.mark.parametrize(
    "content_type, verdict, expected",
    [
        ("setup", "NOVEL", "docs/plans/pundit-calls.jsonl"),
        ("mechanic", "NOVEL", "docs/plans/mechanics-backlog.md"),
        ("claim", "NOVEL", "docs/plans/thesis-inbox.md"),
        ("claim", "ALREADY-TESTED", None),
        ("claim", "FROZEN-CATEGORY", None),
        ("claim", "NOT-FALSIFIABLE", None),
    ],
)
def test_route_target(content_type: str, verdict: str, expected: str | None) -> None:
    assert route_target(content_type, verdict) == expected


def test_route_target_unroutable() -> None:
    with pytest.raises(ValueError):
        route_target("claim", "BOGUS")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_x_route.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.x_route'`.

- [ ] **Step 3: Write minimal implementation**

```python
# tools/x_route.py
"""Pure routing decision for ingested X posts (no I/O).

content_type gate, then the parent pipeline's 4-bucket verdict taxonomy on the
claim path. See docs/superpowers/specs/2026-06-30-x-post-ingest-design.md.
"""

from __future__ import annotations

_DROP_VERDICTS = {"ALREADY-TESTED", "FROZEN-CATEGORY", "NOT-FALSIFIABLE"}


def route_target(content_type: str, verdict: str) -> str | None:
    if content_type == "setup":
        return "docs/plans/pundit-calls.jsonl"
    if content_type == "mechanic":
        return "docs/plans/mechanics-backlog.md"
    if content_type == "claim":
        if verdict == "NOVEL":
            return "docs/plans/thesis-inbox.md"
        if verdict in _DROP_VERDICTS:
            return None
    raise ValueError(f"unroutable: content_type={content_type!r} verdict={verdict!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_x_route.py -v`
Expected: PASS (7 cases).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add tools/x_route.py tests/test_x_route.py
git commit -m "feat(x-ingest): pure route_target routing decision"
```

---

### Task 7: The `/ingest-x` orchestration skill

**Files:**

- Create: `.claude/skills/ingest-x/SKILL.md`

**Interfaces:**

- Consumes: `tools/x_fetch.py` CLI (`--json`), `tools/x_route.py::route_target`, a dispatched subagent, the stream files in `docs/plans/`.
- Produces: the skill itself. Not pytest-tested (orchestration markdown) — validated by a live dry-run (Step 2). The routing *decision* it relies on is unit-tested in Task 6.

- [ ] **Step 1: Write the skill**

Create `.claude/skills/ingest-x/SKILL.md` with exactly this content:

````markdown
---
name: ingest-x
description: >
  Ingest a single X/Twitter post (URL) into the research pipeline. Fetches the
  post text + chart image with NO login/scraping via the public syndication
  endpoint (tools/x_fetch.py), reads the chart with vision in a subagent, classifies
  it (content-type gate -> the parent pipeline's 4-bucket verdict taxonomy), and
  routes it (after a human review gate) into one of the three streams: A hypotheses
  -> docs/plans/thesis-inbox.md, B mechanics -> docs/plans/mechanics-backlog.md,
  C daily setups -> docs/plans/pundit-calls.jsonl. Iteration 1 = text + still
  images; video is detected and skipped. Invoke when the user says "/ingest-x",
  pastes an x.com / twitter.com status URL, or says "ingest this X post".
allowed-tools: Bash, Read, Write, Edit, Task
---

# Ingest X post

Spec: `docs/superpowers/specs/2026-06-30-x-post-ingest-design.md`.

## Flow

1. **Fetch.** Run the read-only fetcher:

   ```bash
   PYTHONPATH=. poetry run python tools/x_fetch.py "<url>" --json
   ```

   - If it prints `UNAVAILABLE: …` (protected/deleted/age-gated), tell the user and
     ask them to paste the post text and drop a screenshot; then continue from step 3
     using the pasted text + image.
   - Otherwise parse the JSON: `author`, `author_name`, `post_ts_utc`, `text`,
     `photo_urls`, `video_present`, `is_thread`, `is_quote`.

2. **Download charts** (only if `photo_urls` is non-empty). In Python via the tool:

   ```bash
   PYTHONPATH=. poetry run python -c "from pathlib import Path; from tools.x_fetch import fetch_x_post, download_photos; p=fetch_x_post('<url>'); print([str(x) for x in download_photos(p, Path('.cache/x-media/<id>'))])"
   ```

   (`<id>` = the numeric tweet id.) If `video_present` is true, note in the digest
   "video detected — skipped (iteration 2)" and proceed with text + still images.

3. **Extract via a subagent** (keeps image bytes out of the main context). Dispatch a
   `general-purpose` subagent (Task tool) with: the post text, the downloaded chart
   image file path(s), the schema below, and this classification context — the
   FROZEN-detector list and the "Parked / NOT now" + "Closed" sections of
   `~/.claude-personal/.../memory/project_todo_master.md` (the living SoT) plus
   `docs/plans/thesis-inbox.md`. Instruct it to Read each image (vision), then return
   ONLY this JSON (no prose):

   ```json
   {
     "symbol": "BTCUSDT | null",
     "direction": "long | short | neutral | null",
     "entry": "...", "stop": "...", "target": "...",
     "horizon": "intraday | swing | unspecified",
     "setup_type": "free text",
     "raw_quote": "the sentence(s) the call/claim came from",
     "chart_read": "what the chart shows (levels, structure, annotations)",
     "content_type": "claim | setup | mechanic",
     "verdict": "NOVEL | ALREADY-TESTED | FROZEN-CATEGORY | NOT-FALSIFIABLE",
     "gap_note": "one line: implied primitive + does the system already have/test/freeze it?"
   }
   ```

   `verdict` applies only when `content_type = claim`; for `setup`/`mechanic` set it to
   `NOVEL` as a non-blocking default (routing uses `content_type` for those).

4. **Review digest.** Show the user: author + timestamp, the text, the `chart_read`,
   the full extraction JSON, the `gap_note`, and the proposed routing (table below).
   Write NOTHING yet.

5. **Route on approval only.** After the user approves, compute the destination with
   `tools/x_route.py::route_target(content_type, verdict)` (returns the sink path or
   `None` for a drop), then append per this table:

   | content_type | verdict | Append to |
   | --- | --- | --- |
   | setup | — | `docs/plans/pundit-calls.jsonl` (one JSON line, schema below) |
   | mechanic | — | `docs/plans/mechanics-backlog.md` (a `- ` bullet) |
   | claim | NOVEL | `docs/plans/thesis-inbox.md` (a draft `H` row) |
   | claim | ALREADY-TESTED / FROZEN-CATEGORY / NOT-FALSIFIABLE | **drop** — state "seen, verdict X", write nothing |

   Create the sink file with a one-line header if it does not exist.

   **Stream C line** (`pundit-calls.jsonl`, one line, matches the parent spec's
   pundit-call schema):

   ```json
   {"source":"twitter","author":"<handle>","url":"<url>","call_ts_utc":"<post_ts_utc>","symbol":"<symbol>","direction":"<direction>","entry":"<entry>","stop":"<stop>","target":"<target>","horizon":"<horizon>","confidence":"<verbatim hedging or empty>","raw_quote":"<raw_quote>"}
   ```

## Guardrails

- Output is a hypothesis/setup/mechanic to TEST — never an "add a detector" task. The
  detector list is frozen.
- Never auto-write a stream file before the user approves the digest.
- Iteration 1: no video, no thread-walking, no scraping. The syndication + manual-paste
  paths are the only two.
````

- [ ] **Step 2: Validate with a live dry-run**

Invoke the skill on the real post used to design it:

`/ingest-x https://x.com/cryptic_heych/status/2071837700500644228`

Expected: it fetches text + the chart, the subagent returns a JSON with
`content_type: "setup"`, `symbol: "BTCUSDT"`, and a `gap_note`; a review digest is
shown; nothing is written until you approve; on approval a line is appended to
`docs/plans/pundit-calls.jsonl`. Confirm the image bytes never appeared in the main
transcript (only the subagent's JSON did).

- [ ] **Step 3: Lint + commit**

```bash
make lint-md
git add .claude/skills/ingest-x/SKILL.md
git commit -m "feat(x-ingest): /ingest-x orchestration skill"
```

---

## After all tasks

- Run the full DoD: `make lint-py && make typecheck && make test` (all green; goldens untouched — no engine/DB change).
- `/post-branch` for the docs sweep (CLAUDE.md tools list gains `tools/x_fetch.py` + the `/ingest-x` skill; the skills table gains a row), then `/pr-summary`.
- Update the SoT (`project_todo_master.md`): move the research-ingestion "Stream A/manual-paste" line forward — the X-URL adapter (iter-1) is now built; Stream C resolution/scoring stays Phase 2.
