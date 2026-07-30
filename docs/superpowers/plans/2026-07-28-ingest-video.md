# `/ingest-video` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a pasted YouTube or X video URL (including Chinese-language video) into routed research items in the existing three-stream pipeline, plus a durable per-video note, without frames ever reaching the main thread.

**Architecture:** Three pure/injectable Python tools under `tools/` do all deterministic work (frame selection, call-time resolution, fetch/transcribe/extract). A skill orchestrates two sonnet subagent passes — a cheap text pass that decides *which* frames exist, then a vision pass over only those frames. Routing reuses `tools/x_route.py` unchanged.

**Tech Stack:** Python 3.11+, Poetry, pytest, mypy strict, ruff. New runtime deps: `yt-dlp`. External: `ffmpeg` (already installed), Groq `whisper-large-v3` (free tier, caption-less videos only).

**Spec:** `docs/superpowers/specs/2026-07-28-ingest-video-design.md`

## Global Constraints

- **No network in the test suite.** `run` (subprocess), `get` (HTTP), `sleep`, `rng` are injected parameters with real defaults. Tests pass fakes. This mirrors `tools/x_fetch.py`'s existing contract.
- **Nothing touches `analytics.db`.** Blast radius is `docs/plans/` and `.cache/` only, both already gitignored.
- **`make lint-py`, `make typecheck` (mypy strict), `make test` must all pass** before each commit. Every function needs full type annotations including `-> None`.
- **A-priori constants, fixed before tuning** — copied verbatim from the spec:
  `ITEM_CAP = 5`, `FRAME_CAP = 15`, `DEDUP_WINDOW_S = 45`, `SAFETY_SAMPLE_S = 300`,
  `BACKLOG_THRESHOLD_H = 24`, `STATED_TS_MAX_LEAD_H = 168`.
- **`call_ts_utc` is never `now`.** It resolves from stated time (bounded) or publish time. This is the look-ahead guard; see Task 2.
- **Routing taxonomy is not forked.** `tools/x_route.py::route_target` is imported, never copied or modified.
- Conventional commits: `feat:`, `test:`, `docs:`, `build:`.

## File Structure

| File | Responsibility |
| --- | --- |
| `tools/video_marks.py` | **New.** Pure frame selection. Owns `TranscriptSegment` (stdlib-only, so the pure module never imports a network dep). |
| `tools/video_calltime.py` | **New.** Pure call-time resolution + CLI. The look-ahead guard, kept out of prompt-space. |
| `tools/video_fetch.py` | **New.** yt-dlp metadata, captions-or-Groq transcript, ffmpeg frames, dedup cache, batch, CLI. |
| `.claude/skills/ingest-video/SKILL.md` | **New.** Orchestration only — two subagent passes, digest, one approval, writes. |
| `tests/test_video_marks.py` | **New.** |
| `tests/test_video_calltime.py` | **New.** |
| `tests/test_video_fetch.py` | **New.** |
| `pyproject.toml` | **Modify.** Add `yt-dlp`. |
| `CLAUDE.md`, `README.md` | **Modify.** Document the tools + skill. |

**Deviation from the spec, deliberate:** the spec described three units. This plan adds a fourth,
`tools/video_calltime.py`, because call-time resolution is the look-ahead-critical logic and must
be deterministic tested code rather than date arithmetic done by an LLM inside a prompt. LLM
timezone arithmetic is a known failure mode; this is a strict improvement and should be noted in
the spec when the branch merges.

---

### Task 1: Pure frame selection (`tools/video_marks.py`)

**Files:**

- Create: `tools/video_marks.py`
- Test: `tests/test_video_marks.py`

**Interfaces:**

- Consumes: nothing (stdlib only).
- Produces:
  - `TranscriptSegment(ts_s: float, text: str, lang: str)` — frozen dataclass, imported by `tools/video_fetch.py` in Task 3.
  - `FrameMark(ts_s: float, reason: str, weight: int)` — frozen dataclass. `reason` ∈ `{"item", "deixis", "level", "sample"}`.
  - `select(segments: list[TranscriptSegment], item_ts: list[float], duration_s: float, *, cap: int = FRAME_CAP, window_s: float = DEDUP_WINDOW_S, sample_s: float = SAFETY_SAMPLE_S) -> list[FrameMark]`
  - Constants `ITEM_CAP`, `FRAME_CAP`, `DEDUP_WINDOW_S`, `SAFETY_SAMPLE_S`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_video_marks.py`:

```python
"""Tests for tools/video_marks.py — pure, no I/O."""

from __future__ import annotations

from tools.video_marks import (
    DEDUP_WINDOW_S,
    FRAME_CAP,
    FrameMark,
    TranscriptSegment,
    dedupe,
    deixis_marks,
    level_marks,
    sample_marks,
    select,
)


def seg(ts: float, text: str, lang: str = "en") -> TranscriptSegment:
    return TranscriptSegment(ts_s=ts, text=text, lang=lang)


def test_deixis_marks_english() -> None:
    segments = [
        seg(10.0, "the market is quiet today"),
        seg(20.0, "I am taking a long here"),
        seg(30.0, "look at this structure"),
    ]
    marks = deixis_marks(segments)
    assert [m.ts_s for m in marks] == [20.0, 30.0]
    assert all(m.reason == "deixis" for m in marks)


def test_deixis_marks_chinese() -> None:
    segments = [seg(5.0, "大盘很安静", "zh"), seg(15.0, "我在这里做多", "zh")]
    marks = deixis_marks(segments)
    assert [m.ts_s for m in marks] == [15.0]


def test_level_marks_needs_symbol_context() -> None:
    segments = [
        seg(10.0, "I have been doing this for 15 years"),
        seg(20.0, "BTC is at 62400 right now"),
    ]
    marks = level_marks(segments)
    assert [m.ts_s for m in marks] == [20.0]
    assert marks[0].reason == "level"


def test_sample_marks_every_interval() -> None:
    marks = sample_marks(duration_s=650.0, sample_s=300.0)
    assert [m.ts_s for m in marks] == [0.0, 300.0, 600.0]
    assert all(m.reason == "sample" for m in marks)


def test_dedupe_keeps_highest_weight_in_window() -> None:
    marks = [
        FrameMark(ts_s=10.0, reason="sample", weight=1),
        FrameMark(ts_s=20.0, reason="item", weight=3),
        FrameMark(ts_s=30.0, reason="deixis", weight=2),
        FrameMark(ts_s=200.0, reason="deixis", weight=2),
    ]
    kept = dedupe(marks, window_s=DEDUP_WINDOW_S)
    assert [(m.ts_s, m.reason) for m in kept] == [(20.0, "item"), (200.0, "deixis")]


def test_select_respects_cap_and_prefers_items() -> None:
    segments = [seg(float(i * 10), f"look here number {i}") for i in range(40)]
    marks = select(segments, item_ts=[5.0], duration_s=400.0, cap=3)
    assert len(marks) == 3
    assert marks[0].reason == "item"
    assert marks == sorted(marks, key=lambda m: m.ts_s)


def test_select_is_deterministic() -> None:
    segments = [seg(10.0, "long here"), seg(90.0, "BTC at 62400")]
    a = select(segments, item_ts=[], duration_s=120.0)
    b = select(segments, item_ts=[], duration_s=120.0)
    assert a == b


def test_select_never_exceeds_frame_cap_by_default() -> None:
    segments = [seg(float(i * 5), "taking a long here") for i in range(200)]
    marks = select(segments, item_ts=[], duration_s=1000.0)
    assert len(marks) <= FRAME_CAP
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_video_marks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.video_marks'`

- [ ] **Step 3: Write the implementation**

Create `tools/video_marks.py`:

```python
"""Pure frame-selection for /ingest-video — which moments of a video are worth seeing.

Deliberately NOT scene-change based (that is what claude-video does, and it returns
~100 near-identical talking-head frames while missing the annotated chart). Frames are
selected where the *transcript* indicates the speaker is pointing at something.

Stdlib only: tools/video_fetch.py imports TranscriptSegment from here, so this module
must never pull a network dependency into the pure import chain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ITEM_CAP = 5
FRAME_CAP = 15
DEDUP_WINDOW_S = 45.0
SAFETY_SAMPLE_S = 300.0

_WEIGHTS = {"item": 3, "deixis": 2, "level": 2, "sample": 1}

_DEIXIS_PATTERNS: tuple[str, ...] = (
    r"\bright here\b",
    r"\bover here\b",
    r"\blook (?:at )?(?:here|this)\b",
    r"\b(?:long|short|buy|sell)(?:ing)? (?:it )?here\b",
    r"\bthis (?:level|structure|zone|area|candle|wick|move|setup)\b",
    r"\bthese (?:levels|zones|highs|lows)\b",
    r"这里",
    r"这个(?:位置|区域|结构|水平)",
    r"看(?:这里|这个)",
)
_DEIXIS_RE = re.compile("|".join(_DEIXIS_PATTERNS), re.IGNORECASE)

_SYMBOL_RE = re.compile(
    r"\b(?:btc|eth|sol|xrp|doge|bnb|ada|avax|link|bitcoin|ether(?:eum)?)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*[km]?\b", re.IGNORECASE)


@dataclass(frozen=True)
class TranscriptSegment:
    ts_s: float
    text: str
    lang: str


@dataclass(frozen=True)
class FrameMark:
    ts_s: float
    reason: str
    weight: int


def _mark(ts_s: float, reason: str) -> FrameMark:
    return FrameMark(ts_s=ts_s, reason=reason, weight=_WEIGHTS[reason])


def deixis_marks(segments: list[TranscriptSegment]) -> list[FrameMark]:
    """Segments where the speaker points at something only the frame shows."""
    return [_mark(s.ts_s, "deixis") for s in segments if _DEIXIS_RE.search(s.text)]


def level_marks(segments: list[TranscriptSegment]) -> list[FrameMark]:
    """Segments naming a price near a symbol — the chart is ground truth for the number."""
    return [
        _mark(s.ts_s, "level")
        for s in segments
        if _SYMBOL_RE.search(s.text) and _NUMBER_RE.search(s.text)
    ]


def sample_marks(
    duration_s: float, *, sample_s: float = SAFETY_SAMPLE_S
) -> list[FrameMark]:
    """Low-rate safety sample so the vision pass is not blind between pointing moments."""
    if sample_s <= 0 or duration_s <= 0:
        return []
    count = int(duration_s // sample_s) + 1
    return [_mark(i * sample_s, "sample") for i in range(count)]


def dedupe(
    marks: list[FrameMark], *, window_s: float = DEDUP_WINDOW_S
) -> list[FrameMark]:
    """Collapse marks inside `window_s` to the highest-weight one.

    Stops a twenty-second riff about "here" yielding eight near-identical frames.
    """
    kept: list[FrameMark] = []
    for mark in sorted(marks, key=lambda m: (m.ts_s, -m.weight)):
        if kept and mark.ts_s - kept[-1].ts_s < window_s:
            if mark.weight > kept[-1].weight:
                kept[-1] = mark
            continue
        kept.append(mark)
    return kept


def select(
    segments: list[TranscriptSegment],
    item_ts: list[float],
    duration_s: float,
    *,
    cap: int = FRAME_CAP,
    window_s: float = DEDUP_WINDOW_S,
    sample_s: float = SAFETY_SAMPLE_S,
) -> list[FrameMark]:
    """Ranked, capped, deduplicated frame timestamps, ordered chronologically.

    Ranking is by weight then earliness, so items beat deixis beats sampling and the
    cap always keeps the most informative frames. Deterministic: no randomness.
    """
    candidates = (
        [_mark(ts, "item") for ts in item_ts]
        + deixis_marks(segments)
        + level_marks(segments)
        + sample_marks(duration_s, sample_s=sample_s)
    )
    deduped = dedupe(candidates, window_s=window_s)
    ranked = sorted(deduped, key=lambda m: (-m.weight, m.ts_s))[:cap]
    return sorted(ranked, key=lambda m: m.ts_s)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_video_marks.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Lint and typecheck**

Run: `make lint-py && make typecheck`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add tools/video_marks.py tests/test_video_marks.py
git commit -m "feat(ingest-video): pure transcript-driven frame selection"
```

---

### Task 2: Call-time resolution (`tools/video_calltime.py`)

This is the look-ahead guard. A stated time is pundit-supplied and unverifiable; publish time is
a trustworthy upper bound. Prefer stated, but reject anything not strictly before publish or more
than `STATED_TS_MAX_LEAD_H` earlier, and resolve date-only statements to the conservative
end-of-day edge.

**Files:**

- Create: `tools/video_calltime.py`
- Test: `tests/test_video_calltime.py`

**Interfaces:**

- Consumes: nothing (stdlib only).
- Produces:
  - `CallTime(call_ts_utc: str, call_ts_source: str, publish_ts_utc: str, stated_ts_raw: str)` — frozen dataclass; `call_ts_source` ∈ `{"stated", "publish"}`.
  - `resolve_call_ts(publish_ts_utc: str, *, stated_ts_utc: str | None = None, stated_date_only: bool = False, stated_ts_raw: str = "", max_lead_h: int = STATED_TS_MAX_LEAD_H) -> CallTime`
  - `is_backlog(publish_ts_utc: str, ingested_ts_utc: str, *, threshold_h: int = BACKLOG_THRESHOLD_H) -> bool`
  - `main(argv: list[str] | None = None) -> int` — CLI emitting JSON, called by the skill in Task 6.
  - Constants `STATED_TS_MAX_LEAD_H`, `BACKLOG_THRESHOLD_H`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_video_calltime.py`:

```python
"""Tests for tools/video_calltime.py — the look-ahead guard. Pure, no I/O."""

from __future__ import annotations

import json

import pytest

from tools.video_calltime import (
    CallTime,
    is_backlog,
    main,
    resolve_call_ts,
)

PUB = "2026-07-28T14:00:00+00:00"


def test_falls_back_to_publish_when_no_stated_time() -> None:
    got = resolve_call_ts(PUB)
    assert got == CallTime(
        call_ts_utc=PUB, call_ts_source="publish", publish_ts_utc=PUB, stated_ts_raw=""
    )


def test_prefers_stated_time_when_valid() -> None:
    got = resolve_call_ts(
        PUB, stated_ts_utc="2026-07-28T08:00:00+00:00", stated_ts_raw="it's 8am Monday"
    )
    assert got.call_ts_utc == "2026-07-28T08:00:00+00:00"
    assert got.call_ts_source == "stated"
    assert got.stated_ts_raw == "it's 8am Monday"
    assert got.publish_ts_utc == PUB


def test_rejects_stated_time_at_or_after_publish() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="2026-07-28T16:00:00+00:00")
    assert got.call_ts_source == "publish"
    assert got.call_ts_utc == PUB


def test_rejects_stated_time_leading_publish_by_more_than_max() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="2026-07-01T08:00:00+00:00")
    assert got.call_ts_source == "publish"


def test_date_only_resolves_to_conservative_end_of_day() -> None:
    got = resolve_call_ts(
        PUB, stated_ts_utc="2026-07-27T00:00:00+00:00", stated_date_only=True
    )
    assert got.call_ts_utc == "2026-07-27T23:59:59+00:00"
    assert got.call_ts_source == "stated"


def test_date_only_clamps_below_publish() -> None:
    got = resolve_call_ts(
        PUB, stated_ts_utc="2026-07-28T00:00:00+00:00", stated_date_only=True
    )
    assert got.call_ts_utc == PUB
    assert got.call_ts_source == "publish"


def test_unparseable_stated_time_falls_back() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="last Tuesday-ish")
    assert got.call_ts_source == "publish"


def test_unparseable_publish_time_raises() -> None:
    with pytest.raises(ValueError, match="publish_ts_utc"):
        resolve_call_ts("not a timestamp")


def test_is_backlog_true_beyond_threshold() -> None:
    assert is_backlog(PUB, "2026-07-30T14:00:00+00:00") is True


def test_is_backlog_false_within_threshold() -> None:
    assert is_backlog(PUB, "2026-07-28T20:00:00+00:00") is False


def test_cli_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--publish", PUB, "--stated", "2026-07-28T08:00:00+00:00"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["call_ts_source"] == "stated"
    assert payload["call_ts_utc"] == "2026-07-28T08:00:00+00:00"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_video_calltime.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.video_calltime'`

- [ ] **Step 3: Write the implementation**

Create `tools/video_calltime.py`:

```python
"""Resolve when a video call was actually made — the /ingest-video look-ahead guard.

tools/pundit_score.py resolves every pundit call forward from call_ts_utc. A speaker
often opens with the date/time, which is closer to when the call was made than the
publish timestamp — but that is pundit-supplied and unverifiable, and it moves in the
look-ahead-permitting direction. Publish time is a trustworthy upper bound.

So: prefer stated, but bound it. Kept as deterministic tested code rather than prompt
logic because LLM timezone arithmetic is a known failure mode and this field decides
whether every author's hit rate is honest.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

STATED_TS_MAX_LEAD_H = 168
BACKLOG_THRESHOLD_H = 24


@dataclass(frozen=True)
class CallTime:
    call_ts_utc: str
    call_ts_source: str
    publish_ts_utc: str
    stated_ts_raw: str


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def resolve_call_ts(
    publish_ts_utc: str,
    *,
    stated_ts_utc: str | None = None,
    stated_date_only: bool = False,
    stated_ts_raw: str = "",
    max_lead_h: int = STATED_TS_MAX_LEAD_H,
) -> CallTime:
    """Stated time if it survives every bound, else publish time."""
    publish = _parse(publish_ts_utc)
    if publish is None:
        raise ValueError(f"publish_ts_utc is not ISO-8601: {publish_ts_utc!r}")

    fallback = CallTime(
        call_ts_utc=publish_ts_utc,
        call_ts_source="publish",
        publish_ts_utc=publish_ts_utc,
        stated_ts_raw=stated_ts_raw,
    )
    if stated_ts_utc is None:
        return fallback
    stated = _parse(stated_ts_utc)
    if stated is None:
        return fallback
    if stated_date_only:
        stated = stated + timedelta(hours=23, minutes=59, seconds=59)
    if stated >= publish:
        return fallback
    if publish - stated > timedelta(hours=max_lead_h):
        return fallback
    return CallTime(
        call_ts_utc=stated.isoformat(),
        call_ts_source="stated",
        publish_ts_utc=publish_ts_utc,
        stated_ts_raw=stated_ts_raw,
    )


def is_backlog(
    publish_ts_utc: str,
    ingested_ts_utc: str,
    *,
    threshold_h: int = BACKLOG_THRESHOLD_H,
) -> bool:
    """True when our ingest lags publication — describes our lag, not the pundit's."""
    publish = _parse(publish_ts_utc)
    ingested = _parse(ingested_ts_utc)
    if publish is None or ingested is None:
        return False
    return ingested - publish > timedelta(hours=threshold_h)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve a video call timestamp (stated time preferred, bounded)."
    )
    parser.add_argument("--publish", required=True, help="ISO-8601 publish timestamp")
    parser.add_argument("--stated", default=None, help="ISO-8601 stated timestamp")
    parser.add_argument(
        "--date-only", action="store_true", help="stated value is a date with no time"
    )
    parser.add_argument("--stated-raw", default="", help="verbatim quote, for audit")
    parser.add_argument("--ingested", default=None, help="ISO-8601 ingest timestamp")
    args = parser.parse_args(argv)

    call = resolve_call_ts(
        args.publish,
        stated_ts_utc=args.stated,
        stated_date_only=args.date_only,
        stated_ts_raw=args.stated_raw,
    )
    payload: dict[str, object] = dict(asdict(call))
    if args.ingested:
        payload["backlog"] = is_backlog(args.publish, args.ingested)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_video_calltime.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Lint and typecheck**

Run: `make lint-py && make typecheck`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add tools/video_calltime.py tests/test_video_calltime.py
git commit -m "feat(ingest-video): bounded stated-vs-publish call-time resolution"
```

---

### Task 3: Video metadata via yt-dlp (`tools/video_fetch.py`, part 1)

**Files:**

- Create: `tools/video_fetch.py`
- Create: `tests/test_video_fetch.py`
- Modify: `pyproject.toml` (add `yt-dlp`)

**Interfaces:**

- Consumes: `TranscriptSegment` from `tools.video_marks` (Task 1).
- Produces:
  - `VideoMeta(source: str, video_id: str, author: str, title: str, publish_ts_utc: str, duration_s: float, lang: str, url: str)`
  - `Unavailable(reason: str)`
  - `RunProc` protocol: `__call__(cmd: list[str]) -> Completedish` where `Completedish` has `returncode: int`, `stdout: str`, `stderr: str`.
  - `parse_video_url(url: str) -> tuple[str, str]` → `(source, video_id)`; `source` ∈ `{"youtube", "x-video"}`.
  - `fetch_meta(url: str, *, run: RunProc = _subprocess_run) -> VideoMeta | Unavailable`

- [ ] **Step 1: Add the dependency**

Run: `poetry add yt-dlp`
Expected: `pyproject.toml` and `poetry.lock` updated. Never hand-edit the lock.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_video_fetch.py`:

```python
"""Tests for tools/video_fetch.py — no real network, no real subprocesses."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools.video_fetch import (
    Unavailable,
    VideoMeta,
    fetch_meta,
    parse_video_url,
)

YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
X_URL = "https://x.com/someone/status/1234567890"

YTDLP_JSON = json.dumps(
    {
        "id": "dQw4w9WgXcQ",
        "uploader_id": "@cryptoTrader",
        "title": "BTC weekly outlook",
        "timestamp": 1785247200,
        "duration": 2280,
        "language": "zh",
    }
)


@dataclass
class FakeProc:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def make_run(proc: FakeProc) -> Callable[[list[str]], FakeProc]:
    def _run(cmd: list[str]) -> FakeProc:
        return proc

    return _run


def test_parse_video_url_youtube_watch() -> None:
    assert parse_video_url(YT_URL) == ("youtube", "dQw4w9WgXcQ")


def test_parse_video_url_youtube_short() -> None:
    assert parse_video_url("https://youtu.be/dQw4w9WgXcQ") == ("youtube", "dQw4w9WgXcQ")


def test_parse_video_url_x() -> None:
    assert parse_video_url(X_URL) == ("x-video", "1234567890")


def test_parse_video_url_rejects_unknown_host() -> None:
    with pytest.raises(ValueError, match="not a supported video URL"):
        parse_video_url("https://example.com/video/1")


def test_fetch_meta_maps_ytdlp_json() -> None:
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, YTDLP_JSON)))
    assert isinstance(meta, VideoMeta)
    assert meta.source == "youtube"
    assert meta.video_id == "dQw4w9WgXcQ"
    assert meta.author == "@cryptoTrader"
    assert meta.duration_s == 2280.0
    assert meta.lang == "zh"
    assert meta.publish_ts_utc.startswith("2026-")


def test_fetch_meta_unavailable_on_nonzero_exit() -> None:
    got = fetch_meta(YT_URL, run=make_run(FakeProc(1, "", "Private video")))
    assert isinstance(got, Unavailable)
    assert "Private video" in got.reason


def test_fetch_meta_unavailable_on_bad_json() -> None:
    got = fetch_meta(YT_URL, run=make_run(FakeProc(0, "not json")))
    assert isinstance(got, Unavailable)
    assert "JSON" in got.reason
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_video_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.video_fetch'`

- [ ] **Step 4: Write the implementation**

Create `tools/video_fetch.py`:

```python
"""Fetch, transcribe, and frame-extract videos for /ingest-video.

Read-only over the public internet. yt-dlp for metadata + captions, ffmpeg for frames,
Groq whisper-large-v3 only when a video has no captions. `run` (subprocess), `get`
(HTTP), `sleep` and `rng` are injected so the test suite never touches the network —
same contract as tools/x_fetch.py.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

_YT_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})")
_X_RE = re.compile(r"(?:twitter|x)\.com/[^/]+/status/(\d+)")


class Completedish(Protocol):
    returncode: int
    stdout: str
    stderr: str


class RunProc(Protocol):
    def __call__(self, cmd: list[str]) -> Completedish: ...


def _subprocess_run(cmd: list[str]) -> Completedish:
    return subprocess.run(  # type: ignore[return-value]
        cmd, capture_output=True, text=True, timeout=600, check=False
    )


@dataclass(frozen=True)
class VideoMeta:
    source: str
    video_id: str
    author: str
    title: str
    publish_ts_utc: str
    duration_s: float
    lang: str
    url: str


@dataclass(frozen=True)
class Unavailable:
    reason: str


def parse_video_url(url: str) -> tuple[str, str]:
    match = _YT_RE.search(url)
    if match:
        return "youtube", match.group(1)
    match = _X_RE.search(url)
    if match:
        return "x-video", match.group(1)
    raise ValueError(f"not a supported video URL: {url!r}")


def fetch_meta(url: str, *, run: RunProc = _subprocess_run) -> VideoMeta | Unavailable:
    source, video_id = parse_video_url(url)
    proc = run(["yt-dlp", "--dump-json", "--no-warnings", "--skip-download", url])
    if proc.returncode != 0:
        return Unavailable(proc.stderr.strip() or f"yt-dlp exit {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return Unavailable("yt-dlp returned non-JSON output")
    if not isinstance(data, dict):
        return Unavailable("yt-dlp returned unexpected JSON shape")
    timestamp = data.get("timestamp")
    publish = (
        datetime.fromtimestamp(float(timestamp), UTC).isoformat()
        if isinstance(timestamp, (int, float))
        else ""
    )
    return VideoMeta(
        source=source,
        video_id=video_id,
        author=str(data.get("uploader_id") or data.get("uploader") or ""),
        title=str(data.get("title") or ""),
        publish_ts_utc=publish,
        duration_s=float(data.get("duration") or 0.0),
        lang=str(data.get("language") or ""),
        url=url,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_video_fetch.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 6: Lint and typecheck**

Run: `make lint-py && make typecheck`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add tools/video_fetch.py tests/test_video_fetch.py pyproject.toml poetry.lock
git commit -m "feat(ingest-video): yt-dlp metadata fetch with injected subprocess"
```

---

### Task 4: Transcript — captions first, Groq fallback

**Files:**

- Modify: `tools/video_fetch.py` (append)
- Modify: `tests/test_video_fetch.py` (append)

**Interfaces:**

- Consumes: `VideoMeta`, `RunProc` (Task 3); `TranscriptSegment` from `tools.video_marks` (Task 1).
- Produces:
  - `parse_vtt(text: str, lang: str) -> list[TranscriptSegment]`
  - `fetch_transcript(meta: VideoMeta, *, run: RunProc = _subprocess_run, get: HttpPost | None = None, groq_key: str | None = None, work_dir: Path = Path(".cache/video")) -> list[TranscriptSegment] | Unavailable`
  - `split_audio(audio: Path, duration_s: float, *, run: RunProc, max_bytes: int = GROQ_MAX_BYTES) -> list[tuple[Path, float]]` — chunk path plus its time offset.
  - Constant `GROQ_MAX_BYTES = 24 * 1024 * 1024`.
  - `HttpPost` protocol: `__call__(url: str, *, headers: dict[str, str], files: dict[str, object], data: dict[str, str]) -> HttpResponse`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_video_fetch.py`:

```python
from tools.video_fetch import fetch_transcript, parse_vtt
from tools.video_marks import TranscriptSegment

VTT = """WEBVTT

00:00:04.000 --> 00:00:07.000
大盘很安静

00:00:10.500 --> 00:00:13.000
我在这里做多
"""


def test_parse_vtt_maps_cues_to_segments() -> None:
    segments = parse_vtt(VTT, lang="zh")
    assert segments == [
        TranscriptSegment(ts_s=4.0, text="大盘很安静", lang="zh"),
        TranscriptSegment(ts_s=10.5, text="我在这里做多", lang="zh"),
    ]


def test_parse_vtt_ignores_header_and_blank_lines() -> None:
    assert parse_vtt("WEBVTT\n\n\n", lang="en") == []


def _meta() -> VideoMeta:
    return VideoMeta(
        source="youtube",
        video_id="dQw4w9WgXcQ",
        author="@cryptoTrader",
        title="BTC weekly outlook",
        publish_ts_utc="2026-07-28T14:00:00+00:00",
        duration_s=2280.0,
        lang="zh",
        url=YT_URL,
    )


def test_fetch_transcript_prefers_captions(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def _run(cmd: list[str]) -> FakeProc:
        captured.append(cmd)
        (tmp_path / "sub.zh.vtt").write_text(VTT)
        return FakeProc(0, "")

    segments = fetch_transcript(_meta(), run=_run, work_dir=tmp_path)
    assert isinstance(segments, list)
    assert segments[1].text == "我在这里做多"
    assert not any("whisper" in " ".join(c) for c in captured)


def test_fetch_transcript_unavailable_without_captions_or_key(tmp_path: Path) -> None:
    got = fetch_transcript(_meta(), run=make_run(FakeProc(0, "")), work_dir=tmp_path)
    assert isinstance(got, Unavailable)
    assert "no captions" in got.reason.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_video_fetch.py -v -k transcript or vtt`
Expected: FAIL — `ImportError: cannot import name 'fetch_transcript'`

- [ ] **Step 3: Write the implementation**

Append to `tools/video_fetch.py`:

```python
_VTT_CUE_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}"
)
_GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Fed to ASR so crypto jargon is not mangled at the source. Cheapest of the three
# transcript-quality layers; the other two live in the vision pass.
_ASR_VOCAB = (
    "FVG, OTE, BOS, CHoCH, liquidity sweep, order block, equal highs, equal lows, "
    "funding rate, open interest, BTC, ETH, SOL, XRP, DOGE, BNB, perp, longs, shorts"
)


class HttpResponse(Protocol):
    status_code: int
    text: str


class HttpPost(Protocol):
    def __call__(
        self,
        url: str,
        *,
        headers: dict[str, str],
        files: dict[str, object],
        data: dict[str, str],
    ) -> HttpResponse: ...


def parse_vtt(text: str, lang: str) -> list[TranscriptSegment]:
    """WebVTT cues → segments. Cue start time is the segment timestamp."""
    segments: list[TranscriptSegment] = []
    pending_ts: float | None = None
    for line in text.splitlines():
        stripped = line.strip()
        match = _VTT_CUE_RE.search(stripped)
        if match:
            hours, minutes, seconds, millis = (int(g) for g in match.groups())
            pending_ts = hours * 3600 + minutes * 60 + seconds + millis / 1000
            continue
        if pending_ts is None or not stripped or stripped == "WEBVTT":
            continue
        segments.append(TranscriptSegment(ts_s=pending_ts, text=stripped, lang=lang))
        pending_ts = None
    return segments


def fetch_transcript(
    meta: VideoMeta,
    *,
    run: RunProc = _subprocess_run,
    get: HttpPost | None = None,
    groq_key: str | None = None,
    work_dir: Path = Path(".cache/video"),
) -> list[TranscriptSegment] | Unavailable:
    """Existing captions in any language first; Groq whisper-large-v3 only when absent."""
    work_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            "yt-dlp",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-format",
            "vtt",
            "--sub-langs",
            "all",
            "-o",
            str(work_dir / "sub"),
            meta.url,
        ]
    )
    vtts = sorted(work_dir.glob("sub*.vtt"))
    if vtts:
        return parse_vtt(vtts[0].read_text(encoding="utf-8"), lang=meta.lang or "en")
    if groq_key is None or get is None:
        return Unavailable("no captions available and no GROQ_API_KEY configured")
    return _transcribe_groq(
        meta, run=run, get=get, groq_key=groq_key, work_dir=work_dir
    )


def _transcribe_groq(
    meta: VideoMeta,
    *,
    run: RunProc,
    get: HttpPost,
    groq_key: str,
    work_dir: Path,
) -> list[TranscriptSegment] | Unavailable:
    audio = work_dir / f"{meta.video_id}.opus"
    proc = run(
        [
            "yt-dlp",
            "-f",
            "bestaudio",
            "-x",
            "--audio-format",
            "opus",
            "--audio-quality",
            "6",
            "-o",
            str(audio),
            meta.url,
        ]
    )
    if proc.returncode != 0 or not audio.exists():
        return Unavailable(proc.stderr.strip() or "audio extraction failed")
    with audio.open("rb") as handle:
        resp = get(
            _GROQ_URL,
            headers={"Authorization": f"Bearer {groq_key}"},
            files={"file": handle},
            data={
                "model": "whisper-large-v3",
                "response_format": "verbose_json",
                "prompt": _ASR_VOCAB,
            },
        )
    if resp.status_code != 200:
        return Unavailable(f"Groq HTTP {resp.status_code}")
    try:
        payload = json.loads(resp.text)
    except json.JSONDecodeError:
        return Unavailable("Groq returned non-JSON output")
    lang = str(payload.get("language") or meta.lang or "en")
    return [
        TranscriptSegment(
            ts_s=float(s["start"]), text=str(s["text"]).strip(), lang=lang
        )
        for s in payload.get("segments", [])
        if str(s.get("text", "")).strip()
    ]
```

Add to the imports at the top of `tools/video_fetch.py`:

```python
from pathlib import Path

from tools.video_marks import TranscriptSegment
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_video_fetch.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Write the failing chunking test**

Groq rejects uploads over 25 MB. Low-bitrate opus keeps roughly three hours under that, but the
operator's backlog contains longer streams, so oversized audio is split rather than dropped.
Append to `tests/test_video_fetch.py`:

```python
from tools.video_fetch import GROQ_MAX_BYTES, split_audio


def test_split_audio_returns_single_chunk_when_small(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * 1024)
    chunks = split_audio(audio, 600.0, run=make_run(FakeProc(0)))
    assert chunks == [(audio, 0.0)]


def test_split_audio_splits_oversized_and_carries_offsets(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * (GROQ_MAX_BYTES * 2 + 1))
    calls: list[list[str]] = []

    def _run(cmd: list[str]) -> FakeProc:
        calls.append(cmd)
        return FakeProc(0)

    chunks = split_audio(audio, 900.0, run=_run)
    assert len(chunks) == 3
    assert [round(offset, 1) for _, offset in chunks] == [0.0, 300.0, 600.0]
    assert all(c[0] == "ffmpeg" for c in calls)


def test_split_audio_drops_chunks_ffmpeg_failed_on(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * (GROQ_MAX_BYTES * 2 + 1))
    chunks = split_audio(audio, 900.0, run=make_run(FakeProc(1)))
    assert chunks == []
```

- [ ] **Step 6: Run it to verify it fails**

Run: `poetry run pytest tests/test_video_fetch.py -k split_audio -v`
Expected: FAIL — `ImportError: cannot import name 'split_audio'`

- [ ] **Step 7: Implement chunking**

Append to `tools/video_fetch.py`:

```python
GROQ_MAX_BYTES = (
    24 * 1024 * 1024
)  # Groq rejects >25MB; leave headroom for multipart overhead


def split_audio(
    audio: Path,
    duration_s: float,
    *,
    run: RunProc,
    max_bytes: int = GROQ_MAX_BYTES,
) -> list[tuple[Path, float]]:
    """Split oversized audio into (chunk, time_offset) pairs. Offsets restore absolute
    timestamps after per-chunk transcription."""
    size = audio.stat().st_size
    if size <= max_bytes:
        return [(audio, 0.0)]
    parts = -(-size // max_bytes)  # ceil
    span = duration_s / parts
    chunks: list[tuple[Path, float]] = []
    for i in range(parts):
        offset = i * span
        out = audio.with_name(f"{audio.stem}_{i:02d}{audio.suffix}")
        proc = run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(offset),
                "-t",
                str(span),
                "-i",
                str(audio),
                "-c",
                "copy",
                str(out),
            ]
        )
        if proc.returncode == 0:
            chunks.append((out, offset))
    return chunks
```

Then change `_transcribe_groq` to transcribe each chunk and shift its timestamps, replacing the
single-file upload block with:

```python
    segments: list[TranscriptSegment] = []
    chunks = split_audio(audio, meta.duration_s, run=run)
    if not chunks:
        return Unavailable("audio chunking failed")
    for chunk, offset in chunks:
        with chunk.open("rb") as handle:
            resp = get(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {groq_key}"},
                files={"file": handle},
                data={
                    "model": "whisper-large-v3",
                    "response_format": "verbose_json",
                    "prompt": _ASR_VOCAB,
                },
            )
        if resp.status_code != 200:
            return Unavailable(f"Groq HTTP {resp.status_code}")
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError:
            return Unavailable("Groq returned non-JSON output")
        lang = str(payload.get("language") or meta.lang or "en")
        segments.extend(
            TranscriptSegment(
                ts_s=float(s["start"]) + offset,
                text=str(s["text"]).strip(),
                lang=lang,
            )
            for s in payload.get("segments", [])
            if str(s.get("text", "")).strip()
        )
    return segments
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_video_fetch.py -v`
Expected: PASS, 14 tests.

- [ ] **Step 9: Lint and typecheck**

Run: `make lint-py && make typecheck`
Expected: both clean.

- [ ] **Step 10: Commit**

```bash
git add tools/video_fetch.py tests/test_video_fetch.py
git commit -m "feat(ingest-video): captions-first transcript with chunked Groq fallback"
```

---

### Task 5: Frame extraction, dedup cache, batch, CLI

**Files:**

- Modify: `tools/video_fetch.py` (append)
- Modify: `tests/test_video_fetch.py` (append)

**Interfaces:**

- Consumes: everything from Tasks 1, 3, 4; `FrameMark` and `select` from `tools.video_marks`.
- Produces:
  - `extract_frames(meta: VideoMeta, marks: list[FrameMark], dest_dir: Path, *, run: RunProc = _subprocess_run) -> list[str]`
  - `BatchResult(url: str, meta: VideoMeta | Unavailable, segments: list[TranscriptSegment], frame_paths: list[str], cached: bool)`
  - `fetch_video_batch(urls: list[str], *, cache_dir: Path = Path(".cache/video"), min_delay: float = 4.0, max_delay: float = 12.0, force: bool = False, run: RunProc = _subprocess_run, get: HttpPost | None = None, groq_key: str | None = None, sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None) -> list[BatchResult]`
  - `main(argv: list[str] | None = None) -> int` — CLI with `--json`, `--batch`, `--force`, `--min-delay`, `--max-delay`, `--cache-dir`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_video_fetch.py`:

```python
import random

from tools.video_fetch import BatchResult, extract_frames, fetch_video_batch
from tools.video_marks import FrameMark


def test_extract_frames_one_ffmpeg_call_per_mark(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def _run(cmd: list[str]) -> FakeProc:
        calls.append(cmd)
        return FakeProc(0)

    marks = [FrameMark(20.0, "item", 3), FrameMark(90.0, "deixis", 2)]
    paths = extract_frames(_meta(), marks, tmp_path, run=_run)
    assert len(calls) == 2
    assert all(c[0] == "ffmpeg" for c in calls)
    assert [Path(p).name for p in paths] == ["f_0020.jpg", "f_0090.jpg"]


def test_extract_frames_skips_failed_grabs(tmp_path: Path) -> None:
    paths = extract_frames(
        _meta(), [FrameMark(20.0, "item", 3)], tmp_path, run=make_run(FakeProc(1))
    )
    assert paths == []


# fetch_video_batch passes work_dir=cache_dir/<video_id> to fetch_transcript, so a fake
# that writes captions to tmp_path itself would be globbed for in the wrong directory.
# Derive the location from yt-dlp's own -o argument, which is how yt-dlp names sub files.
def make_ytdlp_run(
    calls: list[list[str]] | None = None, fail_substr: str | None = None
) -> Callable[[list[str]], FakeProc]:
    def _run(cmd: list[str]) -> FakeProc:
        if calls is not None:
            calls.append(cmd)
        joined = " ".join(cmd)
        if fail_substr is not None and fail_substr in joined:
            return FakeProc(1, "", "Private video")
        if "--dump-json" in cmd:
            return FakeProc(0, YTDLP_JSON)
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.with_name(f"{out.name}.zh.vtt").write_text(VTT, encoding="utf-8")
        return FakeProc(0)

    return _run


def test_batch_second_network_fetch_sleeps_first_does_not(tmp_path: Path) -> None:
    slept: list[float] = []
    fetch_video_batch(
        [YT_URL, "https://youtu.be/AAAAAAAAAAA"],
        cache_dir=tmp_path,
        run=make_ytdlp_run(),
        sleep=slept.append,
        rng=random.Random(0),
    )
    assert len(slept) == 1


def test_batch_cache_hit_does_no_network_and_no_sleep(tmp_path: Path) -> None:
    slept: list[float] = []
    calls: list[list[str]] = []
    run = make_ytdlp_run(calls)

    first_run = fetch_video_batch(
        [YT_URL], cache_dir=tmp_path, run=run, sleep=slept.append
    )
    assert first_run[0].cached is False
    calls_after_first = len(calls)

    results = fetch_video_batch(
        [YT_URL], cache_dir=tmp_path, run=run, sleep=slept.append
    )
    assert len(calls) == calls_after_first
    assert results[0].cached is True
    assert results[0].segments[1].text == "我在这里做多"
    assert slept == []


def test_batch_isolates_one_bad_video(tmp_path: Path) -> None:
    results = fetch_video_batch(
        ["https://youtu.be/BBBBBBBBBBB", YT_URL],
        cache_dir=tmp_path,
        run=make_ytdlp_run(fail_substr="BBBBBBBBBBB"),
    )
    assert isinstance(results[0].meta, Unavailable)
    assert isinstance(results[1].meta, VideoMeta)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_video_fetch.py -v`
Expected: FAIL — `ImportError: cannot import name 'extract_frames'`

- [ ] **Step 3: Write the implementation**

Append to `tools/video_fetch.py`:

```python
def extract_frames(
    meta: VideoMeta,
    marks: list[FrameMark],
    dest_dir: Path,
    *,
    run: RunProc = _subprocess_run,
) -> list[str]:
    """One ffmpeg seek per mark. Never speculative — marks come from the transcript pass."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for mark in marks:
        out = dest_dir / f"f_{int(mark.ts_s):04d}.jpg"
        proc = run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(mark.ts_s),
                "-i",
                meta.url,
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(out),
            ]
        )
        if proc.returncode == 0:
            paths.append(str(out))
    return paths


@dataclass(frozen=True)
class BatchResult:
    url: str
    meta: VideoMeta | Unavailable
    segments: list[TranscriptSegment] = field(default_factory=list)
    frame_paths: list[str] = field(default_factory=list)
    cached: bool = False


def _cache_file(cache_dir: Path, video_id: str) -> Path:
    return cache_dir / video_id / "asset.json"


def _load_cached(cache_dir: Path, video_id: str) -> BatchResult | None:
    path = _cache_file(cache_dir, video_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return BatchResult(
            url=data["url"],
            meta=VideoMeta(**data["meta"]),
            segments=[TranscriptSegment(**s) for s in data["segments"]],
            frame_paths=list(data.get("frame_paths", [])),
            cached=True,
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None  # corrupt cache ⇒ treat as a miss, re-fetch


def _write_cache(cache_dir: Path, result: BatchResult, meta: VideoMeta) -> None:
    path = _cache_file(cache_dir, meta.video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "url": result.url,
                "meta": asdict(meta),
                "segments": [asdict(s) for s in result.segments],
                "frame_paths": result.frame_paths,
                "fetched_at_utc": datetime.now(UTC).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def fetch_video_batch(
    urls: list[str],
    *,
    cache_dir: Path = Path(".cache/video"),
    min_delay: float = 4.0,
    max_delay: float = 12.0,
    force: bool = False,
    run: RunProc = _subprocess_run,
    get: HttpPost | None = None,
    groq_key: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> list[BatchResult]:
    """Fetch metadata + transcript per video once, with a randomized cooldown between
    *network* fetches and a per-id dedup cache. Cache hits add no pause; the first
    network fetch is never delayed. One failing video never kills the batch."""
    rng = rng or random.Random()
    results: list[BatchResult] = []
    did_network = False
    for url in urls:
        try:
            _, video_id = parse_video_url(url)
        except ValueError as exc:
            results.append(BatchResult(url=url, meta=Unavailable(str(exc))))
            continue
        if not force:
            cached = _load_cached(cache_dir, video_id)
            if cached is not None:
                results.append(cached)
                continue
        if did_network:
            sleep(rng.uniform(min_delay, max_delay))
        did_network = True
        try:
            meta = fetch_meta(url, run=run)
            if isinstance(meta, Unavailable):
                results.append(BatchResult(url=url, meta=meta))
                continue
            segments = fetch_transcript(
                meta,
                run=run,
                get=get,
                groq_key=groq_key,
                work_dir=cache_dir / video_id,
            )
            if isinstance(segments, Unavailable):
                results.append(BatchResult(url=url, meta=segments))
                continue
            result = BatchResult(url=url, meta=meta, segments=segments)
            _write_cache(cache_dir, result, meta)
            results.append(result)
        except OSError as exc:  # one bad video never kills the batch
            results.append(
                BatchResult(url=url, meta=Unavailable(f"{type(exc).__name__}: {exc}"))
            )
    return results


def _result_to_dict(result: BatchResult) -> dict[str, object]:
    base: dict[str, object] = {
        "url": result.url,
        "cached": result.cached,
        "frame_paths": result.frame_paths,
    }
    if isinstance(result.meta, Unavailable):
        return {**base, "meta": None, "segments": [], "unavailable": result.meta.reason}
    return {
        **base,
        "meta": asdict(result.meta),
        "segments": [asdict(s) for s in result.segments],
        "unavailable": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch video metadata + transcript for /ingest-video (read-only)."
    )
    parser.add_argument("urls", nargs="+", help="one or more YouTube / X video URLs")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--batch", action="store_true", help="force batch mode")
    parser.add_argument("--force", action="store_true", help="ignore the dedup cache")
    parser.add_argument("--min-delay", type=float, default=4.0)
    parser.add_argument("--max-delay", type=float, default=12.0)
    parser.add_argument("--cache-dir", default=".cache/video")
    args = parser.parse_args(argv)

    results = fetch_video_batch(
        args.urls,
        cache_dir=Path(args.cache_dir),
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        force=args.force,
        get=_requests_post,
        groq_key=os.environ.get("GROQ_API_KEY"),
    )
    if args.json:
        print(
            json.dumps(
                [_result_to_dict(r) for r in results], indent=2, ensure_ascii=False
            )
        )
    else:
        for r in results:
            if isinstance(r.meta, Unavailable):
                print(f"UNAVAILABLE ({r.meta.reason}): {r.url}")
            else:
                tag = " [cached]" if r.cached else ""
                print(
                    f"{r.meta.author}  {r.meta.title}  {r.meta.publish_ts_utc}  "
                    f"{len(r.segments)} segments{tag}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Extend the imports at the top of `tools/video_fetch.py`:

```python
import argparse
import os
import random
import time
from collections.abc import Callable
from dataclasses import asdict, field

import requests

from tools.video_marks import FrameMark


def _requests_post(
    url: str, *, headers: dict[str, str], files: dict[str, object], data: dict[str, str]
) -> HttpResponse:
    return requests.post(  # type: ignore[return-value]
        url, headers=headers, files=files, data=data, timeout=300
    )
```

- [ ] **Step 4: Run the full suite**

Run: `poetry run pytest tests/test_video_fetch.py tests/test_video_marks.py tests/test_video_calltime.py -v`
Expected: PASS, 21 tests.

- [ ] **Step 5: Lint, typecheck, and confirm nothing else broke**

Run: `make lint-py && make typecheck && make test`
Expected: all green, no regressions.

- [ ] **Step 6: Commit**

```bash
git add tools/video_fetch.py tests/test_video_fetch.py
git commit -m "feat(ingest-video): frame extraction, dedup cache, batch fetch, CLI"
```

---

### Task 6: The skill, and documentation

**Files:**

- Create: `.claude/skills/ingest-video/SKILL.md`
- Modify: `CLAUDE.md` (tools list, Agent Skills table)
- Modify: `README.md` (skills section)

**Interfaces:**

- Consumes: the CLIs from Tasks 2, 3, 4, 5; `tools/x_route.py::route_target` unchanged.
- Produces: the `/ingest-video` skill.

- [ ] **Step 1: Write the skill**

Create `.claude/skills/ingest-video/SKILL.md` with frontmatter `name: ingest-video`,
`allowed-tools: Bash, Read, Write, Edit, Task`, and a description that triggers on
"/ingest-video", a pasted YouTube/X video URL, or "ingest this video".

Body must specify, in order:

1. **Fetch the batch in one call:**
   `PYTHONPATH=. poetry run python tools/video_fetch.py <url…> --batch --json`
   Output is a JSON array per video: `url`, `cached`, `meta`, `segments`, `unavailable`.
   For any `unavailable` element, tell the user and continue the rest of the batch.

2. **Pass 1 — text-only subagent, one per video, `model: "sonnet"`,
   `subagent_type: "general-purpose"`.** Input: the `segments` array and the inline rubric
   (copy the classification rubric from `.claude/skills/ingest-x/SKILL.md` verbatim — it is
   shared, and both are refreshed from `project_todo_master.md` on the same cadence).
   It must NOT read any repo, SoT, or memory file. Returns:

   ```json
   {
     "summary": "one paragraph, English",
     "stated_ts_utc": "ISO-8601 or null",
     "stated_date_only": false,
     "stated_ts_raw": "verbatim quote or empty",
     "candidates": [
       {"ts": 252.0, "content_type": "setup|claim|mechanic", "specificity": 1-5, "gist": "..."}
     ]
   }
   ```

   Rank `candidates` by `specificity` descending; keep the top `ITEM_CAP` (5) as items, and
   report the rest as dropped with a one-line reason.

3. **Resolve the call time deterministically — never in the prompt:**

   ```bash
   PYTHONPATH=. poetry run python tools/video_calltime.py \
     --publish <meta.publish_ts_utc> --stated <stated_ts_utc> [--date-only] \
     --stated-raw "<stated_ts_raw>" --ingested <now>
   ```

   Use the returned `call_ts_utc`, `call_ts_source`, `publish_ts_utc`, `backlog` verbatim.

4. **Select and extract frames.** Feed the kept item timestamps plus the segments to
   `tools.video_marks.select(...)`, then extract via `tools.video_fetch.extract_frames(...)`.
   Cap is `FRAME_CAP` (15).

5. **Pass 2 — vision subagent, `model: "sonnet"`.** Input: the frame paths, the segments, and
   the item schema. It Reads each frame and returns a video-level `chart_present` boolean plus
   one item JSON per kept candidate, matching the `/ingest-x` schema plus `ts`, `frame_path`,
   `confidence`, `raw_quote_en`, and `corrected_from`. Rules it must follow: where a chart
   contradicts the transcript, the chart wins and `corrected_from` records the transcript's
   value; anything not visually corroborated gets `confidence: "low"`; `raw_quote` stays in the
   original language. `chart_present: false` (a pure talking-head video) is recorded on the note
   so the operator learns which channels are worth extracting frames from at all.

6. **ONE consolidated digest for the whole batch.** A table with one row per item: video ·
   author · `call_ts_utc` + `call_ts_source` · `ts` · `content_type` · `verdict` · proposed
   routing · `confidence`. Below it, per video: the summary, the dropped candidates with
   reasons, and the `chart_present` flag. **Write nothing yet.**

7. **Route on a single approval.** Per item, call
   `tools/x_route.py::route_target(content_type, verdict)`; append per the same table
   `/ingest-x` uses. Stream C lines carry the video schema from the spec, including
   `call_ts_source`, `publish_ts_utc`, `stated_ts_raw`, `ingested_ts_utc`, `backlog`, and a
   `&t=<ts>s` deep link for YouTube (plain URL plus `ts` for X).

8. **Write the per-video note** to `docs/plans/video-notes/<date>-<author>-<slug>.md`
   (gitignored via `docs/plans/`): frontmatter, summary, items table with routing outcome,
   dropped candidates, frame references, cleaned bilingual transcript.

Guardrails section must state: never write to a stream before approval; `call_ts_utc` never
comes from the model's own date arithmetic; the detector list is frozen so output is always a
hypothesis to test, never an "add a detector" task; and the inherited sink-grep dedup gap
(`/ingest-x` iteration-2 backlog item #7) means a repeated thesis may duplicate — check the
sink before appending a claim that reads familiar.

- [ ] **Step 2: Verify the skill loads**

Run: `poetry run python -c "import pathlib,sys; p=pathlib.Path('.claude/skills/ingest-video/SKILL.md'); sys.exit(0 if p.read_text().startswith('---') else 1)"`
Expected: exit 0. Then confirm `/ingest-video` appears in the skills list on next session start.

- [ ] **Step 3: Update `CLAUDE.md`**

Add to the `tools/` bullet list, alphabetically near `x_fetch.py`:

- `video_fetch.py` — read-only YouTube/X video fetcher (yt-dlp metadata + captions, Groq
  `whisper-large-v3` fallback for caption-less video, ffmpeg frame extraction at
  transcript-selected timestamps, per-id dedup cache in `.cache/video/<id>/`, batch mode with
  randomized cooldown). `run`/`get`/`sleep`/`rng` injected for network-free tests. Backs
  `/ingest-video`.
- `video_marks.py` — pure frame selection (deixis + spoken-level + item + safety-sample
  triggers, dedup window, cap). Owns `TranscriptSegment`.
- `video_calltime.py` — pure call-time resolution: stated time preferred but bounded
  (`< publish`, `≤ STATED_TS_MAX_LEAD_H`, date-only → conservative end-of-day), else publish
  time. The `/ingest-video` look-ahead guard; kept out of prompt-space deliberately.

Add a row to the Agent Skills table for `/ingest-video`.

- [ ] **Step 4: Update `README.md`**

Mirror the skills-table row.

- [ ] **Step 5: Full gate**

Run: `make lint-py && make typecheck && make test && make lint-md && make test-regression`
Expected: all green; goldens unmoved (nothing here touches the backtest pipeline).

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/ingest-video/SKILL.md CLAUDE.md README.md
git commit -m "feat(ingest-video): skill orchestration + docs"
```

---

## Manual verification (after Task 6)

The suite never touches the network, so one real end-to-end run is required before merge:

1. `export GROQ_API_KEY=…` in `.env`.
2. Run `/ingest-video` on one short English YouTube video with captions. Confirm: frames ≤ 15,
   digest renders, nothing is written before approval.
3. Run it on one Chinese video. Confirm `raw_quote` stays in Chinese, `raw_quote_en` is populated.
4. Run it on one X video with no captions. Confirm the Groq fallback fires.
5. Re-run step 2's URL. Confirm `cached: true` and zero network calls.
6. Inspect one appended `pundit-calls.jsonl` line. **Confirm `call_ts_utc` is not today's date
   unless the video was published today.** This is the check that matters most.
