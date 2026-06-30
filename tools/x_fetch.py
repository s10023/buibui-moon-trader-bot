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
import re
import sys
from dataclasses import asdict, dataclass
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


def download_photos(
    post: XPost, dest_dir: Path, *, get: HttpGet = _requests_get
) -> list[Path]:
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
