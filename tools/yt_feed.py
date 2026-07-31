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

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
