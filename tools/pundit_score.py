"""Score the pundit-call ledger against stored OHLCV — measured priors per author/family.

Read-only sibling of ``tools/journal_fetch.py`` / the audit drivers: resolves every
``docs/plans/pundit-calls.jsonl`` call (free-text levels) against 1h/1d OHLCV and
reports hit-rate + R proxies per author x setup-family x direction, plus a
machine-readable ``docs/plans/pundit-priors.json`` sidecar. Descriptive priors only —
no ENABLE/BUILD verdicts (audit_guard gates come later, only if a cell earns n>=30).
Never writes to the DB; no schema change.

Spec: docs/superpowers/specs/2026-07-04-pundit-ledger-scorer-design.md

Usage::

    PYTHONPATH=. poetry run python tools/pundit_score.py \
        [--ledger docs/plans/pundit-calls.jsonl] \
        [--overrides docs/plans/pundit-overrides.jsonl] \
        [--db analytics.db] [--as-of 2026-07-04T00:00:00Z] \
        [--json docs/plans/pundit-priors.json] [--min-n 5]
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
WINDOWS_MS: dict[str, int] = {
    "intraday": 48 * HOUR_MS,
    "swing": 30 * DAY_MS,
    "unspecified": 14 * DAY_MS,
}
SANITY_LO = 0.2
SANITY_HI = 5.0

_UNSPECIFIED_MARKERS = {"", "unspecified", "none", "n/a", "not specified"}
_ZONE_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?\s*(?:-|–|\bto\b)\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?"
)
_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?")


def _expand(num_text: str, k_suffix: str | None) -> float:
    """'57,900' -> 57900.0; '60.5' + 'k' -> 60500.0."""
    return float(num_text.replace(",", "")) * (1000.0 if k_suffix else 1.0)


@dataclass(frozen=True)
class ParsedField:
    """Raw level candidates extracted from one free-text field."""

    zones: tuple[tuple[float, float], ...]
    numbers: tuple[float, ...]
    unspecified: bool


def parse_level_field(text: str | None) -> ParsedField:
    """Extract zone and single-number candidates from a ledger level field."""
    if text is None or str(text).strip().lower() in _UNSPECIFIED_MARKERS:
        return ParsedField(zones=(), numbers=(), unspecified=True)
    cleaned = str(text).replace("$", "").replace("~", "")
    zones: list[tuple[float, float]] = []
    for zm in _ZONE_RE.finditer(cleaned):
        a = _expand(zm.group(1), zm.group(2))
        b = _expand(zm.group(3), zm.group(4))
        zones.append((min(a, b), max(a, b)))
    numbers = tuple(
        _expand(nm.group(1), nm.group(2)) for nm in _NUM_RE.finditer(cleaned)
    )
    return ParsedField(zones=tuple(zones), numbers=numbers, unspecified=False)


@dataclass(frozen=True)
class LedgerCall:
    """One line of docs/plans/pundit-calls.jsonl."""

    line_no: int
    source: str
    author: str
    url: str
    call_ts_utc: str
    symbol: str
    direction: str
    entry: str
    stop: str
    target: str
    horizon: str
    confidence: str
    raw_quote: str
    entry_px: float | None = None
    stop_px: float | None = None
    target_px: float | None = None

    @property
    def call_ts_ms(self) -> int:
        dt = datetime.fromisoformat(self.call_ts_utc.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)


@dataclass(frozen=True)
class Override:
    """One line of docs/plans/pundit-overrides.jsonl — wins over parsed values."""

    url: str
    entry_px: float | None = None
    stop_px: float | None = None
    target_px: float | None = None
    family: str | None = None
    skip: bool = False
    note: str = ""


def _opt_float(obj: dict[str, object], key: str) -> float | None:
    val = obj.get(key)
    return float(val) if isinstance(val, (int, float)) else None


def load_ledger(path: Path) -> tuple[list[LedgerCall], list[str]]:
    """Parse the ledger JSONL; malformed lines become warnings, never crashes."""
    calls: list[LedgerCall] = []
    warnings: list[str] = []
    for line_no, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                warnings.append(f"ledger line {line_no}: skipped (not a JSON object)")
                continue
            calls.append(
                LedgerCall(
                    line_no=line_no,
                    source=str(obj.get("source", "")),
                    author=str(obj.get("author", "")),
                    url=str(obj.get("url", "")),
                    call_ts_utc=str(obj["call_ts_utc"]),
                    symbol=str(obj["symbol"]),
                    direction=str(obj.get("direction", "")).lower(),
                    entry=str(obj.get("entry", "") or ""),
                    stop=str(obj.get("stop", "") or ""),
                    target=str(obj.get("target", "") or ""),
                    horizon=str(obj.get("horizon", "unspecified") or "unspecified"),
                    confidence=str(obj.get("confidence", "") or ""),
                    raw_quote=str(obj.get("raw_quote", "") or ""),
                    entry_px=_opt_float(obj, "entry_px"),
                    stop_px=_opt_float(obj, "stop_px"),
                    target_px=_opt_float(obj, "target_px"),
                )
            )
        except (ValueError, KeyError) as exc:
            warnings.append(f"ledger line {line_no}: skipped ({exc})")
    return calls, warnings


@dataclass(frozen=True)
class ResolvedLevels:
    """Numeric levels for one call after parsing, overrides, and fallbacks."""

    entry_px: float
    entry_is_thesis: bool
    stop_px: float | None
    target_px: float | None
    parse_confidence: str  # ok | low | override | fallback


def select_level(
    parsed: ParsedField, ref_close: float, role: str, direction: str
) -> tuple[float | None, bool]:
    """Pick a price from parsed candidates. Returns (price | None, low_confidence).

    Zone first (both edges must pass the sanity gate): entry -> mid, stop -> far
    edge, target -> near edge. Else the first single number passing the gate;
    multiple distinct sane numbers flag low confidence. Candidates present but all
    rejected also flag low confidence.
    """

    def sane(x: float) -> bool:
        return SANITY_LO * ref_close <= x <= SANITY_HI * ref_close

    for lo, hi in parsed.zones:
        if sane(lo) and sane(hi):
            if role == "entry":
                return (lo + hi) / 2.0, False
            # stop: far edge (long stops sit below -> lo; short stops above -> hi)
            # target: near edge (long targets above -> lo is nearest; short -> hi)
            return (lo if direction == "long" else hi), False
    sane_nums = [x for x in parsed.numbers if sane(x)]
    if sane_nums:
        return sane_nums[0], len(set(sane_nums)) > 1
    return None, bool(parsed.numbers or parsed.zones)


def resolve_levels(
    call: LedgerCall, override: Override | None, ref_close: float
) -> ResolvedLevels:
    """Resolve entry/stop/target with precedence override > ledger px > text > fallback."""
    used_override = False
    low_flag = False

    def pick(
        ov_px: float | None, ledger_px: float | None, text: str, role: str
    ) -> float | None:
        nonlocal used_override, low_flag
        if ov_px is not None:
            used_override = True
            return ov_px
        if ledger_px is not None:
            return ledger_px
        px, low = select_level(parse_level_field(text), ref_close, role, call.direction)
        low_flag = low_flag or low
        return px

    ov = override
    entry = pick(ov.entry_px if ov else None, call.entry_px, call.entry, "entry")
    stop = pick(ov.stop_px if ov else None, call.stop_px, call.stop, "stop")
    target = pick(ov.target_px if ov else None, call.target_px, call.target, "target")

    entry_is_thesis = entry is None
    if entry is None:
        entry = ref_close
    if used_override:
        confidence = "override"
    elif entry_is_thesis:
        confidence = "fallback"
    elif low_flag:
        confidence = "low"
    else:
        confidence = "ok"
    return ResolvedLevels(
        entry_px=entry,
        entry_is_thesis=entry_is_thesis,
        stop_px=stop,
        target_px=target,
        parse_confidence=confidence,
    )


FAMILY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "sweep_reclaim",
        (
            "sweep",
            "reclaim",
            "sfp",
            "stop hunt",
            "stop-hunt",
            "deviation below",
            "deviation above",
        ),
    ),
    (
        "vp_level",
        ("poc", "vah", "val ", "value area", "value-area", "volume profile", "vwap"),
    ),
    (
        "ref_level",
        (
            "pdl",
            "pdh",
            "pwh",
            "pwl",
            "pdval",
            "range low",
            "range high",
            "range-low",
            "range-high",
            "weekly open",
            "daily open",
            "monday",
        ),
    ),
    (
        "ema_trend",
        (
            "ema",
            "moving average",
            "50w",
            "200d",
            "trendline",
            "diagonal",
            "downtrend",
            "uptrend",
            "higher low",
            "lower high",
        ),
    ),
    (
        "flow",
        (
            "cvd",
            "open interest",
            " oi ",
            "absorption",
            "delta",
            "spot bid",
            "spot flow",
            "orderflow",
            "funding",
        ),
    ),
    (
        "accumulation_zone",
        ("accumulation", "dca", "demand zone", "spot-demand", "supply zone", "demand"),
    ),
    (
        "breakout_deviation",
        (
            "breakout",
            "break of",
            "break above",
            "break below",
            "acceptance",
            "deviation",
        ),
    ),
)


def _keyword_hit(t: str, keyword: str) -> bool:
    """Symmetric word-boundary substring match.

    ``keyword`` is stripped of its own leading/trailing spaces before matching
    (multi-word phrases keep their internal spaces, which match literally); a
    hit is accepted only when the character immediately before the match AND
    the character immediately after it are both non-letters (start/end of
    string count as non-letters). A digit on either side does NOT block a
    match, so ``"50ema"``/``"1W 50EMA"`` still hit ``"ema"``. This keeps bare
    short keywords from false-matching mid-word — ``"remains"`` (r-EMA-ins)
    and ``"demand"`` (d-EMA-nd) both contain ``"ema"`` but must not tag
    ``ema_trend`` — while a space-wrapped keyword like ``" oi "`` still hits
    inside ``"reported oi levels are climbing"`` (a left-boundary-only check
    on the raw, un-stripped ``" oi "`` match anchors on the leading space
    itself, whose *preceding* character is the last letter of "reported" —
    wrongly rejecting the hit) and ``"val "`` does not hit inside ``"value"``
    (the trailing ``"u"`` fails the right-boundary check). All occurrences
    are checked, not just the first, since an earlier occurrence can fail a
    boundary test while a later one passes.
    """
    kw = keyword.strip(" ")
    start = 0
    while True:
        idx = t.find(kw, start)
        if idx == -1:
            return False
        left_ok = idx == 0 or not t[idx - 1].isalpha()
        end = idx + len(kw)
        right_ok = end == len(t) or not t[end].isalpha()
        if left_ok and right_ok:
            return True
        start = idx + 1


def tag_family(text: str) -> str:
    """First-match keyword family — a grouping key, not a model (spec §Family)."""
    t = f" {text.lower()} "
    for family, keywords in FAMILY_KEYWORDS:
        if any(_keyword_hit(t, k) for k in keywords):
            return family
    return "other"


def load_overrides(path: Path) -> dict[str, Override]:
    """Parse the overrides sidecar; absent file means no overrides."""
    if not path.exists():
        return {}
    out: dict[str, Override] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        obj = json.loads(raw)
        url = str(obj["url"])
        out[url] = Override(
            url=url,
            entry_px=_opt_float(obj, "entry_px"),
            stop_px=_opt_float(obj, "stop_px"),
            target_px=_opt_float(obj, "target_px"),
            family=str(obj["family"]) if obj.get("family") else None,
            skip=bool(obj.get("skip", False)),
            note=str(obj.get("note", "") or ""),
        )
    return out
