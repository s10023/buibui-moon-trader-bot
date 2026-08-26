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
# The author-less form `resolve` builds for a quoted id, before any payload has
# told it whose post that is — see _canonical_url.
_I_URL_RE = re.compile(r"(?:twitter|x)\.com/i/status/\d+")
# Bumped whenever an XPost field is ADDED or an existing field's meaning changes
# — the defect this exists to prevent was caused by an addition, not a meaning
# change. Starts at 2, not 1: entries
# written before this constant existed carry no version key at all, and an
# absent key must read as stale exactly like a mismatched one (ruling R8) —
# _load_cached treats both as a cache MISS so the post is re-fetched instead
# of silently answered with new fields defaulted false/empty.
# 3 (ST102): the three account-id fields. Every entry written under 2 is now a
# miss and is re-fetched on next touch — free and keyless, but paid at the
# randomized cooldown, so a round re-reading old posts is slower once.
_CACHE_SCHEMA = 3


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


def _canonical_url(url: str, author: str, tweet_id: str) -> str:
    """`resolve` addresses a quoted post as ``x.com/i/status/<id>`` — it has no
    handle to use until the payload answers. Rewrite it once the handle is known,
    so the bundle, the cache entry, and every routed row carry the real author:
    this is a pipeline about attribution, and `/i/` is nobody."""
    if author and _I_URL_RE.search(url):
        return f"https://x.com/{author}/status/{tweet_id}"
    return url


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
    # Thread fields. The defaults let a caller build an XPost without stating
    # every field (e.g. thread_pos, which walk_thread sets via replace() after
    # construction) — they no longer protect a stale cache entry. That is
    # _CACHE_SCHEMA's job: a cache entry predating these fields fails the
    # version check and is re-fetched, rather than loading here with the
    # fields silently defaulted.
    in_reply_to_id: str = ""  # the post this one replies to, "" at the root
    in_reply_to_author: str = ""  # @handle replied to; == author on a self-thread
    conversation_count: int = 0  # replies to the CONVERSATION, never thread length
    thread_pos: int = 0  # 0 = root, ascending toward the leaf
    # Numeric account ids (ST102), which the payload carries beside every
    # screen_name and this parser dropped — so the follow-list roster had to be
    # rebuilt from a paid archive instead of maintaining itself off calls
    # already being made. HANDLES ARE MUTABLE, IDS ARE PERMANENT, which is what
    # makes the id the diff key: a rename read on handles alone is
    # indistinguishable from one unfollow plus one new follow, and
    # tools/pundit_score.py groups on the handle, so it SPLITS that author's
    # track record with nothing downstream able to notice. Empty means the
    # payload carried no id, never that the author has none. They sit here
    # rather than beside their handle siblings only because a defaulted field
    # cannot precede a required one.
    author_id: str = ""  # user.id_str
    quoted_author_id: str = ""  # quoted_tweet.user.id_str
    in_reply_to_author_id: str = ""  # in_reply_to_user_id_str


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
    author = user.get("screen_name", "")
    return XPost(
        source="twitter",
        author=author,
        author_name=user.get("name", ""),
        url=_canonical_url(url, author, tweet_id),
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
        author_id=str(user.get("id_str") or ""),
        quoted_author_id=str(quoted_user.get("id_str") or ""),
        in_reply_to_author_id=str(data.get("in_reply_to_user_id_str") or ""),
    )


def _download_urls(
    urls: tuple[str, ...], dest_dir: Path, *, get: HttpGet = _requests_get
) -> list[Path]:
    if not urls:
        return []  # never mkdir for a post that has no images of this kind
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


def _media_notes(
    tweet_id: str, post: XPost, photo_paths: list[str], quoted_photo_paths: list[str]
) -> list[str]:
    """Name every image that did NOT reach disk.

    A failed GET was dropped by `_download_urls`'s `continue`, the short list was
    then written to the cache as if complete, and the re-run served it back with
    `cached=True` — so the lost chart never retried and nothing ever said it was
    missing. That is this branch's own headline defect ("returned image URLs it
    never downloaded") one layer down, and the `notes` channel is what makes it
    cheap to say out loud. Reported on cache hits too: that is precisely the run
    on which no retry happens.
    """
    notes: list[str] = []
    for kind, urls, paths in (
        ("images", post.photo_urls, photo_paths),
        ("quoted-post images", post.quoted_photo_urls, quoted_photo_paths),
    ):
        missing = len(urls) - len(paths)
        if missing > 0:
            notes.append(
                f"{missing} of {len(urls)} {kind} failed to download for "
                f"{tweet_id} — NOT on disk; re-run with --force to retry"
            )
    return notes


@dataclass(frozen=True)
class BatchResult:
    url: str
    post: XPost | Unavailable
    photo_paths: list[str] = field(default_factory=list)
    quoted_photo_paths: list[str] = field(default_factory=list)
    cached: bool = False
    notes: list[str] = field(default_factory=list)  # media shortfalls, verbatim


@dataclass(frozen=True)
class ReusableMedia:
    """Images ALREADY on disk for a post that is about to be fetched — in
    practice the referrer's embedded copy of its quoted post's photos.

    Used only when ``urls`` matches the fetched post's own ``photo_urls``
    exactly AND every one of them reached disk. Without this, one chart cost two
    GETs and landed twice, and `fetch_x_batch`'s own docstring promise —
    "downloads charts once" — was false on exactly the path this branch added.
    An INCOMPLETE copy is deliberately not reused: reusing it would turn one
    failed GET on the referrer into a permanent loss on both posts, where a fresh
    download is a free retry.
    """

    urls: tuple[str, ...]
    paths: list[str]


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
    force: bool = False,
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

    Reads the per-id cache so an ancestor already ingested costs no network —
    unless ``force``, which is the only way to refresh a post whose text has since
    been edited, now that a cache entry is authoritative for every field. With
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
        cached = None if force else _load_cached(cache_dir, tweet_id)
        if cached is not None:
            post = cached[0]
            if download:
                photo_paths[tweet_id] = cached[1]
                quoted_photo_paths[tweet_id] = cached[2]
                notes.extend(_media_notes(tweet_id, post, cached[1], cached[2]))
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
                notes.extend(_media_notes(tweet_id, post, own, quoted_own))
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
        # Absent or stale version (ruling R8) ⇒ miss, re-fetch rather than load
        # with newer fields silently defaulted.
        if data.get("cache_schema") != _CACHE_SCHEMA:
            return None
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
        "cache_schema": _CACHE_SCHEMA,
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
    reuse: ReusableMedia | None = None,
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
    Downloads charts once (no double-fetch) — including across the quote hop,
    which is what ``reuse`` is for; it applies to the single post being fetched,
    so it is rejected for a multi-url call rather than silently mis-applied.
    Every image that did NOT reach disk is named in ``BatchResult.notes``."""
    if reuse is not None and len(urls) != 1:
        raise ValueError("reuse applies to one post: pass a single url with it")
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
                        notes=_media_notes(
                            tweet_id, post, photo_paths, quoted_photo_paths
                        ),
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
        if (
            reuse is not None
            and reuse.paths
            and len(reuse.paths) == len(reuse.urls)  # never reuse a partial copy
            and reuse.urls == post_or_err.photo_urls
        ):
            photo_paths = list(reuse.paths)  # already on disk — see ReusableMedia
        else:
            photo_paths = [
                str(p)
                for p in download_photos(post_or_err, media_root / tweet_id, get=get)
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
                notes=_media_notes(
                    tweet_id, post_or_err, photo_paths, quoted_photo_paths
                ),
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
    force: bool = False,
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

    ``force`` reaches BOTH the chain walk and every quote hop. A cache entry is
    authoritative for every field since schema versioning landed, and ``edited:
    true`` is exactly the case worth re-fetching, so a half-wired ``force``
    would leave a stale half that nothing reports.

    One bound is deliberately NOT walked: a quoted post that is itself a reply.
    Its own chain would be a second upward walk per hop, multiplying the request
    amplification §7 already flags, so it is noted instead — spec §5 carries
    that row as ⚠ for the same reason.
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
        force=force,
        get=get,
        sleep=sleep,
        rng=rng,
    )
    bundle = Bundle(notes=list(chain.notes))
    visited: set[str] = set()
    index_by_id: dict[str, int] = {}
    queue: list[tuple[str, str, int]] = []  # (quoted id, referrer id, depth)

    def record(tid: str, resolved: ResolvedPost) -> None:
        index_by_id[tid] = len(bundle.posts)
        bundle.posts.append(resolved)

    def queue_quote(post: XPost, tid: str, depth: int) -> None:
        """A quote that cannot be followed still has to be SAID.

        ``quoted_id`` comes back empty when the quoted post is tombstoned or
        withheld, and before this the entire quote vanished: nothing queued it,
        nothing noted it, and the bundle read as if the post had never quoted
        anything.
        """
        if post.quoted_id:
            queue.append((post.quoted_id, tid, depth))
        elif post.is_quote:
            bundle.notes.append(
                f"{tid} quotes a post with no id in the payload (deleted or "
                "withheld) — NOT resolved; its truncated text may still be on "
                "the referrer as quoted_text"
            )

    for post in chain.posts:
        tid = parse_tweet_id(post.url)
        visited.add(tid)
        record(
            tid,
            ResolvedPost(
                post=post,
                role="bookmarked" if tid == bookmarked_id else "chain_parent",
                depth=0,
                referred_by="",
                photo_paths=chain.photo_paths.get(tid, []),
                quoted_photo_paths=chain.quoted_photo_paths.get(tid, []),
            ),
        )
        queue_quote(post, tid, 1)

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
        referrer_rp = (
            bundle.posts[index_by_id[referrer]] if referrer in index_by_id else None
        )
        result = fetch_x_batch(
            [quoted_url],
            cache_dir=cache_dir,
            media_root=media_root,
            min_delay=min_delay,
            max_delay=max_delay,
            force=force,
            delay_first=True,  # each hop is its own fetch_x_batch call — see docstring
            reuse=(
                ReusableMedia(
                    urls=referrer_rp.post.quoted_photo_urls,
                    paths=referrer_rp.quoted_photo_paths,
                )
                if referrer_rp is not None and referrer_rp.quoted_photo_paths
                else None
            ),
            get=get,
            sleep=sleep,
            rng=rng,
        )[0]
        bundle.notes.extend(result.notes)
        if isinstance(result.post, Unavailable):
            bundle.notes.append(
                f"quoted {quoted_id} unavailable: {result.post.reason} — "
                "its text may still be on the referrer as quoted_text"
            )
            continue
        record(
            quoted_id,
            ResolvedPost(
                post=result.post,
                role="quoted",
                depth=depth,
                referred_by=referrer,
                photo_paths=result.photo_paths,
                quoted_photo_paths=result.quoted_photo_paths,
            ),
        )
        if (
            referrer_rp is not None
            and referrer_rp.quoted_photo_paths
            and referrer_rp.post.quoted_photo_urls == result.post.photo_urls
        ):
            # Same image URLs: the referrer's embedded copy IS this post's own
            # chart. Listing both puts one chart under two ResolvedPosts, so two
            # vision subagents read it and the digest can double-count it. It
            # stays with the post that owns it — which is also the attribution
            # that matters. Unequal URLs keep both: there the embedded copy is
            # evidence the quoted post's own payload does not carry.
            bundle.posts[index_by_id[referrer]] = replace(
                referrer_rp, quoted_photo_paths=[]
            )
        if result.post.in_reply_to_id:
            bundle.notes.append(
                f"quoted {quoted_id} is itself a reply to "
                f"{result.post.in_reply_to_id} — its own chain was NOT walked; "
                "only the bookmarked post's chain is"
            )
        queue_quote(result.post, quoted_id, depth + 1)

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
        "notes": result.notes,
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
    parser.add_argument(
        "--max-hops",
        type=int,
        default=25,
        help="max reply-chain hops to walk upward (default 25)",
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

    # Both multi-URL branches below accumulate ONE array under --json. Printing a
    # top-level object per URL emitted concatenated documents that json.loads
    # rejects outright ("Extra data: line 33 column 1") — on the exact invocation
    # the /ingest-x skill mandates — and an unavailable URL printed nothing at
    # all, so N inputs yielded N-1 outputs with no key tying a bundle to its
    # input. Every element carries its input `url` for that reason.
    if args.resolve:
        rc = 0
        emitted: list[dict[str, object]] = []
        for url in args.urls:
            bundle = resolve(
                url,
                max_quote_depth=args.max_quote_depth,
                max_hops=args.max_hops,
                cache_dir=Path(args.cache_dir),
                media_root=Path(args.media_root),
                min_delay=args.min_delay,
                max_delay=args.max_delay,
                force=args.force,
                get=get,
                sleep=sleep,
            )
            if args.json:
                emitted.append(
                    {
                        "url": url,
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
                    }
                )
            if not bundle.posts:
                # The notes hold WHY — "stopped at 200: HTTP 404" is what tells
                # protected from deleted from age-gated, which is the call the
                # skill asks the operator to make. They used to die here.
                print(f"UNAVAILABLE: {url}", file=sys.stderr)
                for note in bundle.notes:
                    print(f"  ! {note}", file=sys.stderr)
                rc = 1
                continue
            if not args.json:
                for rp in bundle.posts:
                    print(f"[{rp.role} d{rp.depth}] {_format_human(rp.post)}")
                for note in bundle.notes:
                    print(f"  ! {note}", file=sys.stderr)
        if args.json:
            print(json.dumps(emitted, indent=2))
        return rc

    if args.thread:
        rc = 0
        chains: list[dict[str, object]] = []
        for url in args.urls:
            chain = walk_thread(
                url,
                max_hops=args.max_hops,
                cache_dir=Path(args.cache_dir),
                min_delay=args.min_delay,
                max_delay=args.max_delay,
                force=args.force,
                get=get,
                sleep=sleep,
            )
            if args.json:
                chains.append(
                    {
                        "url": url,
                        "posts": [asdict(p) for p in chain.posts],
                        "notes": chain.notes,
                    }
                )
            if not chain.posts:
                print(f"UNAVAILABLE: {url}", file=sys.stderr)
                for note in chain.notes:
                    print(f"  ! {note}", file=sys.stderr)
                rc = 1
                continue
            if not args.json:
                # The recovered count is the review surface: a bookmark that
                # silently expanded into N posts cannot be reviewed as one.
                print(f"thread: {len(chain.posts)} posts @{chain.posts[0].author}")
                for p in chain.posts:
                    print(f"\n--- [{p.thread_pos}] {p.url}\n{_format_human(p)}")
                for note in chain.notes:
                    print(f"\n! {note}", file=sys.stderr)
        if args.json:
            print(json.dumps(chains, indent=2))
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
            for note in r.notes:
                print(f"  ! {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
