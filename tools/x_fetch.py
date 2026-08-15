"""Read-only X/Twitter post fetcher via the public syndication endpoint.

No auth, no scraping: hits cdn.syndication.twimg.com/tweet-result (the data path
that powers embedded tweets) and maps the JSON into an XPost. A non-empty `token`
query param is required but its value is not validated by the endpoint, so a fixed
dummy is used (verified 2026-06-30). Mirrors tools/journal_fetch.py: read-only,
HTTP injectable for tests, CLI for ad-hoc use.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import requests

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
    return requests.get(url, headers=headers, timeout=20)  # type: ignore[return-value]


def parse_tweet_id(url: str) -> str:
    match = _ID_RE.search(url)
    if not match:
        raise ValueError(f"not an X/Twitter status URL: {url!r}")
    return match.group(1)


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
    quoted_text: str = ""  # nested quoted_tweet body, best-effort
    quoted_author: str = ""  # nested quoted_tweet @handle, best-effort
    quoted_id: str = ""  # quoted_tweet.id_str — the traversal edge for resolve()
    quoted_photo_urls: tuple[
        str, ...
    ] = ()  # quoted_tweet.photos, present but unread until now
    # Payload-honesty fields. Spec 2026-08-15 section 3.1: display_text_range is
    # NOT usable — it flags every post carrying media, because the trailing t.co
    # link sits outside the range. note_tweet is the only reliable signal, and it
    # holds an ID stub, never the body: the long text is detectable, not fetchable.
    text_truncated: bool = False
    edited: bool = False  # text may differ from what was posted at call time
    # Thread fields. Defaults are load-bearing: _load_cached does XPost(**raw) and
    # every cache entry written before these existed lacks the keys.
    in_reply_to_id: str = ""  # the post this one replies to, "" at the root
    in_reply_to_author: str = ""  # @handle replied to; == author on a self-thread
    conversation_count: int = 0  # replies to the CONVERSATION, never thread length
    thread_pos: int = 0  # 0 = root, ascending toward the leaf


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
    if not isinstance(data, dict):
        return Unavailable("unexpected JSON shape")
    if not data or data.get("__typename") == "TweetTombstone" or "text" not in data:
        return Unavailable("post unavailable (protected/deleted/age-gated)")
    media = data.get("mediaDetails") or []
    video_present = any(m.get("type") in ("video", "animated_gif") for m in media)
    photos = tuple(
        _orig(p["url"])
        for p in (data.get("photos") or [])
        if isinstance(p, dict) and p.get("url")
    )
    user = data.get("user") or {}
    quoted = data.get("quoted_tweet") or {}
    quoted_user = quoted.get("user") or {} if isinstance(quoted, dict) else {}
    quoted_photos = (
        tuple(
            _orig(p["url"])
            for p in (quoted.get("photos") or [])
            if isinstance(p, dict) and p.get("url")
        )
        if isinstance(quoted, dict)
        else ()
    )
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
        quoted_text=quoted.get("text", "") if isinstance(quoted, dict) else "",
        quoted_author=quoted_user.get("screen_name", ""),
        quoted_id=str(quoted.get("id_str") or "") if isinstance(quoted, dict) else "",
        quoted_photo_urls=quoted_photos,
        text_truncated=bool(data.get("note_tweet")),
        edited=bool(data.get("isEdited") or data.get("isStaleEdit")),
        in_reply_to_id=str(data.get("in_reply_to_status_id_str") or ""),
        in_reply_to_author=str(data.get("in_reply_to_screen_name") or ""),
        conversation_count=int(data.get("conversation_count") or 0),
    )


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


@dataclass(frozen=True)
class BatchResult:
    url: str
    post: XPost | Unavailable
    photo_paths: list[str] = field(default_factory=list)
    quoted_photo_paths: list[str] = field(default_factory=list)
    cached: bool = False


@dataclass
class ThreadChain:
    """One author's self-thread, root -> leaf, plus why the walk stopped."""

    posts: list[XPost] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    photo_paths: dict[str, list[str]] = field(
        default_factory=dict
    )  # tweet id -> local files
    quoted_photo_paths: dict[str, list[str]] = field(
        default_factory=dict
    )  # tweet id -> local files of ITS OWN embedded quoted post


def walk_thread(
    url: str,
    *,
    max_hops: int = 25,
    cache_dir: Path = Path(".cache/x-posts"),
    download: bool = False,
    media_root: Path = Path(".cache/x-media"),
    min_delay: float = 4.0,
    max_delay: float = 12.0,
    get: HttpGet = _requests_get,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> ThreadChain:
    """Walk a self-thread UPWARD from its tail and return it root -> leaf.

    The syndication endpoint exposes ``in_reply_to_status_id_str`` but has no
    replies/children field, so a thread can only be recovered from its LAST post
    — bookmarking the parent yields nothing below it.

    Stops at the root, on an author change (climbing further would harvest a
    different pundit's words), at ``max_hops``, or on an unavailable hop. Every
    stop except reaching the root records a note; a truncated chain that reported
    nothing would read as a complete one. Never raises on a broken chain.

    Reads the per-id cache so an ancestor already ingested costs no network. With
    ``download=False`` (the default) it does not WRITE the cache: cache entries
    carry ``photo_paths``, and writing one here with no photos would make a later
    ingest of that post skip its chart download. With ``download=True`` it fetches
    each hop's own and quoted-post images before writing the cache entry, so the
    entry is complete precisely because the photos were downloaded first — the
    same reasoning, satisfied rather than bypassed.
    """
    rng = rng or random.Random()
    walked: list[XPost] = []
    notes: list[str] = []
    photo_paths: dict[str, list[str]] = {}
    quoted_photo_paths: dict[str, list[str]] = {}
    did_network = False
    next_url: str | None = url
    while next_url is not None:
        if len(walked) >= max_hops:
            notes.append(
                f"stopped: max_hops={max_hops} reached — chain truncated, "
                "older posts were NOT fetched"
            )
            break
        tweet_id = parse_tweet_id(next_url)
        cached = _load_cached(cache_dir, tweet_id)
        if cached is not None:
            post = cached[0]
            if download:
                photo_paths[tweet_id] = cached[1]
                quoted_photo_paths[tweet_id] = cached[2]
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
                own = [
                    str(p)
                    for p in download_photos(post, media_root / tweet_id, get=get)
                ]
                quoted_own = [
                    str(p)
                    for p in download_quoted_photos(
                        post, media_root / f"{tweet_id}_quoted", get=get
                    )
                ]
                _write_cache(cache_dir, tweet_id, post, own, quoted_own)
                photo_paths[tweet_id] = own
                quoted_photo_paths[tweet_id] = quoted_own
        walked.append(post)
        if not post.in_reply_to_id:
            break  # root reached — the normal stop, no note
        if post.in_reply_to_author and post.in_reply_to_author != post.author:
            notes.append(
                f"stopped: @{post.author} is replying to @{post.in_reply_to_author}, "
                "not self-threading"
            )
            break
        next_url = f"https://x.com/{post.author}/status/{post.in_reply_to_id}"
    walked.reverse()
    return ThreadChain(
        posts=[replace(p, thread_pos=i) for i, p in enumerate(walked)],
        notes=notes,
        photo_paths=photo_paths,
        quoted_photo_paths=quoted_photo_paths,
    )


def _cache_path(cache_dir: Path, tweet_id: str) -> Path:
    return cache_dir / f"{tweet_id}.json"


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


def fetch_x_batch(
    urls: list[str],
    *,
    cache_dir: Path = Path(".cache/x-posts"),
    media_root: Path = Path(".cache/x-media"),
    min_delay: float = 4.0,
    max_delay: float = 12.0,
    force: bool = False,
    delay_first: bool = False,
    get: HttpGet = _requests_get,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> list[BatchResult]:
    """Fetch several posts once each, with a randomized cooldown between *network*
    fetches and a per-id dedup cache (re-runs hit zero network). Cache hits add no
    pause; the first network fetch is never delayed — UNLESS ``delay_first=True``,
    for a caller that issues one call per post (``resolve``'s per-quote-hop
    fetches): from this function's point of view each such call's first fetch
    looks ungated, so without the flag the randomized cooldown between requests
    that Spec 2026-08-15 §7 mandates is silently skipped on every hop after the
    first. ``sleep``/``rng``/``get`` are injected for deterministic tests.
    Downloads charts once (no double-fetch)."""
    rng = rng or random.Random()
    results: list[BatchResult] = []
    did_network = False
    for url in urls:
        try:
            tweet_id = parse_tweet_id(url)
        except ValueError as exc:
            results.append(BatchResult(url=url, post=Unavailable(str(exc))))
            continue
        if not force:
            cached = _load_cached(cache_dir, tweet_id)
            if cached is not None:
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
        if did_network or delay_first:
            sleep(rng.uniform(min_delay, max_delay))
        did_network = True
        post_or_err = fetch_x_post(url, get=get)
        if isinstance(post_or_err, Unavailable):
            results.append(BatchResult(url=url, post=post_or_err))
            continue
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
    return results


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

    Each quote hop is its own ``fetch_x_batch`` call, so ``delay_first=True`` is
    passed there — otherwise every hop after the first looks like an ungated
    first fetch to that function and the randomized cooldown (Spec 2026-08-15
    §7's mitigation for request amplification) is silently skipped.
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
                quoted_photo_paths=chain.quoted_photo_paths.get(tid, []),
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
            delay_first=True,  # each hop is its own fetch_x_batch call — see docstring
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


def _format_human(post: XPost) -> str:
    lines = [
        f"@{post.author} ({post.author_name})  {post.post_ts_utc}",
        post.text,
        f"photos: {len(post.photo_urls)}  video: {post.video_present}  "
        f"thread: {post.is_thread}  quote: {post.is_quote}",
    ]
    if post.quoted_text:
        lines.append(f"  ↳ quoting @{post.quoted_author}: {post.quoted_text}")
    lines.extend(f"  {u}" for u in post.photo_urls)
    return "\n".join(lines)


def _result_to_dict(result: BatchResult) -> dict[str, object]:
    base: dict[str, object] = {
        "url": result.url,
        "cached": result.cached,
        "photo_paths": result.photo_paths,
        "quoted_photo_paths": result.quoted_photo_paths,
    }
    if isinstance(result.post, Unavailable):
        return {**base, "post": None, "unavailable": result.post.reason}
    return {**base, "post": asdict(result.post), "unavailable": None}


def main(
    argv: list[str] | None = None,
    *,
    get: HttpGet = _requests_get,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch one or more X posts via the public syndication endpoint (read-only)."
    )
    parser.add_argument("urls", nargs="+", help="one or more X/Twitter status URLs")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--batch", action="store_true", help="force batch mode for a single URL"
    )
    parser.add_argument("--force", action="store_true", help="ignore the dedup cache")
    parser.add_argument(
        "--thread",
        action="store_true",
        help="walk the self-thread upward from this post (pass the LAST post)",
    )
    parser.add_argument(
        "--min-delay", type=float, default=4.0, help="min cooldown seconds"
    )
    parser.add_argument(
        "--max-delay", type=float, default=12.0, help="max cooldown seconds"
    )
    parser.add_argument("--cache-dir", default=".cache/x-posts", help="dedup cache dir")
    parser.add_argument(
        "--media-root", default=".cache/x-media", help="downloaded-chart dir"
    )
    args = parser.parse_args(argv)

    if args.thread:
        rc = 0
        for url in args.urls:
            chain = walk_thread(
                url,
                cache_dir=Path(args.cache_dir),
                min_delay=args.min_delay,
                max_delay=args.max_delay,
                get=get,
                sleep=sleep,
            )
            if not chain.posts:
                print(f"UNAVAILABLE: {url}", file=sys.stderr)
                rc = 1
                continue
            if args.json:
                print(
                    json.dumps(
                        {
                            "posts": [asdict(p) for p in chain.posts],
                            "notes": chain.notes,
                        },
                        indent=2,
                    )
                )
            else:
                # The recovered count is the review surface: a bookmark that
                # silently expanded into N posts cannot be reviewed as one.
                print(f"thread: {len(chain.posts)} posts @{chain.posts[0].author}")
                for p in chain.posts:
                    print(f"\n--- [{p.thread_pos}] {p.url}\n{_format_human(p)}")
                for note in chain.notes:
                    print(f"\n! {note}", file=sys.stderr)
        return rc

    if len(args.urls) == 1 and not args.batch:
        result = fetch_x_post(args.urls[0], get=get)
        if isinstance(result, Unavailable):
            print(f"UNAVAILABLE: {result.reason}", file=sys.stderr)
            return 1
        print(
            json.dumps(asdict(result), indent=2) if args.json else _format_human(result)
        )
        return 0

    results = fetch_x_batch(
        args.urls,
        cache_dir=Path(args.cache_dir),
        media_root=Path(args.media_root),
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        force=args.force,
        get=get,
        sleep=sleep,
    )
    if args.json:
        print(json.dumps([_result_to_dict(r) for r in results], indent=2))
    else:
        for r in results:
            tag = " [cached]" if r.cached else ""
            if isinstance(r.post, Unavailable):
                print(f"UNAVAILABLE ({r.post.reason}): {r.url}")
            else:
                print(f"{_format_human(r.post)}{tag}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
