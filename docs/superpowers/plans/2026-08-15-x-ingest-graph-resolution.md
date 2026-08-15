# X Ingest Graph Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/ingest-x` resolve an X post's full evidence graph — reply chain, quoted posts, and every image — and flag the evidence it provably cannot recover, so a subagent never extracts from a silently incomplete payload.

**Architecture:** Three independently shippable phases against one module. **A** adds two honesty flags parsed from fields already in the payload. **B** parses the quoted post's images and id, which are also already in the payload (zero extra network). **C** adds `resolve()`, a bounded breadth-first traversal that returns one flat bundle with roles, replacing the operator's undocumented two-pass `--batch`-then-`--thread` dance.

**Tech Stack:** Python 3.11+, Poetry, pytest, mypy strict, ruff. No new dependencies — `requests`, `json`, `pathlib` only.

**Spec:** `docs/superpowers/specs/2026-08-15-x-ingest-graph-resolution.md`

## Global Constraints

- **No network in tests.** Every test injects `get=` / `sleep=` / `rng=`. Fixtures are captured payloads on disk.
- **Fixtures live in `tests/data/x_payloads/`, NOT `tests/fixtures/`.** `tests/fixtures/` is on the backtest-surface list in `CLAUDE.md`, so a file added there makes `make test-regression` (~97s) mandatory on every future diff that touches it. This work cannot reach the backtest pipeline.
- **`text_truncated` is `bool(note_tweet)` and nothing else.** Spec §3.1 measured `display_text_range[1] < len(text)` as 1-for-3 wrong — it flags complete posts that carry an image.
- **Every new `XPost` field needs a default.** `_load_cached` does `XPost(**raw)` and pre-existing cache entries lack the keys (`tools/x_fetch.py:67-68` states this).
- **Every bound that bites appends to `notes`.** A truncated bundle that reads as complete is the defect this work removes.
- **No scraping, no login, no paid API.** `cdn.syndication.twimg.com/tweet-result` plus manual paste remain the only fetch paths.
- Definition of Done per task: `make lint-py` ✓, `make typecheck` ✓, `make test` ✓. `make test-regression` is **not** required — no file here is on the backtest surface.

## File Structure

| File | Responsibility | Phase |
| --- | --- | --- |
| `tools/x_fetch.py` | All fetching, parsing, caching, traversal. Stays one module — it is ~410 lines and cohesive. | A, B, C |
| `tests/test_x_fetch.py` | All tests. Existing file, ~675 lines, strict TDD conventions already established. | A, B, C |
| `tests/data/x_payloads/*.json` | Captured real payloads (new dir). | A, B |
| `.claude/skills/ingest-x/SKILL.md` | Operator-facing flow. | C |

---

### Task 1: Truncation and edited flags

**Files:**

- Modify: `tools/x_fetch.py:53-72` (XPost), `tools/x_fetch.py:109-125` (fetch_x_post return)
- Create: `tests/data/x_payloads/complete_with_image.json`, `truncated_longform.json`, `edited_post.json`
- Test: `tests/test_x_fetch.py`

**Interfaces:**

- Consumes: nothing (first task)
- Produces: `XPost.text_truncated: bool`, `XPost.edited: bool` — Task 3 reads both; Task 5 surfaces them in the digest.

- [ ] **Step 1: Capture the real payloads**

```bash
mkdir -p tests/data/x_payloads
PYTHONPATH=. poetry run python - <<'PY'
import json, urllib.request
from pathlib import Path
from tools.x_fetch import _SYNDICATION_URL, _TOKEN

IDS = {
    "complete_with_image": "2077667306172236028",   # reads complete, HAS an image
    "truncated_longform":  "2080609907561124004",   # note_tweet present
    "quote_with_image":    "2079877893694320817",   # quoted_tweet carries 1 photo
}
out = Path("tests/data/x_payloads")
out.mkdir(parents=True, exist_ok=True)
for name, tid in IDS.items():
    url = f"{_SYNDICATION_URL}?id={tid}&lang=en&token={_TOKEN}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.load(urllib.request.urlopen(req, timeout=30))
    (out / f"{name}.json").write_text(json.dumps(data, indent=2))
    print("wrote", name, "note_tweet=", bool(data.get("note_tweet")))
PY
```

Expected: three files written; `complete_with_image` prints `note_tweet= False`, the other two `True` / `False` respectively (`quote_with_image` is not long-form).

Then derive the edited fixture — **copy a real payload and flip one real field**, so the fixture keeps the true shape:

```bash
PYTHONPATH=. poetry run python - <<'PY'
import json
from pathlib import Path
p = Path("tests/data/x_payloads")
d = json.loads((p / "complete_with_image.json").read_text())
assert "isEdited" in d, "shape changed upstream — do not fabricate the key"
d["isEdited"] = True
(p / "edited_post.json").write_text(json.dumps(d, indent=2))
print("wrote edited_post.json")
PY
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_x_fetch.py`:

```python
_PAYLOAD_DIR = Path(__file__).parent / "data" / "x_payloads"


def _payload(name: str) -> str:
    return (_PAYLOAD_DIR / f"{name}.json").read_text()


def test_longform_post_flags_text_truncated() -> None:
    get = make_get(FakeResp(200, _payload("truncated_longform")))
    post = fetch_x_post("https://x.com/a/status/2080609907561124004", get=get)
    assert isinstance(post, XPost)
    assert post.text_truncated is True


def test_complete_post_with_image_does_not_flag_truncated() -> None:
    """Regression for spec 3.1.

    display_text_range[1] < len(text) is True for THIS post too, because the
    trailing t.co media link sits outside the display range. A range-based
    implementation passes the test above and fails this one.
    """
    get = make_get(FakeResp(200, _payload("complete_with_image")))
    post = fetch_x_post("https://x.com/a/status/2077667306172236028", get=get)
    assert isinstance(post, XPost)
    assert post.text_truncated is False


def test_edited_post_flags_edited() -> None:
    get = make_get(FakeResp(200, _payload("edited_post")))
    post = fetch_x_post("https://x.com/a/status/2077667306172236028", get=get)
    assert isinstance(post, XPost)
    assert post.edited is True


def test_unedited_post_does_not_flag_edited() -> None:
    get = make_get(FakeResp(200, _payload("complete_with_image")))
    post = fetch_x_post("https://x.com/a/status/2077667306172236028", get=get)
    assert isinstance(post, XPost)
    assert post.edited is False
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_x_fetch.py -k "truncated or edited" -v`
Expected: FAIL with `AttributeError: 'XPost' object has no attribute 'text_truncated'`

- [ ] **Step 4: Add the fields to `XPost`**

In `tools/x_fetch.py`, after the `quoted_author` line (`:66`) and before the `# Thread fields.` comment:

```python
    # Payload-honesty fields. Spec 2026-08-15 section 3.1: display_text_range is
    # NOT usable — it flags every post carrying media, because the trailing t.co
    # link sits outside the range. note_tweet is the only reliable signal, and it
    # holds an ID stub, never the body: the long text is detectable, not fetchable.
    text_truncated: bool = False
    edited: bool = False  # text may differ from what was posted at call time
```

- [ ] **Step 5: Parse them in `fetch_x_post`**

In the `return XPost(...)` block (`tools/x_fetch.py:109-125`), after `quoted_author=...`:

```text
        text_truncated=bool(data.get("note_tweet")),
        edited=bool(data.get("isEdited") or data.get("isStaleEdit")),
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_x_fetch.py -v`
Expected: PASS, all pre-existing tests included (the defaults keep cache round-trips working).

- [ ] **Step 7: Mutation-check the discriminator**

Temporarily replace the `text_truncated` expression with the tempting wrong one:

```text
        text_truncated=bool((data.get("display_text_range") or [0, 0])[1] < len(data.get("text", ""))),
```

Run: `poetry run pytest tests/test_x_fetch.py -k truncated -v`
Expected: `test_complete_post_with_image_does_not_flag_truncated` **FAILS**. If it passes, the fixture is wrong — it is not a post carrying an image. **Revert the mutation before continuing.**

- [ ] **Step 8: Gates and commit**

```bash
make lint-py && make typecheck && make test
git add tools/x_fetch.py tests/test_x_fetch.py tests/data/x_payloads/
git commit -m "feat(x-fetch): flag truncated long-form and edited posts"
```

---

### Task 2: Quoted-post images and id

**Files:**

- Modify: `tools/x_fetch.py` — `XPost` (`:53-72`), `fetch_x_post` (`:99-125`), `download_photos` (`:128-140`), `BatchResult` (`:143-148`), `_load_cached` (`:231-241`), `_write_cache` (`:244-253`), `fetch_x_batch` (`:281-306`)
- Test: `tests/test_x_fetch.py`

**Interfaces:**

- Consumes: `XPost.text_truncated`, `XPost.edited` (Task 1)
- Produces: `XPost.quoted_id: str`, `XPost.quoted_photo_urls: tuple[str, ...]`, `BatchResult.quoted_photo_paths: list[str]`, `download_quoted_photos(post, dest_dir, *, get) -> list[Path]`. Task 3 uses `quoted_id` as its traversal edge.

> **Scope note.** This task gives the quoted post's **images**, not its truncation state — spec §3.1 measured that `quoted_tweet` sub-payloads carry no `note_tweet` key even when the quoted body is long-form. Detecting that needs the full re-fetch, which is Task 3. Both are real deliverables; neither subsumes the other.

- [ ] **Step 1: Write the failing tests**

```python
def test_quoted_tweet_photos_and_id_are_parsed() -> None:
    get = make_get(FakeResp(200, _payload("quote_with_image")))
    post = fetch_x_post("https://x.com/a/status/2079877893694320817", get=get)
    assert isinstance(post, XPost)
    assert post.quoted_id == "2062887573790359920"
    assert len(post.quoted_photo_urls) == 1
    assert post.quoted_photo_urls[0].endswith("?name=orig")


def test_quoted_photos_download_to_a_separate_dir(tmp_path: Path) -> None:
    post = XPost(
        source="twitter",
        author="a",
        author_name="A",
        url="https://x.com/a/status/1",
        post_ts_utc="",
        text="t",
        photo_urls=("https://pbs.twimg.com/media/PARENT.jpg?name=orig",),
        video_present=False,
        is_thread=False,
        is_quote=True,
        quoted_photo_urls=("https://pbs.twimg.com/media/QUOTED.jpg?name=orig",),
    )
    get = make_get(FakeResp(200, content=b"bytes"))
    parent = download_photos(post, tmp_path / "1", get=get)
    quoted = download_quoted_photos(post, tmp_path / "1_quoted", get=get)
    assert [p.name for p in parent] == ["0.jpg"]
    assert [p.name for p in quoted] == ["0.jpg"]
    assert parent[0] != quoted[0]


def test_batch_downloads_quoted_photos_and_caches_them(tmp_path: Path) -> None:
    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            return FakeResp(200, _payload("quote_with_image"))
        return FakeResp(200, content=b"img")

    results = fetch_x_batch(
        ["https://x.com/a/status/2079877893694320817"],
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert len(results[0].quoted_photo_paths) == 1
    assert "_quoted" in results[0].quoted_photo_paths[0]
    # and the cache round-trips it
    again = fetch_x_batch(
        ["https://x.com/a/status/2079877893694320817"],
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert again[0].cached is True
    assert again[0].quoted_photo_paths == results[0].quoted_photo_paths
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_x_fetch.py -k quoted -v`
Expected: FAIL — `TypeError: XPost.__init__() got an unexpected keyword argument 'quoted_photo_urls'`

- [ ] **Step 3: Add the fields to `XPost`**

After `quoted_author` (`:66`):

```text
    quoted_id: str = ""  # quoted_tweet.id_str — the traversal edge for resolve()
    quoted_photo_urls: tuple[str, ...] = ()  # quoted_tweet.photos, present but unread until now
```

- [ ] **Step 4: Parse them in `fetch_x_post`**

Replace the `quoted`/`quoted_user` block (`:107-108`) and extend the return. After line `:108`:

```python
    quoted_photos = (
        tuple(
            _orig(p["url"])
            for p in (quoted.get("photos") or [])
            if isinstance(p, dict) and p.get("url")
        )
        if isinstance(quoted, dict)
        else ()
    )
```

and in the `return XPost(...)`:

```text
        quoted_id=str(quoted.get("id_str") or "") if isinstance(quoted, dict) else "",
        quoted_photo_urls=quoted_photos,
```

- [ ] **Step 5: Split the downloader and add the quoted variant**

Replace `download_photos` (`:128-140`) with:

```python
def _download_urls(
    urls: tuple[str, ...], dest_dir: Path, *, get: HttpGet = _requests_get
) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, photo_url in enumerate(urls):
        resp = get(photo_url, headers={"User-Agent": _UA})
        if resp.status_code != 200:
            continue
        out = dest_dir / f"{i}.jpg"
        out.write_bytes(resp.content)
        paths.append(out)
    return paths


def download_photos(
    post: XPost, dest_dir: Path, *, get: HttpGet = _requests_get
) -> list[Path]:
    return _download_urls(post.photo_urls, dest_dir, get=get)


def download_quoted_photos(
    post: XPost, dest_dir: Path, *, get: HttpGet = _requests_get
) -> list[Path]:
    """The quoted post's charts. Its images ride in the SAME payload as the
    containing post, so this costs no extra request — they were simply unread."""
    return _download_urls(post.quoted_photo_urls, dest_dir, get=get)
```

- [ ] **Step 6: Carry quoted paths through `BatchResult` and the cache**

`BatchResult` (`:143-148`) gains one field:

```python
    quoted_photo_paths: list[str] = field(default_factory=list)
```

`_load_cached` (`:231-241`) returns a 3-tuple — restore the tuple field and read the new key:

```python
def _load_cached(
    cache_dir: Path, tweet_id: str
) -> tuple[XPost, list[str], list[str]] | None:
    path = _cache_path(cache_dir, tweet_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        raw = dict(data["post"])
        raw["photo_urls"] = tuple(raw.get("photo_urls", ()))
        raw["quoted_photo_urls"] = tuple(raw.get("quoted_photo_urls", ()))
        return (
            XPost(**raw),
            list(data.get("photo_paths", [])),
            list(data.get("quoted_photo_paths", [])),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None  # corrupt cache ⇒ treat as a miss, re-fetch
```

`_write_cache` (`:244-253`) takes and stores the extra list:

```python
def _write_cache(
    cache_dir: Path,
    tweet_id: str,
    post: XPost,
    photo_paths: list[str],
    quoted_photo_paths: list[str],
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "post": asdict(post),
        "photo_paths": photo_paths,
        "quoted_photo_paths": quoted_photo_paths,
        "fetched_at_utc": datetime.now(UTC).isoformat(),
    }
    _cache_path(cache_dir, tweet_id).write_text(json.dumps(payload, indent=2))
```

**`walk_thread` needs no change** — it reads `cached[0]` by index (`tools/x_fetch.py:199`), which is unaffected by the extra element.

- [ ] **Step 7: Wire it into `fetch_x_batch`**

In `fetch_x_batch`, change the cache-hit unpack (`:284`) and the download/write block (`:298-306`):

```python
                post, photo_paths, quoted_photo_paths = cached
                results.append(
                    BatchResult(
                        url=url,
                        post=post,
                        photo_paths=photo_paths,
                        quoted_photo_paths=quoted_photo_paths,
                        cached=True,
                    )
                )
                continue
```

```python
        photo_paths = [
            str(p) for p in download_photos(post_or_err, media_root / tweet_id, get=get)
        ]
        quoted_photo_paths = [
            str(p)
            for p in download_quoted_photos(
                post_or_err, media_root / f"{tweet_id}_quoted", get=get
            )
        ]
        _write_cache(cache_dir, tweet_id, post_or_err, photo_paths, quoted_photo_paths)
        results.append(
            BatchResult(
                url=url,
                post=post_or_err,
                photo_paths=photo_paths,
                quoted_photo_paths=quoted_photo_paths,
                cached=False,
            )
        )
```

- [ ] **Step 8: Update the JSON emitter**

In `_main`'s batch serialiser (`tools/x_fetch.py:327`, the dict containing `"photo_paths": result.photo_paths`), add:

```python
        "quoted_photo_paths": result.quoted_photo_paths,
```

- [ ] **Step 9: Run the tests**

Run: `poetry run pytest tests/test_x_fetch.py -v`
Expected: PASS. The pre-existing cache test at `tests/test_x_fetch.py:646` unpacks `_load_cached` — if it indexes `loaded[0]` it is unaffected; if it unpacks two names, update it to three.

- [ ] **Step 10: Gates and commit**

```bash
make lint-py && make typecheck && make test
git add tools/x_fetch.py tests/test_x_fetch.py
git commit -m "feat(x-fetch): parse and download quoted-post images and id"
```

---

### Task 3: `resolve()` — the bundle

**Files:**

- Modify: `tools/x_fetch.py` — `ThreadChain` (`:151-156`), `walk_thread` (`:159-224`); append `ResolvedPost`, `Bundle`, `resolve`
- Test: `tests/test_x_fetch.py`

**Interfaces:**

- Consumes: `XPost.quoted_id` (Task 2), `walk_thread`, `fetch_x_batch`
- Produces: `resolve(url, *, max_quote_depth=2, max_hops=25, cache_dir, media_root, min_delay, max_delay, get, sleep, rng) -> Bundle`; `Bundle.posts: list[ResolvedPost]`, `Bundle.notes: list[str]`; `ResolvedPost(post, role, depth, referred_by, photo_paths, quoted_photo_paths)`. Task 4 serialises this.

- [ ] **Step 1: Write the failing tests**

```python
def test_resolve_returns_chain_with_roles_and_photos(tmp_path: Path) -> None:
    """A 2-post self-thread: the bookmarked leaf plus its parent, both with images."""
    leaf = _thread_meta(
        text="leaf",
        tid="200",
        reply_to="100",
        author="a",
        photos=["https://pbs.twimg.com/media/LEAF.jpg"],
    )
    root = _thread_meta(
        text="root",
        tid="100",
        reply_to=None,
        author="a",
        photos=["https://pbs.twimg.com/media/ROOT.jpg"],
    )
    bodies = {"200": leaf, "100": root}

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            tid = re.search(r"id=(\d+)", url).group(1)  # type: ignore[union-attr]
            return FakeResp(200, bodies[tid])
        return FakeResp(200, content=b"img")

    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert [rp.role for rp in bundle.posts] == ["chain_parent", "bookmarked"]
    # every post in the bundle carries LOCAL paths — not just the bookmarked one
    assert all(rp.photo_paths for rp in bundle.posts)


def test_resolve_pulls_in_quoted_post(tmp_path: Path) -> None:
    main_post = _thread_meta(
        text="main", tid="200", reply_to=None, author="a", quoted_id="900"
    )
    quoted = _thread_meta(text="quoted", tid="900", reply_to=None, author="b")
    bodies = {"200": main_post, "900": quoted}

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            tid = re.search(r"id=(\d+)", url).group(1)  # type: ignore[union-attr]
            return FakeResp(200, bodies[tid])
        return FakeResp(200, content=b"img")

    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    roles = {rp.post.author: rp.role for rp in bundle.posts}
    assert roles == {"a": "bookmarked", "b": "quoted"}
    quoted_rp = next(rp for rp in bundle.posts if rp.role == "quoted")
    assert quoted_rp.referred_by == "200"
    assert quoted_rp.depth == 1


def test_resolve_terminates_on_a_quote_cycle(tmp_path: Path) -> None:
    a = _thread_meta(text="a", tid="200", reply_to=None, author="a", quoted_id="900")
    b = _thread_meta(text="b", tid="900", reply_to=None, author="b", quoted_id="200")
    bodies = {"200": a, "900": b}

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            tid = re.search(r"id=(\d+)", url).group(1)  # type: ignore[union-attr]
            return FakeResp(200, bodies[tid])
        return FakeResp(200, content=b"img")

    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    ids = [rp.post.url.rsplit("/", 1)[-1] for rp in bundle.posts]
    assert sorted(ids) == ["200", "900"]  # each exactly once
    assert any("already in bundle" in n for n in bundle.notes)


def test_resolve_respects_max_quote_depth(tmp_path: Path) -> None:
    chain = {
        "200": _thread_meta(
            text="a", tid="200", reply_to=None, author="a", quoted_id="300"
        ),
        "300": _thread_meta(
            text="b", tid="300", reply_to=None, author="b", quoted_id="400"
        ),
        "400": _thread_meta(
            text="c", tid="400", reply_to=None, author="c", quoted_id="500"
        ),
        "500": _thread_meta(text="d", tid="500", reply_to=None, author="d"),
    }

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            tid = re.search(r"id=(\d+)", url).group(1)  # type: ignore[union-attr]
            return FakeResp(200, chain[tid])
        return FakeResp(200, content=b"img")

    bundle = resolve(
        "https://x.com/a/status/200",
        max_quote_depth=2,
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert [rp.post.author for rp in bundle.posts] == ["a", "b", "c"]
    assert any("max_quote_depth" in n for n in bundle.notes)
```

- [ ] **Step 2: Extend the `_thread_meta` helper**

The existing helper at `tests/test_x_fetch.py:460` builds thread payloads. Give it the two keyword arguments the tests above use, keeping current call sites working:

```python
def _thread_meta(
    *,
    text: str,
    tid: str,
    reply_to: str | None,
    author: str,
    photos: list[str] | None = None,
    quoted_id: str | None = None,
) -> str:
    body: dict[str, Any] = {
        "__typename": "Tweet",
        "text": text,
        "created_at": "2026-07-01T00:00:00.000Z",
        "user": {"screen_name": author, "name": author.upper()},
        "photos": [{"url": u} for u in (photos or [])],
        "mediaDetails": [],
        "in_reply_to_status_id_str": reply_to,
        "in_reply_to_screen_name": author if reply_to else None,
        "conversation_count": 0,
        "id_str": tid,
    }
    if quoted_id:
        body["quoted_tweet"] = {
            "id_str": quoted_id,
            "text": f"quoted {quoted_id}",
            "user": {"screen_name": "q", "name": "Q"},
            "photos": [],
        }
    return json.dumps(body)
```

If the existing helper has a different signature, adapt these calls rather than breaking its current callers.

- [ ] **Step 3: Run to verify the tests fail**

Run: `poetry run pytest tests/test_x_fetch.py -k resolve -v`
Expected: FAIL with `ImportError: cannot import name 'resolve'`

- [ ] **Step 4: Let `walk_thread` download**

`ThreadChain` gains a per-id path map:

```python
@dataclass
class ThreadChain:
    """One author's self-thread, root -> leaf, plus why the walk stopped."""

    posts: list[XPost] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    photo_paths: dict[str, list[str]] = field(
        default_factory=dict
    )  # tweet id -> local files
```

`walk_thread` gains two keyword arguments:

```text
    download: bool = False,
    media_root: Path = Path(".cache/x-media"),
```

Inside the loop, replace the cache-hit branch and the fetch branch so a downloading walk also writes a **complete** cache entry. The existing docstring explains why the non-downloading walk must not write one — that reason is satisfied here precisely because the photos are fetched:

```python
        cached = _load_cached(cache_dir, tweet_id)
        if cached is not None:
            post = cached[0]
            if download:
                photo_paths[tweet_id] = cached[1]
        else:
            if did_network:
                sleep(rng.uniform(min_delay, max_delay))
            did_network = True
            fetched = fetch_x_post(next_url, get=get)
            if isinstance(fetched, Unavailable):
                notes.append(f"stopped at {tweet_id}: {fetched.reason}")
                break
            post = fetched
            if download:
                own = [str(p) for p in download_photos(post, media_root / tweet_id, get=get)]
                quoted_own = [
                    str(p)
                    for p in download_quoted_photos(
                        post, media_root / f"{tweet_id}_quoted", get=get
                    )
                ]
                _write_cache(cache_dir, tweet_id, post, own, quoted_own)
                photo_paths[tweet_id] = own
```

Declare `photo_paths: dict[str, list[str]] = {}` beside `walked`/`notes`, and pass it into the returned `ThreadChain`.

- [ ] **Step 5: Add the bundle types and `resolve`**

Append to `tools/x_fetch.py`:

```python
@dataclass(frozen=True)
class ResolvedPost:
    post: XPost
    role: str  # "bookmarked" | "chain_parent" | "quoted"
    depth: int  # 0 for the chain, +1 per quote hop
    referred_by: str  # status id that pulled this in; "" for the bookmarked post
    photo_paths: list[str] = field(default_factory=list)
    quoted_photo_paths: list[str] = field(default_factory=list)


@dataclass
class Bundle:
    posts: list[ResolvedPost] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def resolve(
    url: str,
    *,
    max_quote_depth: int = 2,
    max_hops: int = 25,
    cache_dir: Path = Path(".cache/x-posts"),
    media_root: Path = Path(".cache/x-media"),
    min_delay: float = 4.0,
    max_delay: float = 12.0,
    get: HttpGet = _requests_get,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> Bundle:
    """Resolve a post's whole evidence graph into one flat bundle.

    Walks the reply chain UPWARD (the endpoint has no children field, so a
    thread is only recoverable from its last post), then resolves every quoted
    post breadth-first to ``max_quote_depth``, downloading images for each.

    ``visited`` is mandatory rather than defensive: a quote cycle is
    constructible, and self-quoting authors make near-cycles routine. Every
    bound that bites appends to ``notes`` — a truncated bundle that read as a
    complete one is the defect this function exists to remove.
    """
    rng = rng or random.Random()
    bookmarked_id = parse_tweet_id(url)
    chain = walk_thread(
        url,
        max_hops=max_hops,
        cache_dir=cache_dir,
        media_root=media_root,
        download=True,
        min_delay=min_delay,
        max_delay=max_delay,
        get=get,
        sleep=sleep,
        rng=rng,
    )
    bundle = Bundle(notes=list(chain.notes))
    visited: set[str] = set()
    queue: list[tuple[str, str, int]] = []  # (quoted id, referrer id, depth)

    for post in chain.posts:
        tid = parse_tweet_id(post.url)
        visited.add(tid)
        bundle.posts.append(
            ResolvedPost(
                post=post,
                role="bookmarked" if tid == bookmarked_id else "chain_parent",
                depth=0,
                referred_by="",
                photo_paths=chain.photo_paths.get(tid, []),
            )
        )
        if post.quoted_id:
            queue.append((post.quoted_id, tid, 1))

    while queue:
        quoted_id, referrer, depth = queue.pop(0)
        if quoted_id in visited:
            bundle.notes.append(
                f"skipped {quoted_id}: already in bundle (quoted by {referrer})"
            )
            continue
        if depth > max_quote_depth:
            bundle.notes.append(
                f"skipped {quoted_id}: max_quote_depth={max_quote_depth} reached — "
                "quoted context beyond this depth was NOT fetched"
            )
            continue
        visited.add(quoted_id)
        quoted_url = f"https://x.com/i/status/{quoted_id}"
        result = fetch_x_batch(
            [quoted_url],
            cache_dir=cache_dir,
            media_root=media_root,
            min_delay=min_delay,
            max_delay=max_delay,
            get=get,
            sleep=sleep,
            rng=rng,
        )[0]
        if isinstance(result.post, Unavailable):
            bundle.notes.append(
                f"quoted {quoted_id} unavailable: {result.post.reason} — "
                "its text may still be on the referrer as quoted_text"
            )
            continue
        bundle.posts.append(
            ResolvedPost(
                post=result.post,
                role="quoted",
                depth=depth,
                referred_by=referrer,
                photo_paths=result.photo_paths,
                quoted_photo_paths=result.quoted_photo_paths,
            )
        )
        if result.post.quoted_id:
            queue.append((result.post.quoted_id, quoted_id, depth + 1))

    return bundle
```

- [ ] **Step 6: Run the tests**

Run: `poetry run pytest tests/test_x_fetch.py -v`
Expected: PASS

- [ ] **Step 7: Mutation-check the scope of the photo assertion**

`test_resolve_returns_chain_with_roles_and_photos` asserts `all(rp.photo_paths ...)`. Confirm it is scoped, not merely reached: temporarily change Step 4's cache-hit branch to populate `photo_paths` for the leaf only —

```python
            if download and tweet_id == parse_tweet_id(url):
```

Run: `poetry run pytest tests/test_x_fetch.py -k resolve_returns_chain -v`
Expected: **FAIL.** A test asserting only "some post has paths" would pass here, which is exactly the reachability-vs-scope gap. **Revert the mutation.**

- [ ] **Step 8: Gates and commit**

```bash
make lint-py && make typecheck && make test
git add tools/x_fetch.py tests/test_x_fetch.py
git commit -m "feat(x-fetch): add resolve() bundling chain, quotes and media"
```

---

### Task 4: `--resolve` CLI

**Files:**

- Modify: `tools/x_fetch.py` — argparse block (`:346-362`), `main` dispatch (`:366+`)
- Test: `tests/test_x_fetch.py`

**Interfaces:**

- Consumes: `resolve`, `Bundle`, `ResolvedPost` (Task 3)
- Produces: `python tools/x_fetch.py <url> --resolve --json` emitting `{"posts": [...], "notes": [...]}`, each post carrying `role`, `depth`, `referred_by`, `photo_paths`, `quoted_photo_paths`, and the flat `XPost` fields. Task 5's skill step 1 calls exactly this.

- [ ] **Step 1: Write the failing test**

```python
def test_main_resolve_json_emits_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _thread_meta(text="only", tid="200", reply_to=None, author="a")

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            return FakeResp(200, body)
        return FakeResp(200, content=b"img")

    rc = main(
        [
            "https://x.com/a/status/200",
            "--resolve",
            "--json",
            "--cache-dir",
            str(tmp_path / "posts"),
            "--media-root",
            str(tmp_path / "media"),
            "--min-delay",
            "0",
            "--max-delay",
            "0",
        ],
        get=routed_get,
        sleep=lambda _s: None,
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["posts"][0]["role"] == "bookmarked"
    assert payload["posts"][0]["depth"] == 0
    assert "notes" in payload
```

Match the injection style of the existing `test_main_batch_json_emits_array` at `tests/test_x_fetch.py:400` — if `main` there takes `get`/`sleep` differently, mirror that signature exactly rather than inventing one.

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/test_x_fetch.py -k main_resolve -v`
Expected: FAIL — argparse errors on the unknown `--resolve` flag.

- [ ] **Step 3: Add the flag**

Beside the `--thread` argument (`tools/x_fetch.py:350`):

```python
    parser.add_argument(
        "--resolve",
        action="store_true",
        help="resolve the whole evidence graph: reply chain + quoted posts + all media",
    )
    parser.add_argument(
        "--max-quote-depth",
        type=int,
        default=2,
        help="how many quote hops to follow (default 2)",
    )
```

- [ ] **Step 4: Dispatch it**

Immediately **before** the `if args.thread:` branch (`:366`), so `--resolve` wins when both are passed:

```python
    if args.resolve:
        rc = 0
        for url in args.urls:
            bundle = resolve(
                url,
                max_quote_depth=args.max_quote_depth,
                max_hops=args.max_hops,
                cache_dir=Path(args.cache_dir),
                media_root=Path(args.media_root),
                min_delay=args.min_delay,
                max_delay=args.max_delay,
                get=get,
                sleep=sleep,
            )
            if not bundle.posts:
                print(f"UNAVAILABLE: {url}", file=sys.stderr)
                rc = 1
                continue
            if args.json:
                print(
                    json.dumps(
                        {
                            "posts": [
                                {
                                    **asdict(rp.post),
                                    "role": rp.role,
                                    "depth": rp.depth,
                                    "referred_by": rp.referred_by,
                                    "photo_paths": rp.photo_paths,
                                    "quoted_photo_paths": rp.quoted_photo_paths,
                                }
                                for rp in bundle.posts
                            ],
                            "notes": bundle.notes,
                        },
                        indent=2,
                    )
                )
            else:
                for rp in bundle.posts:
                    print(f"[{rp.role} d{rp.depth}] {_format_human(rp.post)}")
                for note in bundle.notes:
                    print(f"  ! {note}")
        return rc
```

If `--max-hops` is not already an argparse argument, add it with `type=int, default=25`.

- [ ] **Step 5: Run the tests**

Run: `poetry run pytest tests/test_x_fetch.py -v`
Expected: PASS

- [ ] **Step 6: Smoke-test against the real endpoint**

```bash
PYTHONPATH=. poetry run python tools/x_fetch.py \
  https://x.com/sergio_tesla_/status/2088284971131646041 --resolve --json | head -40
```

Expected: a 4-post chain with `role` values `chain_parent ×3` then `bookmarked`, non-empty `photo_paths` on the three posts that carry charts, and the self-quotes resolved. **This is the case that previously needed two commands and an undocumented second pass.**

- [ ] **Step 7: Gates and commit**

```bash
make lint-py && make typecheck && make test
git add tools/x_fetch.py tests/test_x_fetch.py
git commit -m "feat(x-fetch): add --resolve CLI emitting the evidence bundle"
```

---

### Task 5: Rewrite the skill flow

**Files:**

- Modify: `.claude/skills/ingest-x/SKILL.md` — step 1 / 1a, step 2 schema, step 3 digest

**Interfaces:**

- Consumes: the `--resolve --json` contract (Task 4)
- Produces: no code. The operator-facing flow.

- [ ] **Step 1: Replace step 1 and step 1a with one command**

Step 1's fetch command becomes:

```bash
PYTHONPATH=. poetry run python tools/x_fetch.py <url1> <url2> … --resolve --json
```

Document the output as `{"posts": [...], "notes": [...]}` per URL, each post carrying `role` (`bookmarked` / `chain_parent` / `quoted`), `depth`, `referred_by`, `photo_paths`, `quoted_photo_paths`, `text_truncated`, `edited`.

**Delete** the instruction to re-run `--batch` over recovered parents — it is now unnecessary, and leaving it would have a session download twice. Keep the operator rule verbatim: **bookmark the LAST post of a thread**, since the endpoint still has no children field.

Keep both existing traps and add the new one:

- `conversation_count` is NOT thread length.
- Always read `notes` — a walk that stopped early says so there.
- **New:** a `text_truncated: true` post is missing a long-form tail that no keyless path can fetch. Say so to the extractor rather than letting it extrapolate.

- [ ] **Step 2: Add the level-format contract to the step-2 schema**

The `entry` / `stop` / `target` fields in step 2's JSON schema block gain, verbatim:

> Write each as a BARE NUMBER — no commentary, no parentheticals, no hyphenated ranges. These fields are machine-parsed and exactly one number is taken from each; a hyphenated range beats a `/`-separated ladder and resolves to the range's LOW end. If not stated, use `""`.

Step 4 already carries this rule where the row is *written*, but the subagent that *produces* the values never saw it — and on 2026-08-15 it returned prose with parentheticals for all three fields. That was harmless only because `direction: neutral` short-circuits before the parse.

Also instruct the subagent to name any `text_truncated` post in its `chart_read` or `gap_note`, so a partial reading is visible rather than inferred.

- [ ] **Step 3: Add digest columns**

Step 3's consolidated table gains **truncated?** and **edited?** columns, on every row. Same principle that put `is_retrospective` on every row: the operator decides, but only about what they can see. Note beside the table that a `quoted` post appears as its own row with its `referred_by` shown, so attribution is visible before approval.

- [ ] **Step 4: Verify the skill's own claims**

Re-read the edited SKILL.md against the implementation and confirm every command in it runs as written:

```bash
PYTHONPATH=. poetry run python tools/x_fetch.py https://x.com/sergio_tesla_/status/2079877893694320817 --resolve --json | python3 -c "import json,sys; d=json.load(sys.stdin); print([(p['role'],p['text_truncated'],len(p['photo_paths'])) for p in d['posts']])"
```

Expected: the bookmarked post plus its quoted post, with the quoted post's image count non-zero — the case that returned nothing before this work.

- [ ] **Step 5: Lint and commit**

```bash
make lint-md
git add .claude/skills/ingest-x/SKILL.md
git commit -m "docs(ingest-x): resolve the whole evidence graph in one step"
```

---

## Self-Review

**Spec coverage.** §4.1 flags → Task 1. §4.2 quoted promotion → Task 2 (images/id) + Task 3 (full re-fetch, which is what gives quoted posts their own truncation detection). §4.3 `resolve` → Tasks 3–4. §4.4 skill changes → Task 5, including both carried-over fixes. §6 acceptance cases 1–4 and 6–8 map to named tests; **case 5 (third-party quote) has no fixture and is deliberately not implemented** — the corpus contains zero genuine third-party quotes, so the plan cannot honestly test it. Task 3's `test_resolve_pulls_in_quoted_post` uses authors `a` and `b` and therefore exercises the *mechanism*, but not against a real payload. §7 failure modes are handled: amplification by `max_quote_depth`, unavailable quoted post by the `Unavailable` branch, over-collection by `role`/`depth`.

**Open questions left open on purpose.** Spec §10 asks whether a third-party quoted post routes as its own item and whether `edited: true` should block Stream C. This plan surfaces both fields and routes neither differently — deciding without a real case means inventing the test too.

**Type consistency.** `text_truncated`, `edited`, `quoted_id`, `quoted_photo_urls`, `quoted_photo_paths`, `photo_paths`, `role`, `depth`, `referred_by` are used identically across Tasks 1–5. `_load_cached` returns a 3-tuple from Task 2 onward; `walk_thread`'s `cached[0]` index access is unaffected, and `fetch_x_batch`'s unpack is updated in the same task.
