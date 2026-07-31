"""YouTube channel auto-feed backing /ingest-feed (ST10).

Read-only discovery of new uploads across a configured channel list, plus the
explicit-outcome ledger that decides what is "new". Consumption is stamped ONLY
by `mark`, after the /ingest-video review gate routes a batch — `poll` and
`backfill` never write anything. (The historical defect this guards against:
wifey PR #68 watermark-on-send — stamping "seen" at fetch time let an aborted
run permanently consume items.) Mirrors tools/x_fetch.py: HTTP injectable for
tests, CLI for ad-hoc use.

Spec: docs/superpowers/specs/2026-07-31-st10-youtube-feed-design.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import requests

from tools.video_marks import FRAME_CAP

_API_BASE = "https://www.googleapis.com/youtube/v3"
_PAGE_SIZE = 50
_DEFAULT_COLD_START_DAYS = 14
_DEFAULT_MIN_DURATION_S = 180
_DEFAULT_BACKFILL_MAX = 200
EST_TOKENS_PER_S = 4
EST_TOKENS_PER_FRAME = 1400
EST_FIXED_OVERHEAD = 8000
_STATE_VERSION = 1
DEFAULT_CONFIG_PATH = Path("config/youtube_channels.toml")
DEFAULT_STATE_PATH = Path("docs/plans/yt-feed-state.json")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ISO_DUR_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)


@dataclass(frozen=True)
class ChannelConfig:
    id: str
    name: str
    title_include: tuple[str, ...]
    title_exclude: tuple[str, ...]
    min_duration_s: int
    lang: str


@dataclass(frozen=True)
class FeedConfig:
    cold_start_days: int
    channels: tuple[ChannelConfig, ...]


def load_feed_config(path: Path) -> FeedConfig:
    if not path.exists():
        raise SystemExit(
            f"channel config not found: {path} (copy {path}.example and fill it in)"
        )
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    feed = data.get("feed", {})
    channels: list[ChannelConfig] = []
    for raw in data.get("channel", []):
        cid = str(raw.get("id", ""))
        if not cid.startswith("UC"):
            raise SystemExit(
                f"channel id must start with UC (use `yt_feed.py resolve <handle>`): {cid!r}"
            )
        channels.append(
            ChannelConfig(
                id=cid,
                name=str(raw.get("name", cid)),
                title_include=tuple(
                    str(k).lower() for k in raw.get("title_include", [])
                ),
                title_exclude=tuple(
                    str(k).lower() for k in raw.get("title_exclude", [])
                ),
                min_duration_s=int(raw.get("min_duration_s", _DEFAULT_MIN_DURATION_S)),
                lang=str(raw.get("lang", "")),
            )
        )
    return FeedConfig(
        cold_start_days=int(feed.get("cold_start_days", _DEFAULT_COLD_START_DAYS)),
        channels=tuple(channels),
    )


def uploads_playlist_id(channel_id: str) -> str:
    """A channel's uploads playlist is its UC… id with a UU prefix."""
    return "UU" + channel_id[2:]


def parse_iso8601_duration(raw: str) -> int:
    match = _ISO_DUR_RE.match(raw)
    if match is None:
        raise ValueError(f"unparseable ISO-8601 duration: {raw!r}")
    parts = {k: int(v) for k, v in match.groupdict().items() if v is not None}
    return (
        parts.get("d", 0) * 86400
        + parts.get("h", 0) * 3600
        + parts.get("m", 0) * 60
        + parts.get("s", 0)
    )


def estimate_tokens(duration_s: int) -> int:
    """Ranking-grade (±30%) ingest-cost estimate — spec §9."""
    return (
        duration_s * EST_TOKENS_PER_S
        + FRAME_CAP * EST_TOKENS_PER_FRAME
        + EST_FIXED_OVERHEAD
    )


def title_excluded(title: str, channel: ChannelConfig) -> bool:
    """Case-insensitive substring filters; exclude wins over include."""
    low = title.lower()
    if any(k in low for k in channel.title_exclude):
        return True
    return bool(channel.title_include) and not any(
        k in low for k in channel.title_include
    )


def _fresh_state() -> dict[str, Any]:
    return {"version": _STATE_VERSION, "channels": {}, "videos": {}}


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _fresh_state()
    try:
        state: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"malformed state file {path}: {exc} — refusing to silently reset "
            "(that would re-queue everything ever ingested); fix or move the file"
        ) from exc
    if (
        not isinstance(state, dict)
        or state.get("version") != _STATE_VERSION
        or not isinstance(state.get("channels"), dict)
        or not isinstance(state.get("videos"), dict)
    ):
        raise SystemExit(
            f"unrecognized state shape/version in {path} — refusing to silently reset"
        )
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def floor_for(
    channel_id: str, state: dict[str, Any], now: datetime, cold_start_days: int
) -> datetime:
    """Static per-channel candidacy floor (spec §3): persisted value wins; else computed.

    Never advanced by any fetch — the entry is persisted only by `mark`.
    """
    entry = state["channels"].get(channel_id)
    if entry is not None:
        return datetime.fromisoformat(entry["floor_ts_utc"])
    return now - timedelta(days=cold_start_days)


class HttpResponse(Protocol):
    status_code: int
    text: str


class HttpGet(Protocol):
    def __call__(self, url: str, *, params: dict[str, str]) -> HttpResponse: ...


def _requests_get(url: str, *, params: dict[str, str]) -> HttpResponse:
    return requests.get(url, params=params, timeout=20)  # type: ignore[return-value]


class FeedApiError(Exception):
    """A YouTube Data API call failed; message carries HTTP status + API reason."""


_EXCLUDE_REASONS = (
    "below_floor",
    "ledgered",
    "title_filtered",
    "too_short",
    "live_or_upcoming",
    "unavailable",
)


@dataclass(frozen=True)
class Candidate:
    channel_id: str
    channel_name: str
    video_id: str
    url: str
    title: str
    publish_ts_utc: str
    duration_s: int
    age_h: float
    est_tokens: int
    lang_hint: str


@dataclass
class ChannelResult:
    channel_id: str
    channel_name: str
    floor_ts_utc: str
    candidates: list[Candidate] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _api_error_reason(body: str) -> str:
    try:
        payload = json.loads(body)
        reason = payload["error"]["errors"][0]["reason"]
        return str(reason)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return ""


def _api_get(
    get: HttpGet, api_key: str, endpoint: str, params: dict[str, str]
) -> dict[str, Any]:
    resp = get(f"{_API_BASE}/{endpoint}", params={**params, "key": api_key})
    if resp.status_code != 200:
        reason = _api_error_reason(resp.text)
        suffix = f" ({reason})" if reason else ""
        raise FeedApiError(f"{endpoint} HTTP {resp.status_code}{suffix}")
    result: dict[str, Any] = json.loads(resp.text)
    return result


def _fetch_playlist_page(
    get: HttpGet, api_key: str, playlist_id: str, page_token: str | None
) -> dict[str, Any]:
    params = {
        "part": "snippet,contentDetails",
        "playlistId": playlist_id,
        "maxResults": str(_PAGE_SIZE),
    }
    if page_token is not None:
        params["pageToken"] = page_token
    return _api_get(get, api_key, "playlistItems", params)


def _resolve_uploads_id(get: HttpGet, api_key: str, channel_id: str) -> str:
    data = _api_get(
        get, api_key, "channels", {"part": "contentDetails", "id": channel_id}
    )
    items = data.get("items", [])
    if not items:
        raise FeedApiError(f"channel not found: {channel_id}")
    uploads: str = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    return uploads


def _scan_items(
    channel: ChannelConfig,
    items: list[dict[str, Any]],
    *,
    ledger: dict[str, Any],
    floor: datetime | None,
    excluded: dict[str, int],
) -> list[dict[str, Any]]:
    """First-stage filter over playlistItems entries (pre-quota: no API calls here)."""
    survivors: list[dict[str, Any]] = []
    for item in items:
        details = item.get("contentDetails", {})
        snippet = item.get("snippet", {})
        vid = details.get("videoId") or snippet.get("resourceId", {}).get("videoId")
        pub_raw = details.get("videoPublishedAt")
        title = str(snippet.get("title", ""))
        if not vid or not pub_raw or title in ("Deleted video", "Private video"):
            excluded["unavailable"] += 1
            continue
        pub = datetime.fromisoformat(pub_raw)
        if floor is not None and pub < floor:
            excluded["below_floor"] += 1
            continue
        if vid in ledger:
            excluded["ledgered"] += 1
            continue
        if title_excluded(title, channel):
            excluded["title_filtered"] += 1
            continue
        survivors.append({"video_id": vid, "title": title, "publish": pub})
    return survivors


def _resolve_durations(
    get: HttpGet,
    api_key: str,
    survivors: list[dict[str, Any]],
    channel: ChannelConfig,
    now: datetime,
    excluded: dict[str, int],
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for start in range(0, len(survivors), _PAGE_SIZE):
        chunk = survivors[start : start + _PAGE_SIZE]
        data = _api_get(
            get,
            api_key,
            "videos",
            {
                "part": "contentDetails,snippet",
                "id": ",".join(s["video_id"] for s in chunk),
            },
        )
        by_id = {v["id"]: v for v in data.get("items", [])}
        for s in chunk:
            video = by_id.get(s["video_id"])
            if video is None:
                excluded["unavailable"] += 1
                continue
            if video.get("snippet", {}).get("liveBroadcastContent", "none") in (
                "live",
                "upcoming",
            ):
                excluded["live_or_upcoming"] += 1
                continue
            try:
                duration_s = parse_iso8601_duration(
                    video.get("contentDetails", {}).get("duration", "")
                )
            except ValueError:
                excluded["unavailable"] += 1
                continue
            if duration_s < channel.min_duration_s:
                excluded["too_short"] += 1
                continue
            publish: datetime = s["publish"]
            candidates.append(
                Candidate(
                    channel_id=channel.id,
                    channel_name=channel.name,
                    video_id=s["video_id"],
                    url=f"https://www.youtube.com/watch?v={s['video_id']}",
                    title=s["title"],
                    publish_ts_utc=publish.isoformat(),
                    duration_s=duration_s,
                    age_h=round((now - publish).total_seconds() / 3600, 1),
                    est_tokens=estimate_tokens(duration_s),
                    lang_hint=channel.lang,
                )
            )
    return candidates


def poll_channel(
    channel: ChannelConfig,
    state: dict[str, Any],
    *,
    now: datetime,
    get: HttpGet,
    api_key: str,
    cold_start_days: int,
) -> ChannelResult:
    """Daily-feed scan of one channel. Strictly read-only — writes nothing."""
    floor = floor_for(channel.id, state, now, cold_start_days)
    excluded = dict.fromkeys(_EXCLUDE_REASONS, 0)
    result = ChannelResult(
        channel.id, channel.name, floor.isoformat(), [], excluded, []
    )
    try:
        try:
            page = _fetch_playlist_page(
                get, api_key, uploads_playlist_id(channel.id), None
            )
        except FeedApiError as exc:
            if "404" not in str(exc):
                raise
            uploads = _resolve_uploads_id(get, api_key, channel.id)
            page = _fetch_playlist_page(get, api_key, uploads, None)
        survivors = _scan_items(
            channel,
            page.get("items", []),
            ledger=state["videos"],
            floor=floor,
            excluded=excluded,
        )
        result.candidates = _resolve_durations(
            get, api_key, survivors, channel, now, excluded
        )
    except FeedApiError as exc:
        result.errors.append(str(exc))
    return result


def backfill_channel(
    channel: ChannelConfig,
    state: dict[str, Any],
    *,
    now: datetime,
    get: HttpGet,
    api_key: str,
    since: datetime | None,
    max_videos: int,
) -> ChannelResult:
    """Deep back-catalogue scan (spec §5): floor ignored, ledger respected, read-only.

    `since` reuses the "below_floor" exclusion bucket (= "older than --since" here);
    `max_videos` bounds playlist entries examined, keeping quota predictable.
    """
    excluded = dict.fromkeys(_EXCLUDE_REASONS, 0)
    result = ChannelResult(channel.id, channel.name, "", [], excluded, [])
    collected: list[dict[str, Any]] = []
    token: str | None = None
    try:
        uploads = uploads_playlist_id(channel.id)
        while True:
            try:
                page = _fetch_playlist_page(get, api_key, uploads, token)
            except FeedApiError as exc:
                if token is None and "404" in str(exc):
                    uploads = _resolve_uploads_id(get, api_key, channel.id)
                    page = _fetch_playlist_page(get, api_key, uploads, token)
                else:
                    raise
            items = page.get("items", [])
            collected.extend(items[: max_videos - len(collected)])
            token = page.get("nextPageToken")
            last_pub_raw = (
                items[-1].get("contentDetails", {}).get("videoPublishedAt")
                if items
                else None
            )
            past_since = (
                since is not None
                and last_pub_raw is not None
                and datetime.fromisoformat(last_pub_raw) < since
            )
            if token is None or len(collected) >= max_videos or past_since:
                break
        survivors = _scan_items(
            channel, collected, ledger=state["videos"], floor=since, excluded=excluded
        )
        result.candidates = _resolve_durations(
            get, api_key, survivors, channel, now, excluded
        )
    except FeedApiError as exc:
        result.errors.append(str(exc))
    return result


def run_mark(
    state_path: Path,
    *,
    ingested: list[str],
    skipped: list[str],
    channel_seen: list[str],
    candidates_json: Path | None,
    now: datetime,
) -> int:
    """The ONLY state writer. Every entry is an explicit outcome (spec §3)."""
    overlap = set(ingested) & set(skipped)
    if overlap:
        raise SystemExit(
            f"video id(s) in both --ingested and --skipped: {sorted(overlap)}"
        )
    state = load_state(state_path)
    meta: dict[str, dict[str, str]] = {}
    if candidates_json is not None:
        payload = json.loads(candidates_json.read_text(encoding="utf-8"))
        for cand in payload.get("candidates", []):
            meta[cand["video_id"]] = {
                "channel_id": cand.get("channel_id", ""),
                "title": cand.get("title", ""),
            }
    count = 0
    for status, ids in (("ingested", ingested), ("skipped", skipped)):
        for vid in ids:
            if not _VIDEO_ID_RE.match(vid):
                raise SystemExit(f"not a YouTube video id: {vid!r}")
            enrich = meta.get(vid, {})
            state["videos"][vid] = {
                "status": status,
                "channel_id": enrich.get("channel_id") or None,
                "title": enrich.get("title") or None,
                "decided_ts_utc": now.isoformat(),
            }
            count += 1
    for pair in channel_seen:
        cid, sep, floor_raw = pair.partition("=")
        if sep != "=" or not cid.startswith("UC") or not floor_raw:
            raise SystemExit(f"bad --channel-seen (want UC…=<iso ts>): {pair!r}")
        try:
            datetime.fromisoformat(floor_raw)
        except ValueError as exc:
            raise SystemExit(f"bad --channel-seen timestamp: {floor_raw!r}") from exc
        # setdefault is load-bearing: an existing floor is STATIC and never moves
        state["channels"].setdefault(
            cid, {"added_ts_utc": now.isoformat(), "floor_ts_utc": floor_raw}
        )
    save_state(state_path, state)
    return count


def resolve_handle(get: HttpGet, api_key: str, handle: str) -> str:
    """Handle → ready-to-paste [[channel]] TOML block. Never writes config."""
    normalized = handle if handle.startswith("@") else f"@{handle}"
    data = _api_get(
        get, api_key, "channels", {"part": "id,snippet", "forHandle": normalized}
    )
    items = data.get("items", [])
    if not items:
        raise SystemExit(f"no channel found for handle {normalized!r}")
    cid = items[0]["id"]
    name = items[0]["snippet"]["title"]
    return (
        "[[channel]]\n"
        f'id = "{cid}"\n'
        f'name = "{name}"\n'
        "title_include = []\n"
        'title_exclude = ["#shorts"]\n'
        f"min_duration_s = {_DEFAULT_MIN_DURATION_S}\n"
        'lang = ""\n'
    )


def _results_to_dict(results: list[ChannelResult], now: datetime) -> dict[str, Any]:
    return {
        "generated_utc": now.isoformat(),
        "candidates": [asdict(c) for r in results for c in r.candidates],
        "channels": [
            {
                "channel_id": r.channel_id,
                "channel_name": r.channel_name,
                "floor_ts_utc": r.floor_ts_utc,
                "excluded": r.excluded,
                "errors": r.errors,
            }
            for r in results
        ],
    }


def _format_human(results: list[ChannelResult]) -> str:
    lines: list[str] = []
    for r in results:
        drops = ", ".join(f"{k}={v}" for k, v in r.excluded.items() if v)
        lines.append(
            f"# {r.channel_name} ({r.channel_id})"
            + (f" — excluded: {drops}" if drops else "")
        )
        for e in r.errors:
            lines.append(f"  ERROR: {e}")
        for c in r.candidates:
            mins = c.duration_s // 60
            lines.append(
                f"  {c.video_id}  {mins:>4}m  {c.age_h:>7.1f}h  ~{c.est_tokens // 1000}k tok  {c.title}"
            )
    total = sum(len(r.candidates) for r in results)
    lines.append(f"# {total} candidate(s)")
    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    *,
    get: HttpGet = _requests_get,
    now: datetime | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="YouTube channel auto-feed for /ingest-feed (read-only except `mark`)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_poll = sub.add_parser(
        "poll", help="list new uploads across the configured channels"
    )
    p_back = sub.add_parser(
        "backfill", help="page a channel's deep back-catalogue (floor ignored)"
    )
    p_back.add_argument("channel_id", help="UC… id; must exist in the channel config")
    p_back.add_argument(
        "--since", default=None, help="ISO date/ts; stop at older uploads"
    )
    p_back.add_argument(
        "--max-videos",
        type=int,
        default=_DEFAULT_BACKFILL_MAX,
        help="max playlist entries examined (quota bound)",
    )
    for p in (p_poll, p_back):
        p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
        p.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
        p.add_argument("--json", action="store_true", dest="as_json")

    p_mark = sub.add_parser(
        "mark", help="record explicit outcomes (the ONLY state writer)"
    )
    p_mark.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    p_mark.add_argument("--ingested", nargs="*", default=[])
    p_mark.add_argument("--skipped", nargs="*", default=[])
    p_mark.add_argument(
        "--channel-seen",
        action="append",
        default=[],
        help="UC…=<floor iso ts> — persists a channel entry (setdefault only)",
    )
    p_mark.add_argument(
        "--candidates-json",
        type=Path,
        default=None,
        help="poll/backfill --json output; enriches ledger rows",
    )

    p_res = sub.add_parser(
        "resolve", help="handle → ready-to-paste [[channel]] TOML block"
    )
    p_res.add_argument("handle")

    args = parser.parse_args(argv)
    now_dt = now if now is not None else datetime.now(UTC)

    if args.cmd == "mark":
        count = run_mark(
            args.state,
            ingested=args.ingested,
            skipped=args.skipped,
            channel_seen=args.channel_seen,
            candidates_json=args.candidates_json,
            now=now_dt,
        )
        print(f"marked {count} video(s) in {args.state}")
        return 0

    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        print(
            "YOUTUBE_API_KEY is not set — add it to .env (see .env.example)",
            file=sys.stderr,
        )
        return 2

    if args.cmd == "resolve":
        print(resolve_handle(get, api_key, args.handle), end="")
        return 0

    cfg = load_feed_config(args.config)
    state = load_state(args.state)
    if args.cmd == "poll":
        results = [
            poll_channel(
                ch,
                state,
                now=now_dt,
                get=get,
                api_key=api_key,
                cold_start_days=cfg.cold_start_days,
            )
            for ch in cfg.channels
        ]
    else:  # backfill
        by_id = {ch.id: ch for ch in cfg.channels}
        channel = by_id.get(args.channel_id)
        if channel is None:
            raise SystemExit(
                f"channel {args.channel_id} not in config {args.config} — add it first "
                "(filters live in config)"
            )
        since = datetime.fromisoformat(args.since) if args.since else None
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        results = [
            backfill_channel(
                channel,
                state,
                now=now_dt,
                get=get,
                api_key=api_key,
                since=since,
                max_videos=args.max_videos,
            )
        ]

    print(
        json.dumps(_results_to_dict(results, now_dt), indent=2)
        if args.as_json
        else _format_human(results)
    )
    return 1 if any(r.errors for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
