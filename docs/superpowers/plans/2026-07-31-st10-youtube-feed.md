# ST10 YouTube Channel Auto-Feed (`/ingest-feed`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `tools/yt_feed.py` (poll/backfill/mark/resolve) + the `/ingest-feed` skill so new uploads from a configured YouTube channel list flow into the existing `/ingest-video` batch path, with consumption stamped only after the review gate routes a batch.

**Architecture:** One single-file tool (repo convention, mirrors `tools/x_fetch.py`: injected HTTP transport, pure logic, argparse CLI) + a JSON explicit-outcome ledger under `docs/plans/` + a thin orchestration skill. `poll`/`backfill` are strictly read-only; `mark` is the only state writer. The `/ingest-video` skill gains the retrospective-call drop rule.

**Tech Stack:** Python 3.11 (`tomllib`, `dataclasses`), `requests`, pytest with injected fakes (zero network), YouTube Data API v3 (key-only).

**Spec:** `docs/superpowers/specs/2026-07-31-st10-youtube-feed-design.md` — read it before starting any task; it is the authority on behavior.

## Global Constraints

- mypy strict: every function annotated, including test methods (`-> None`).
- No real network in tests: HTTP goes through an injected `HttpGet`; tests assert **request shape** (endpoint path + params), per the faked-transport blind spot named in spec §10.
- Constants, verbatim from the spec: `_PAGE_SIZE = 50`, `_DEFAULT_COLD_START_DAYS = 14`, `_DEFAULT_MIN_DURATION_S = 180`, `_DEFAULT_BACKFILL_MAX = 200`, `EST_TOKENS_PER_S = 4`, `EST_TOKENS_PER_FRAME = 1400`, `EST_FIXED_OVERHEAD = 8000`, `_STATE_VERSION = 1`.
- Default paths: config `config/youtube_channels.toml`, state `docs/plans/yt-feed-state.json`.
- `search.list` is never called (100 units); only `playlistItems.list`, `videos.list`, `channels.list`.
- `poll` and `backfill` must never write anything to disk. Only `mark` writes state.
- Conventional commits (`feat:`/`test:`/`docs:`); after any Python change the Definition of Done gates apply: `make lint-py`, `make typecheck`, `make test`; `make test-regression` goldens must be untouched (nothing here goes near the backtest pipeline).
- Markdown files outside `.claude/` must pass `make lint-md` (every fence needs a language; table delimiters spaced `| --- |`).
- Work on branch `feat/ingest-feed`, created from `docs/st10-youtube-feed-spec` (spec + plan + implementation ship as one PR).

---

### Task 1: Pure helpers + config loading (`tools/yt_feed.py` skeleton)

**Named defect to avoid:** none historical here, but keep every helper pure — no `datetime.now()` inside logic functions; `now` is always a parameter.

**Files:**

- Create: `tools/yt_feed.py`
- Create: `tests/test_yt_feed.py`

**Interfaces:**

- Produces (later tasks rely on these exact names):
  - `ChannelConfig` (frozen dataclass: `id: str`, `name: str`, `title_include: tuple[str, ...]`, `title_exclude: tuple[str, ...]`, `min_duration_s: int`, `lang: str`)
  - `FeedConfig` (frozen dataclass: `cold_start_days: int`, `channels: tuple[ChannelConfig, ...]`)
  - `load_feed_config(path: Path) -> FeedConfig`
  - `uploads_playlist_id(channel_id: str) -> str`
  - `parse_iso8601_duration(raw: str) -> int` (raises `ValueError` on garbage)
  - `estimate_tokens(duration_s: int) -> int`
  - `title_excluded(title: str, channel: ChannelConfig) -> bool`
  - Constants listed in Global Constraints, plus `DEFAULT_CONFIG_PATH`, `DEFAULT_STATE_PATH`, `_VIDEO_ID_RE`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for tools/yt_feed.py — strict TDD, no real network calls."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tools.yt_feed import (
    ChannelConfig,
    FeedConfig,
    estimate_tokens,
    load_feed_config,
    parse_iso8601_duration,
    title_excluded,
    uploads_playlist_id,
)


def make_channel(**overrides: Any) -> ChannelConfig:
    base: dict[str, Any] = {
        "id": "UCabcdefghijklmnopqrstu",
        "name": "Test Channel",
        "title_include": (),
        "title_exclude": (),
        "min_duration_s": 180,
        "lang": "en",
    }
    base.update(overrides)
    return ChannelConfig(**base)


class TestPureHelpers:
    def test_uploads_playlist_id_swaps_prefix(self) -> None:
        assert uploads_playlist_id("UCabc123") == "UUabc123"

    def test_parse_iso8601_duration(self) -> None:
        assert parse_iso8601_duration("PT21M") == 1260
        assert parse_iso8601_duration("PT1H2M3S") == 3723
        assert parse_iso8601_duration("P1DT2H") == 93600
        assert parse_iso8601_duration("PT45S") == 45
        assert parse_iso8601_duration("P0D") == 0  # live stream placeholder

    def test_parse_iso8601_duration_garbage_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_iso8601_duration("banana")

    def test_estimate_tokens_matches_spec_example(self) -> None:
        # spec §9: 1260 s → 1260×4 + 15×1400 + 8000 = 34040
        assert estimate_tokens(1260) == 34040

    def test_title_exclude_wins_over_include(self) -> None:
        ch = make_channel(title_include=("bitcoin",), title_exclude=("#shorts",))
        assert title_excluded("Bitcoin update #SHORTS", ch) is True

    def test_title_include_empty_keeps_all(self) -> None:
        assert title_excluded("anything", make_channel()) is False

    def test_title_include_nonmatch_excludes(self) -> None:
        ch = make_channel(title_include=("bitcoin",))
        assert title_excluded("Ethereum update", ch) is True
        assert title_excluded("BITCOIN weekly", ch) is False


class TestLoadFeedConfig:
    def _write(self, tmp_path: Path, body: str) -> Path:
        p = tmp_path / "youtube_channels.toml"
        p.write_text(body, encoding="utf-8")
        return p

    def test_full_config(self, tmp_path: Path) -> None:
        cfg = load_feed_config(
            self._write(
                tmp_path,
                "[feed]\ncold_start_days = 7\n\n[[channel]]\n"
                'id = "UCabcdefghijklmnopqrstu"\nname = "Cowen"\n'
                'title_include = ["btc"]\ntitle_exclude = ["#shorts"]\n'
                'min_duration_s = 240\nlang = "en"\n',
            )
        )
        assert cfg.cold_start_days == 7
        ch = cfg.channels[0]
        assert ch.name == "Cowen"
        assert ch.title_include == ("btc",)
        assert ch.min_duration_s == 240

    def test_defaults_applied(self, tmp_path: Path) -> None:
        cfg = load_feed_config(
            self._write(tmp_path, '[[channel]]\nid = "UCabcdefghijklmnopqrstu"\n')
        )
        assert cfg.cold_start_days == 14
        ch = cfg.channels[0]
        assert ch.min_duration_s == 180
        assert ch.title_include == ()
        assert ch.lang == ""
        assert ch.name == "UCabcdefghijklmnopqrstu"

    def test_missing_file_aborts(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            load_feed_config(tmp_path / "nope.toml")

    def test_non_uc_id_aborts(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            load_feed_config(self._write(tmp_path, '[[channel]]\nid = "abc"\n'))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_yt_feed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.yt_feed'`

- [ ] **Step 3: Write the implementation**

```python
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
```

(`field`, `asdict`, `json`, `os`, `sys`, `argparse`, `requests`, `UTC`, `datetime`, `timedelta`, `Protocol`, `Any` are used by later tasks — keep the imports now so the file only grows.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_yt_feed.py -v`
Expected: all PASS. If ruff flags an unused import at this stage, add `# noqa: F401` is **wrong** — instead run `make lint-py` and remove only what it insists on, re-adding in the task that uses it.

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/test_yt_feed.py -q
git add tools/yt_feed.py tests/test_yt_feed.py
git commit -m "feat(yt-feed): config loading + pure helpers for the ST10 channel feed"
```

---

### Task 2: State ledger — load/save/floor

**Named defect to avoid:** silent state reset. A malformed state file must abort loudly — a quiet re-create would re-queue every video ever ingested and erase every skip decision.

**Files:**

- Modify: `tools/yt_feed.py` (append)
- Modify: `tests/test_yt_feed.py` (append)

**Interfaces:**

- Produces:
  - `load_state(path: Path) -> dict[str, Any]` — missing file → fresh `{"version": 1, "channels": {}, "videos": {}}`; malformed/wrong-version → `SystemExit`
  - `save_state(path: Path, state: dict[str, Any]) -> None` — atomic (`tmp` + `os.replace`)
  - `floor_for(channel_id: str, state: dict[str, Any], now: datetime, cold_start_days: int) -> datetime`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_yt_feed.py`; extend the module imports with `floor_for, load_state, save_state`)

```python
class TestState:
    def test_missing_file_returns_fresh(self, tmp_path: Path) -> None:
        state = load_state(tmp_path / "yt-feed-state.json")
        assert state == {"version": 1, "channels": {}, "videos": {}}

    def test_malformed_json_aborts(self, tmp_path: Path) -> None:
        p = tmp_path / "s.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(SystemExit, match="refusing"):
            load_state(p)

    def test_wrong_version_aborts(self, tmp_path: Path) -> None:
        p = tmp_path / "s.json"
        p.write_text(
            json.dumps({"version": 99, "channels": {}, "videos": {}}), encoding="utf-8"
        )
        with pytest.raises(SystemExit):
            load_state(p)

    def test_save_roundtrip_atomic(self, tmp_path: Path) -> None:
        p = tmp_path / "sub" / "s.json"
        state = load_state(p)
        state["videos"]["aaaaaaaaaaa"] = {"status": "ingested"}
        save_state(p, state)
        assert load_state(p)["videos"]["aaaaaaaaaaa"]["status"] == "ingested"
        assert not p.with_suffix(".json.tmp").exists()

    def test_floor_prefers_persisted_entry(self, tmp_path: Path) -> None:
        now = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
        state = {
            "version": 1,
            "videos": {},
            "channels": {
                "UCx": {
                    "added_ts_utc": "2026-07-01T00:00:00+00:00",
                    "floor_ts_utc": "2026-06-17T00:00:00+00:00",
                }
            },
        }
        assert floor_for("UCx", state, now, 14) == datetime(2026, 6, 17, tzinfo=UTC)

    def test_floor_computed_for_unknown_channel(self) -> None:
        now = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
        state = {"version": 1, "videos": {}, "channels": {}}
        assert floor_for("UCy", state, now, 14) == now - timedelta(days=14)
```

Also add `from datetime import UTC, datetime, timedelta` to the test imports (replacing the narrower line from Task 1).

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_yt_feed.py -k State -v`
Expected: FAIL with ImportError on `load_state`

- [ ] **Step 3: Write the implementation** (append to `tools/yt_feed.py`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_yt_feed.py -q`
Expected: all PASS

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/test_yt_feed.py -q
git add tools/yt_feed.py tests/test_yt_feed.py
git commit -m "feat(yt-feed): explicit-outcome state ledger with loud-abort on malformed state"
```

---

### Task 3: API transport + `poll_channel`

**Named defect to avoid:** the faked-subprocess/transport blind spot (cost 2 Criticals on PR #513) — a network-free suite cannot catch a wrong real-world parameter, so every test here asserts the **request shape** (endpoint path, `part=`, `playlistId=`, `maxResults=`, `key=`) against messy fixtures, and the manual e2e in Task 10 is load-bearing, not optional.

**Files:**

- Modify: `tools/yt_feed.py` (append)
- Modify: `tests/test_yt_feed.py` (append)

**Interfaces:**

- Produces:
  - `HttpResponse` / `HttpGet` Protocols (`get(url: str, *, params: dict[str, str]) -> HttpResponse`)
  - `FeedApiError(Exception)`
  - `Candidate` (frozen dataclass: `channel_id, channel_name, video_id, url, title, publish_ts_utc: str, duration_s: int, age_h: float, est_tokens: int, lang_hint: str`)
  - `ChannelResult` (dataclass: `channel_id: str, channel_name: str, floor_ts_utc: str, candidates: list[Candidate], excluded: dict[str, int], errors: list[str]`)
  - `poll_channel(channel: ChannelConfig, state: dict[str, Any], *, now: datetime, get: HttpGet, api_key: str, cold_start_days: int) -> ChannelResult`
  - internals reused by Task 5: `_api_get`, `_fetch_playlist_page`, `_resolve_uploads_id`, `_scan_items`, `_resolve_durations`, `_EXCLUDE_REASONS`

- [ ] **Step 1: Write the failing tests** (append; extend imports with `Candidate, ChannelResult, FeedApiError, poll_channel, _resolve_durations`)

```python
class FakeResp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self.text = json.dumps(payload)


class FakeGet:
    """Routes by endpoint suffix; records every (url, params) for shape assertions."""

    def __init__(self, routes: dict[str, list[FakeResp]]) -> None:
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, *, params: dict[str, str]) -> FakeResp:
        self.calls.append((url, dict(params)))
        endpoint = url.rsplit("/", 1)[-1]
        queue = self.routes.get(endpoint)
        if not queue:
            raise AssertionError(f"unexpected call to {endpoint}")
        return queue.pop(0)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
FRESH_STATE: dict[str, Any] = {"version": 1, "channels": {}, "videos": {}}


def playlist_item(vid: str, title: str, pub: str) -> dict[str, Any]:
    return {
        "snippet": {"title": title, "resourceId": {"videoId": vid}},
        "contentDetails": {"videoId": vid, "videoPublishedAt": pub},
    }


def video_item(vid: str, duration: str, live: str = "none") -> dict[str, Any]:
    return {
        "id": vid,
        "snippet": {"liveBroadcastContent": live},
        "contentDetails": {"duration": duration},
    }


class TestPollChannel:
    def _channel(self) -> ChannelConfig:
        return make_channel(id="UCabcdefghijklmnopqrstu", title_exclude=("#shorts",))

    def test_request_shape(self) -> None:
        get = FakeGet(
            {
                "playlistItems": [FakeResp(200, {"items": []})],
            }
        )
        poll_channel(
            self._channel(),
            dict(FRESH_STATE),
            now=NOW,
            get=get,
            api_key="K",
            cold_start_days=14,
        )
        url, params = get.calls[0]
        assert url == "https://www.googleapis.com/youtube/v3/playlistItems"
        assert params["part"] == "snippet,contentDetails"
        assert params["playlistId"] == "UUabcdefghijklmnopqrstu"
        assert params["maxResults"] == "50"
        assert params["key"] == "K"

    def test_messy_page_exclusions_and_one_candidate(self) -> None:
        state: dict[str, Any] = {
            "version": 1,
            "channels": {},
            "videos": {"bbbbbbbbbbb": {"status": "ingested"}},
        }
        page = {
            "items": [
                playlist_item("aaaaaaaaaaa", "Deleted video", "2026-07-31T01:00:00Z"),
                playlist_item("bbbbbbbbbbb", "already done", "2026-07-30T01:00:00Z"),
                playlist_item("ccccccccccc", "old news", "2026-06-01T01:00:00Z"),
                playlist_item("ddddddddddd", "clip #shorts", "2026-07-30T02:00:00Z"),
                playlist_item("eeeeeeeeeee", "a short one", "2026-07-30T03:00:00Z"),
                playlist_item("fffffffffff", "premiere soon", "2026-07-30T04:00:00Z"),
                playlist_item(
                    "ggggggggggg", "BTC weekly outlook", "2026-07-31T02:00:00Z"
                ),
            ]
        }
        videos = {
            "items": [
                video_item("eeeeeeeeeee", "PT45S"),
                video_item("fffffffffff", "P0D", live="upcoming"),
                video_item("ggggggggggg", "PT21M"),
            ]
        }
        get = FakeGet(
            {"playlistItems": [FakeResp(200, page)], "videos": [FakeResp(200, videos)]}
        )
        result = poll_channel(
            self._channel(), state, now=NOW, get=get, api_key="K", cold_start_days=14
        )
        assert result.excluded == {
            "below_floor": 1,
            "ledgered": 1,
            "title_filtered": 1,
            "too_short": 1,
            "live_or_upcoming": 1,
            "unavailable": 1,
        }
        assert [c.video_id for c in result.candidates] == ["ggggggggggg"]
        cand = result.candidates[0]
        assert cand.duration_s == 1260
        assert cand.est_tokens == 34040
        assert cand.url == "https://www.youtube.com/watch?v=ggggggggggg"
        assert cand.age_h == 10.0
        _, vparams = get.calls[1]
        assert vparams["part"] == "contentDetails,snippet"
        assert "eeeeeeeeeee" in vparams["id"]

    def test_uu_derivation_404_falls_back_to_channels_list(self) -> None:
        err = {"error": {"code": 404, "errors": [{"reason": "playlistNotFound"}]}}
        real_uploads = {
            "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUother"}}}]
        }
        get = FakeGet(
            {
                "playlistItems": [FakeResp(404, err), FakeResp(200, {"items": []})],
                "channels": [FakeResp(200, real_uploads)],
            }
        )
        result = poll_channel(
            self._channel(),
            dict(FRESH_STATE),
            now=NOW,
            get=get,
            api_key="K",
            cold_start_days=14,
        )
        assert result.errors == []
        assert get.calls[2][1]["playlistId"] == "UUother"

    def test_quota_403_reported_not_raised(self) -> None:
        err = {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}
        get = FakeGet({"playlistItems": [FakeResp(403, err)]})
        result = poll_channel(
            self._channel(),
            dict(FRESH_STATE),
            now=NOW,
            get=get,
            api_key="K",
            cold_start_days=14,
        )
        assert result.candidates == []
        assert any("quotaExceeded" in e for e in result.errors)

    def test_duration_batching_chunks_at_50(self) -> None:
        survivors = [
            {
                "video_id": f"v{i:010d}",
                "title": f"t{i}",
                "publish": NOW - timedelta(hours=2),
            }
            for i in range(60)
        ]
        videos_pages = [
            FakeResp(
                200,
                {"items": [video_item(s["video_id"], "PT10M") for s in survivors[:50]]},
            ),
            FakeResp(
                200,
                {"items": [video_item(s["video_id"], "PT10M") for s in survivors[50:]]},
            ),
        ]
        get = FakeGet({"videos": videos_pages})
        excluded = dict.fromkeys(
            (
                "below_floor",
                "ledgered",
                "title_filtered",
                "too_short",
                "live_or_upcoming",
                "unavailable",
            ),
            0,
        )
        cands = _resolve_durations(
            get, "K", survivors, make_channel(min_duration_s=60), NOW, excluded
        )
        assert len(cands) == 60
        assert len(get.calls) == 2
        assert len(get.calls[0][1]["id"].split(",")) == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_yt_feed.py -k "PollChannel" -v`
Expected: FAIL with ImportError on `poll_channel`

- [ ] **Step 3: Write the implementation** (append to `tools/yt_feed.py`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_yt_feed.py -q`
Expected: all PASS

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/test_yt_feed.py -q
git add tools/yt_feed.py tests/test_yt_feed.py
git commit -m "feat(yt-feed): read-only poll_channel with request-shape-tested API transport"
```

---

### Task 4: `backfill_channel` — deep back-catalogue pager

**Named defect to avoid:** wifey-#68 again — `backfill` is read-only exactly like `poll`; it must not touch state even though it walks thousands of entries.

**Files:**

- Modify: `tools/yt_feed.py` (append)
- Modify: `tests/test_yt_feed.py` (append)

**Interfaces:**

- Consumes: `_fetch_playlist_page`, `_resolve_uploads_id`, `_scan_items`, `_resolve_durations`, `ChannelResult`, `FeedApiError` (Task 3, exact signatures above)
- Produces: `backfill_channel(channel: ChannelConfig, state: dict[str, Any], *, now: datetime, get: HttpGet, api_key: str, since: datetime | None, max_videos: int) -> ChannelResult`

Behavioral notes (spec §5): ignores the floor (its purpose); `--since` reuses the `below_floor` exclusion bucket (documented as "older than --since" in backfill context); `max_videos` bounds the number of **playlist entries examined** (predictable quota), default 200; paging stops early when a page's last item is older than `since` (uploads playlists are newest-first); `floor_ts_utc` on the result is `""` (there is no floor to persist).

- [ ] **Step 1: Write the failing tests** (append; extend imports with `backfill_channel`)

```python
class TestBackfillChannel:
    def _pages(self) -> dict[str, list[FakeResp]]:
        page1 = {
            "items": [playlist_item("aaaaaaaaaa1", "recent", "2026-07-30T00:00:00Z")],
            "nextPageToken": "P2",
        }
        page2 = {
            "items": [playlist_item("aaaaaaaaaa2", "ancient", "2025-01-15T00:00:00Z")],
        }
        videos = {
            "items": [
                video_item("aaaaaaaaaa1", "PT30M"),
                video_item("aaaaaaaaaa2", "PT30M"),
            ]
        }
        return {
            "playlistItems": [FakeResp(200, page1), FakeResp(200, page2)],
            "videos": [FakeResp(200, videos)],
        }

    def test_pagination_ignores_floor_and_respects_ledger(self) -> None:
        state: dict[str, Any] = {
            "version": 1,
            "channels": {
                "UCabcdefghijklmnopqrstu": {
                    "added_ts_utc": "2026-07-31T00:00:00+00:00",
                    "floor_ts_utc": "2026-07-17T00:00:00+00:00",
                }
            },
            "videos": {"aaaaaaaaaa1": {"status": "ingested"}},
        }
        get = FakeGet(self._pages())
        result = backfill_channel(
            make_channel(),
            state,
            now=NOW,
            get=get,
            api_key="K",
            since=None,
            max_videos=200,
        )
        # the 2025 video is WAY below the poll floor but IS a backfill candidate
        assert [c.video_id for c in result.candidates] == ["aaaaaaaaaa2"]
        assert result.excluded["ledgered"] == 1
        assert result.floor_ts_utc == ""
        # second page requested with the pageToken
        assert get.calls[1][1]["pageToken"] == "P2"

    def test_since_stops_paging_and_excludes_older(self) -> None:
        get = FakeGet(self._pages())
        since = datetime(2026, 1, 1, tzinfo=UTC)
        result = backfill_channel(
            make_channel(),
            {"version": 1, "channels": {}, "videos": {}},
            now=NOW,
            get=get,
            api_key="K",
            since=since,
            max_videos=200,
        )
        assert [c.video_id for c in result.candidates] == ["aaaaaaaaaa1"]
        assert result.excluded["below_floor"] == 1  # "older than --since" bucket
        # page1's last item (2026-07-30) is newer than since, so page2 WAS fetched;
        # its item then landed below since. Now verify early-stop: with since after
        # page1's last item, page2 must never be fetched.
        get2 = FakeGet(self._pages())
        result2 = backfill_channel(
            make_channel(),
            {"version": 1, "channels": {}, "videos": {}},
            now=NOW,
            get=get2,
            api_key="K",
            since=datetime(2026, 7, 31, tzinfo=UTC),
            max_videos=200,
        )
        assert result2.candidates == []
        playlist_calls = [c for c in get2.calls if c[0].endswith("playlistItems")]
        assert len(playlist_calls) == 1

    def test_max_videos_bounds_examined_entries(self) -> None:
        get = FakeGet(self._pages())
        result = backfill_channel(
            make_channel(),
            {"version": 1, "channels": {}, "videos": {}},
            now=NOW,
            get=get,
            api_key="K",
            since=None,
            max_videos=1,
        )
        assert len(result.candidates) == 1
        playlist_calls = [c for c in get.calls if c[0].endswith("playlistItems")]
        assert len(playlist_calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_yt_feed.py -k Backfill -v`
Expected: FAIL with ImportError on `backfill_channel`

- [ ] **Step 3: Write the implementation** (append to `tools/yt_feed.py`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_yt_feed.py -q`
Expected: all PASS

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/test_yt_feed.py -q
git add tools/yt_feed.py tests/test_yt_feed.py
git commit -m "feat(yt-feed): backfill pager — floor ignored, ledger respected, read-only"
```

---

### Task 5: `mark` + `resolve` core functions

**Named defect to avoid:** wifey-#68's inverse — `mark` must never *move* an existing channel floor (`--channel-seen` uses `setdefault`); a moving floor is the moving-watermark semantics the spec rejected.

**Files:**

- Modify: `tools/yt_feed.py` (append)
- Modify: `tests/test_yt_feed.py` (append)

**Interfaces:**

- Consumes: `load_state`, `save_state`, `_VIDEO_ID_RE` (Tasks 1–2)
- Produces:
  - `run_mark(state_path: Path, *, ingested: list[str], skipped: list[str], channel_seen: list[str], candidates_json: Path | None, now: datetime) -> int` — returns count of videos marked
  - `resolve_handle(get: HttpGet, api_key: str, handle: str) -> str` — returns the ready-to-paste TOML block

- [ ] **Step 1: Write the failing tests** (append; extend imports with `resolve_handle, run_mark`)

```python
class TestMark:
    def test_writes_statuses_and_summary_count(self, tmp_path: Path) -> None:
        p = tmp_path / "s.json"
        n = run_mark(
            p,
            ingested=["aaaaaaaaaaa"],
            skipped=["bbbbbbbbbbb"],
            channel_seen=[],
            candidates_json=None,
            now=NOW,
        )
        assert n == 2
        state = load_state(p)
        assert state["videos"]["aaaaaaaaaaa"]["status"] == "ingested"
        assert state["videos"]["bbbbbbbbbbb"]["status"] == "skipped"
        assert state["videos"]["aaaaaaaaaaa"]["channel_id"] is None

    def test_candidates_json_enriches(self, tmp_path: Path) -> None:
        cj = tmp_path / "cands.json"
        cj.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "video_id": "aaaaaaaaaaa",
                            "channel_id": "UCx",
                            "title": "BTC weekly",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        p = tmp_path / "s.json"
        run_mark(
            p,
            ingested=["aaaaaaaaaaa"],
            skipped=[],
            channel_seen=[],
            candidates_json=cj,
            now=NOW,
        )
        entry = load_state(p)["videos"]["aaaaaaaaaaa"]
        assert entry["channel_id"] == "UCx"
        assert entry["title"] == "BTC weekly"

    def test_bad_id_aborts(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            run_mark(
                tmp_path / "s.json",
                ingested=["nope"],
                skipped=[],
                channel_seen=[],
                candidates_json=None,
                now=NOW,
            )

    def test_id_in_both_lists_aborts(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            run_mark(
                tmp_path / "s.json",
                ingested=["aaaaaaaaaaa"],
                skipped=["aaaaaaaaaaa"],
                channel_seen=[],
                candidates_json=None,
                now=NOW,
            )

    def test_remark_overwrites_last_wins(self, tmp_path: Path) -> None:
        p = tmp_path / "s.json"
        run_mark(
            p,
            ingested=[],
            skipped=["aaaaaaaaaaa"],
            channel_seen=[],
            candidates_json=None,
            now=NOW,
        )
        run_mark(
            p,
            ingested=["aaaaaaaaaaa"],
            skipped=[],
            channel_seen=[],
            candidates_json=None,
            now=NOW,
        )
        assert load_state(p)["videos"]["aaaaaaaaaaa"]["status"] == "ingested"

    def test_channel_seen_persists_but_never_moves_existing_floor(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "s.json"
        run_mark(
            p,
            ingested=[],
            skipped=[],
            channel_seen=["UCx=2026-07-17T00:00:00+00:00"],
            candidates_json=None,
            now=NOW,
        )
        assert (
            load_state(p)["channels"]["UCx"]["floor_ts_utc"]
            == "2026-07-17T00:00:00+00:00"
        )
        run_mark(
            p,
            ingested=[],
            skipped=[],
            channel_seen=["UCx=2026-07-25T00:00:00+00:00"],
            candidates_json=None,
            now=NOW,
        )
        # static floor: a later --channel-seen must NOT advance it
        assert (
            load_state(p)["channels"]["UCx"]["floor_ts_utc"]
            == "2026-07-17T00:00:00+00:00"
        )

    def test_bad_channel_seen_aborts(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            run_mark(
                tmp_path / "s.json",
                ingested=[],
                skipped=[],
                channel_seen=["UCx:2026-07-17T00:00:00+00:00"],  # colon, not =
                candidates_json=None,
                now=NOW,
            )


class TestResolve:
    def test_resolve_request_shape_and_toml_block(self) -> None:
        payload = {
            "items": [{"id": "UCreal", "snippet": {"title": "Into The Cryptoverse"}}]
        }
        get = FakeGet({"channels": [FakeResp(200, payload)]})
        block = resolve_handle(get, "K", "intothecryptoverse")
        url, params = get.calls[0]
        assert url.endswith("/channels")
        assert params["forHandle"] == "@intothecryptoverse"
        assert params["part"] == "id,snippet"
        assert "[[channel]]" in block
        assert 'id = "UCreal"' in block
        assert 'name = "Into The Cryptoverse"' in block

    def test_resolve_unknown_handle_aborts(self) -> None:
        get = FakeGet({"channels": [FakeResp(200, {"items": []})]})
        with pytest.raises(SystemExit):
            resolve_handle(get, "K", "@ghost")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_yt_feed.py -k "Mark or Resolve" -v`
Expected: FAIL with ImportError on `run_mark`

- [ ] **Step 3: Write the implementation** (append to `tools/yt_feed.py`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_yt_feed.py -q`
Expected: all PASS

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck && poetry run pytest tests/test_yt_feed.py -q
git add tools/yt_feed.py tests/test_yt_feed.py
git commit -m "feat(yt-feed): mark (sole state writer, static floors) + resolve"
```

---

### Task 6: CLI wiring (`main`) + output formatting

**Named defect to avoid:** poll writing state. `main`'s `poll`/`backfill` branches call no `save_state` under any path — there is a test for it.

**Files:**

- Modify: `tools/yt_feed.py` (append)
- Modify: `tests/test_yt_feed.py` (append)

**Interfaces:**

- Consumes: everything above.
- Produces:
  - `main(argv: list[str] | None = None, *, get: HttpGet = _requests_get, now: datetime | None = None) -> int`
  - `_results_to_dict(results: list[ChannelResult], now: datetime) -> dict[str, Any]` — the `--json` payload: `{"generated_utc", "candidates": [...], "channels": [{"channel_id", "channel_name", "floor_ts_utc", "excluded", "errors"}]}`
  - Exit codes: 0 ok · 1 any channel errored (poll/backfill) · 2 missing `YOUTUBE_API_KEY` · `SystemExit(msg)` (→ 1) for config/state/argument errors
  - `if __name__ == "__main__": sys.exit(main())` footer

- [ ] **Step 1: Write the failing tests** (append; extend imports with `main`)

```python
def write_config(tmp_path: Path, extra_channel: str = "") -> Path:
    p = tmp_path / "channels.toml"
    p.write_text(
        '[[channel]]\nid = "UCabcdefghijklmnopqrstu"\nname = "One"\n' + extra_channel,
        encoding="utf-8",
    )
    return p


class TestMainPoll:
    def test_missing_api_key_exits_2(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        rc = main(
            [
                "poll",
                "--config",
                str(write_config(tmp_path)),
                "--state",
                str(tmp_path / "s.json"),
            ],
            get=FakeGet({}),
            now=NOW,
        )
        assert rc == 2
        assert "YOUTUBE_API_KEY" in capsys.readouterr().err

    def test_partial_failure_exits_1_keeps_other_channel(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("YOUTUBE_API_KEY", "K")
        cfg = write_config(
            tmp_path, '[[channel]]\nid = "UCzzzzzzzzzzzzzzzzzzzzz"\nname = "Two"\n'
        )
        good = {
            "items": [
                playlist_item("ggggggggggg", "BTC weekly", "2026-07-31T02:00:00Z")
            ]
        }
        vids = {"items": [video_item("ggggggggggg", "PT21M")]}
        err = {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}
        get = FakeGet(
            {
                "playlistItems": [FakeResp(200, good), FakeResp(403, err)],
                "videos": [FakeResp(200, vids)],
            }
        )
        state_path = tmp_path / "s.json"
        rc = main(
            ["poll", "--config", str(cfg), "--state", str(state_path), "--json"],
            get=get,
            now=NOW,
        )
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert [c["video_id"] for c in payload["candidates"]] == ["ggggggggggg"]
        assert any(
            "quotaExceeded" in e for ch in payload["channels"] for e in ch["errors"]
        )
        # read-only invariant: poll wrote NOTHING
        assert not state_path.exists()

    def test_backfill_requires_channel_in_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("YOUTUBE_API_KEY", "K")
        with pytest.raises(SystemExit, match="not in config"):
            main(
                [
                    "backfill",
                    "UCnotconfigured000000000",
                    "--config",
                    str(write_config(tmp_path)),
                    "--state",
                    str(tmp_path / "s.json"),
                ],
                get=FakeGet({}),
                now=NOW,
            )

    def test_mark_via_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)  # mark needs no key
        state_path = tmp_path / "s.json"
        rc = main(
            [
                "mark",
                "--state",
                str(state_path),
                "--ingested",
                "aaaaaaaaaaa",
                "--skipped",
                "bbbbbbbbbbb",
                "--channel-seen",
                "UCx=2026-07-17T00:00:00+00:00",
            ],
            get=FakeGet({}),
            now=NOW,
        )
        assert rc == 0
        state = load_state(state_path)
        assert state["videos"]["aaaaaaaaaaa"]["status"] == "ingested"
        assert "UCx" in state["channels"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_yt_feed.py -k Main -v`
Expected: FAIL with ImportError on `main`

- [ ] **Step 3: Write the implementation** (append to `tools/yt_feed.py`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_yt_feed.py -q`
Expected: all PASS

- [ ] **Step 5: Full gates + commit**

```bash
make lint-py && make typecheck && make test
git add tools/yt_feed.py tests/test_yt_feed.py
git commit -m "feat(yt-feed): CLI — poll/backfill/mark/resolve with read-only poll invariant"
```

---

### Task 7: Config example, `.gitignore`, `.env.example`

**Files:**

- Create: `config/youtube_channels.toml.example`
- Modify: `.gitignore` (after the `config/coins.json` line)
- Modify: `.env.example` (after the `GROQ_API_KEY=` line)
- Modify: `tests/test_yt_feed.py` (append one test)

- [ ] **Step 1: Write the failing test** (append)

```python
class TestExampleConfig:
    def test_example_config_parses(self) -> None:
        cfg = load_feed_config(Path("config/youtube_channels.toml.example"))
        assert cfg.cold_start_days == 14
        assert cfg.channels[0].name == "Benjamin Cowen"
```

Run: `poetry run pytest tests/test_yt_feed.py -k Example -v` — expected FAIL (file missing).

- [ ] **Step 2: Create `config/youtube_channels.toml.example`**

First resolve Cowen's real channel id — never hand-guess an id (spec §4). If `YOUTUBE_API_KEY` is available in the environment run:
`PYTHONPATH=. poetry run python tools/yt_feed.py resolve benjaminjcowen`
and paste its output block. If no key is available in the execution environment, use the placeholder `id = "UCRvqjQPSeaWn-uEx-w0XOIg"` **only after verifying it**: `curl -s "https://www.youtube.com/@benjaminjcowen" | grep -o 'channel_id=[A-Za-z0-9_-]*' | head -1` must print the same id; otherwise leave `id = "UC__FILL_ME_VIA_RESOLVE"` and note it in the PR body as an operator to-do. Then write:

```toml
# YouTube channel follow list for /ingest-feed (ST10).
# Copy to config/youtube_channels.toml (gitignored — a follow list is personal).
# Resolve a channel id from a handle:
#   PYTHONPATH=. poetry run python tools/yt_feed.py resolve <handle>
# Spec: docs/superpowers/specs/2026-07-31-st10-youtube-feed-design.md

[feed]
cold_start_days = 14   # bounds the daily poll only; old videos stay reachable via `backfill`

[[channel]]
id = "<verified id per the step above>"
name = "Benjamin Cowen"
title_include = []
title_exclude = ["#shorts"]
min_duration_s = 180
lang = "en"
```

- [ ] **Step 3: `.gitignore` + `.env.example` edits**

In `.gitignore`, extend the config ignore block:

```gitignore
# Ignore only config/coins.json
config/coins.json
# Personal YouTube follow list for /ingest-feed (example is committed)
config/youtube_channels.toml
```

(Adjust the pre-existing comment if it reads oddly next to the new line — e.g. change it to `# Personal config (examples are committed)`.)

In `.env.example`, after `GROQ_API_KEY=`:

```bash
# /ingest-feed — YouTube Data API v3 key (key-only, no OAuth).
# Restrict to the YouTube Data API in the GCP console.
YOUTUBE_API_KEY=
```

- [ ] **Step 4: Run tests + verify the state path needs no gitignore change**

Run: `poetry run pytest tests/test_yt_feed.py -q` — all PASS.
Run: `git check-ignore docs/plans/yt-feed-state.json config/youtube_channels.toml` — both paths must print (already/now ignored).

- [ ] **Step 5: Commit**

```bash
git add config/youtube_channels.toml.example .gitignore .env.example tests/test_yt_feed.py
git commit -m "feat(yt-feed): committed channel-config example + gitignore/env plumbing"
```

---

### Task 8: `/ingest-video` retrospective-call rule

**Named defect to avoid:** the 07-29 e2e ledger-integrity finding itself — a retrospective call stamped with the video's `call_ts_utc` carries an entry set a day earlier, so the scorer resolves forward from a point where the outcome is partly known, flattering the hit rate.

**Files:**

- Modify: `.claude/skills/ingest-video/SKILL.md` (four targeted edits; `.claude/` is excluded from markdownlint, so verification is by grep)

**Placement note (deliberate, do not "fix"):** the rule lives in step 6's "Rules for the
subagent" (pass 2 is what emits the field), NOT in the shared inline classification rubric —
that rubric block is shared **verbatim** with `/ingest-x` ("keep the two in sync"), and
`retrospective` is a video-only field; adding it there would desync the pair or force an
unrelated `/ingest-x` change.

- [ ] **Step 1: Pass-2 item schema** — in the step-6 JSON block, after the line `"content_type": "claim | setup | mechanic",` insert:

```json
      "retrospective": false,
```

- [ ] **Step 2: Pass-2 rules** — append this bullet to the "Rules for the subagent" list in step 6:

```markdown
- `retrospective` — `true` when the speaker is reviewing or managing a position entered
  **before** this video (past-tense entry, "we entered yesterday", "已经进场", a
  screenshot of a prior day's fill), rather than issuing a new actionable call. When the
  entry timing is unclear, `true` — the conservative direction for ledger integrity
  (a retrospective stamped with this video's `call_ts_utc` scores forward from a point
  where the outcome is partly known, flattering the author's hit rate). For
  `claim`/`mechanic` items always `false`.
```

- [ ] **Step 3: Routing** — in step 8, change the routing table's `setup` row and add the drop row so the table reads:

```markdown
| content_type | verdict | Append to |
| --- | --- | --- |
| setup (`retrospective: false`) | — | `docs/plans/pundit-calls.jsonl` (one JSON line, schema below) |
| setup (`retrospective: true`) | — | **drop** — reason "retrospective — call predates video"; shown in the digest and the per-video note, never a Stream C write |
| mechanic | — | `docs/plans/mechanics-backlog.md` (a `- ` bullet) |
| claim | NOVEL | `docs/plans/thesis-inbox.md` (a draft `H` row) |
| claim | ALREADY-TESTED / FROZEN-CATEGORY / NOT-FALSIFIABLE | **drop** — state "seen, verdict X", write nothing |
```

- [ ] **Step 4: Digest + note columns** — in step 7, extend the digest table column list `· content_type · verdict ·` to `· content_type · retrospective · verdict ·`; in step 9, extend the note items-table column list the same way (`ts · content_type · retrospective · verdict · routing outcome · vision_confidence · frame_path`).

- [ ] **Step 5: Verify + commit**

```bash
grep -c "retrospective" .claude/skills/ingest-video/SKILL.md   # expect ≥ 6
git add .claude/skills/ingest-video/SKILL.md
git commit -m "feat(ingest-video): retrospective-call drop rule (07-29 e2e ledger-integrity finding)"
```

---

### Task 9: `/ingest-feed` skill

**Files:**

- Create: `.claude/skills/ingest-feed/SKILL.md`

- [ ] **Step 1: Write the skill file** (full content below; frontmatter mirrors the sibling skills)

````markdown
---
name: ingest-feed
description: Poll the configured YouTube channel follow list for new uploads (tools/yt_feed.py — read-only Data API polling + explicit-outcome ledger) and feed the picked videos into the existing /ingest-video batch flow, marking consumption ONLY after the review gate routes the batch. Also drives deep back-catalogue ingestion per channel via the backfill subcommand. Invoke when the user says "/ingest-feed", "what's new on youtube", "poll the channels", "backfill <channel>", or "ingest the old videos from <channel>".
---

# Ingest feed (YouTube channel auto-feed)

Spec: `docs/superpowers/specs/2026-07-31-st10-youtube-feed-design.md`. Companion of
`/ingest-video` — this skill only automates *discovery*; every research-sink write still
happens inside the `/ingest-video` flow behind its single approval gate.

## Flow

### 1. Poll (or backfill)

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py poll --json
```

For a back-catalogue request ("backfill Cowen", "ingest the old videos"), find the
channel's `UC…` id in `config/youtube_channels.toml` and run instead:

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py backfill <UC…> --json \
  [--since 2026-01-01] [--max-videos 200]
```

Save the JSON output to a scratchpad file — step 4 needs it for `--candidates-json`.
Exit 1 means at least one channel errored; report the errors and continue with the
candidates that did resolve. Exit 2 = `YOUTUBE_API_KEY` missing from `.env`.

### 2. Present the candidate table

One row per candidate: channel · title · duration · age · `est_tokens` (a ±30%
ranking-grade estimate — rank by it, don't budget by it). Below the table: each
channel's exclusion summary (`below_floor` / `ledgered` / `title_filtered` /
`too_short` / `live_or_upcoming` / `unavailable`) and any errors — never hide drops.

Zero candidates → report that and stop.

**Re-presented-candidate guard:** if a candidate's per-video note already exists under
`docs/plans/video-notes/` (match on the video id in frontmatter), flag the row —
"note exists — was this already routed?" — a prior run may have died between routing
and `mark`. Confirm with the operator before re-ingesting it.

For a large backfill, recommend a tranche sized to the remaining session quota rather
than ingesting the whole list — the ledger carries the progress across days.

### 3. Operator picks

Ask which candidates to ingest. Three outcomes per candidate, and only the first two
are ever marked:

- **ingest** — goes into the batch (→ `mark --ingested` in step 5)
- **skip** — operator explicitly declines, permanently (→ `mark --skipped`)
- **defer** (the default for anything not named) — NO mark; it simply reappears next
  poll/backfill

### 4. Run the ingest batch

Execute the `/ingest-video` flow (`.claude/skills/ingest-video/SKILL.md`), steps 1–9,
over the picked URLs. Follow that skill by reference — do not restate or fork it here.
Its digest + single approval remain the only gate before any research-sink write.

### 5. Mark — immediately after routing completes

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py mark \
  --ingested <picked ids…> --skipped <explicitly skipped ids…> \
  --candidates-json <scratchpad file from step 1> \
  --channel-seen <UC…>=<floor_ts_utc from the poll output, one per channel polled>
```

`--channel-seen` persists first-seen channels' static floors (`setdefault` — it can
never move an existing floor). Do this in the same turn as routing: the gap between
routing and mark is the one failure window (see Guardrails).

### 6. Report

Per candidate: ingested (→ which streams) / skipped / deferred. Include the mark
summary line ("marked N video(s)") as evidence the ledger write happened.

## Guardrails

- **The historical defect this design exists to avoid (wifey PR #68, watermark-on-send;
  same class the scanner's N6 fixed):** consumption stamped at fetch lets an aborted
  run permanently eat videos. Therefore `poll`/`backfill` write NOTHING; only `mark`
  writes, and only after the review gate routed the batch. Never "optimize" by marking
  early, and never edit `docs/plans/yt-feed-state.json` by hand.
- If a run dies between routing and `mark`, the routed videos re-present next poll —
  that is deliberate (visible duplication beats invisible loss). The step-2 note-exists
  guard is how the duplicate gets caught.
- This skill never writes to `thesis-inbox.md` / `mechanics-backlog.md` /
  `pundit-calls.jsonl` itself — those writes live inside `/ingest-video`'s flow only.
- A follow-list change (`config/youtube_channels.toml`) is a deliberate operator edit;
  `resolve` prints a block to paste, it never writes config.
- Quota: poll ≈ 2–3 units/channel/day against 10,000/day — never call `search.list`
  (100 units); the tool doesn't, don't add it.
````

- [ ] **Step 2: Verify + commit**

```bash
ls .claude/skills/ingest-feed/SKILL.md
git add .claude/skills/ingest-feed/SKILL.md
git commit -m "feat(ingest-feed): /ingest-feed skill — poll → pick → /ingest-video → mark"
```

---

### Task 10: Docs, full gates, manual e2e

**Files:**

- Modify: `README.md` (tools + skills sections)
- Modify: `CLAUDE.md` (tools list + Agent Skills table)

- [ ] **Step 1: CLAUDE.md** — add to the `tools/` bullet list (alphabetical/topical placement near `video_fetch.py`):

```markdown
- `yt_feed.py` — YouTube channel auto-feed backing `/ingest-feed` (ST10; spec `docs/superpowers/specs/2026-07-31-st10-youtube-feed-design.md`). Read-only `poll` of each configured channel's uploads playlist (Data API v3, `YOUTUBE_API_KEY`, ~2–3 units/channel/day, never `search.list`) + `backfill` deep pager (floor ignored, ledger respected) + the explicit-outcome ledger (`mark` is the ONLY writer — consumption stamped post-review-gate; the wifey-#68 watermark-on-send defect class is structurally impossible: no fetch-time writes, no moving watermark, static per-channel `floor_ts`) + `resolve` handle→TOML. Config gitignored `config/youtube_channels.toml` (committed `.example`); state `docs/plans/yt-feed-state.json` (gitignored, atomic writes, loud-abort on malformed). Injected HTTP `get` → network-free request-shape tests. Run via `PYTHONPATH=. poetry run python tools/yt_feed.py poll|backfill|mark|resolve`.
```

And a row in the Agent Skills table:

```markdown
| `ingest-feed` | `/ingest-feed` | Poll the gitignored YouTube channel follow list for new uploads → present-and-pick (est-token ranking) → existing `/ingest-video` flow → post-review `mark`; `backfill` for deep back-catalogues | Daily habit; after adding a channel; "backfill <channel>" for old videos |
```

- [ ] **Step 2: README.md** — mirror the CLAUDE.md content in README style: one bullet in the tools section, one row/mention in the skills section (match the surrounding formatting; README IS markdownlint-checked — spaced table delimiters, languages on fences).

- [ ] **Step 3: Full gates**

```bash
make lint-py && make typecheck && make test && make lint-md && make test-regression
```

Expected: all green; regression goldens untouched (state each result plainly in the task report — if one was skipped or failed, say so).

- [ ] **Step 4: Manual e2e (load-bearing — spec §12; do NOT skip silently)**

Requires `YOUTUBE_API_KEY` in `.env` and a real `config/youtube_channels.toml`. If the execution environment lacks either, STOP and report "manual e2e pending operator" — the PR body must carry it as an explicit unchecked item.

```bash
PYTHONPATH=. poetry run python tools/yt_feed.py poll            # sane candidates?
PYTHONPATH=. poetry run python tools/yt_feed.py poll --json > /tmp/claude-poll.json
ls docs/plans/yt-feed-state.json 2>/dev/null || echo "OK: poll wrote nothing"
PYTHONPATH=. poetry run python tools/yt_feed.py backfill <UC…> --max-videos 60 --json | head -50
PYTHONPATH=. poetry run python tools/yt_feed.py mark --ingested <one id> \
  --candidates-json /tmp/claude-poll.json --channel-seen <UC…>=<floor from poll json>
PYTHONPATH=. poetry run python tools/yt_feed.py poll            # marked id gone?
```

Verify: durations/ages sane; backfill pages past 50; the marked id no longer appears; state file matches the spec §6 shape.

Spec §12.3 (retrospective rule visible end-to-end on a real prior-day-entry video) cannot be
forced from a poll — it validates on the next real ingest batch. Carry it in the PR body as a
pending-operator item alongside any pending e2e steps.

- [ ] **Step 5: Commit docs**

```bash
git add README.md CLAUDE.md
git commit -m "docs: README + CLAUDE.md rows for /ingest-feed + tools/yt_feed.py"
```

---

## Post-plan (orchestrator, not a subagent task)

- `/pr-summary` then `gh pr create` from `feat/ingest-feed` (PR includes spec + plan + implementation), then `/post-branch`.
- PR body carries the manual-e2e status explicitly (done, or pending-operator with the exact commands).
- MEMORY.md Current State update per the Session Memory Protocol.
