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
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

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
