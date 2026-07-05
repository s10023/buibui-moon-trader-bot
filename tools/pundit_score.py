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

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.backtest.engine import _compute_atr14
from analytics.store import DEFAULT_DB_PATH
from analytics.store.market_data import get_ohlcv

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


def window_ms(horizon: str) -> int:
    """Pre-committed horizon window (spec §Windows); unknown values -> unspecified."""
    return WINDOWS_MS.get(horizon, WINDOWS_MS["unspecified"])


def find_call_candle(df: pd.DataFrame, ts_ms: int) -> int | None:
    """Index of the 1h candle containing ts_ms, or None when outside the data."""
    if df.empty:
        return None
    idx = int(df["open_time"].searchsorted(ts_ms, side="right")) - 1
    if idx < 0 or ts_ms >= int(df["open_time"].iloc[idx]) + HOUR_MS:
        return None
    return idx


def find_fill(
    df: pd.DataFrame, call_idx: int, entry_px: float, is_thesis: bool, limit_ms: int
) -> tuple[int, float] | None:
    """First candle at/after the call that fills the entry (spec §Trigger).

    Thesis entries fill immediately at the call candle close. Level entries fill on
    the first later candle whose range contains the price (direction-agnostic touch:
    covers both pullback and breakout entries). Candles opening after limit_ms never
    fill.
    """
    if is_thesis:
        return call_idx, float(df["close"].iloc[call_idx])
    for i in range(call_idx + 1, len(df)):
        if int(df["open_time"].iloc[i]) > limit_ms:
            return None
        if float(df["low"].iloc[i]) <= entry_px <= float(df["high"].iloc[i]):
            return i, entry_px
    return None


STATE_WIN = "WIN"
STATE_LOSS = "LOSS"
STATE_OPEN = "OPEN"
STATE_NOT_TRIGGERED = "NOT_TRIGGERED"
STATE_UNSCORED = "UNSCORED"
STATE_UNRESOLVABLE = "UNRESOLVABLE"
STATE_STALE = "STALE"
STATE_SKIPPED = "SKIPPED"
RESOLVED_STATES = (STATE_WIN, STATE_LOSS)


@dataclass(frozen=True)
class ScoredCall:
    """One ledger call after resolution against OHLCV."""

    call: LedgerCall
    levels: ResolvedLevels | None
    family: str
    state: str
    fill_ts_ms: int | None = None
    fill_px: float | None = None
    exit_ts_ms: int | None = None
    exit_px: float | None = None
    r: float | None = None
    atr_r: float | None = None
    win: bool | None = None
    note: str = ""


def atr14_before(df: pd.DataFrame, ts_ms: int, tf_ms: int) -> float | None:
    """ATR14 over the candles fully closed by ts_ms (engine TR-mean convention)."""
    if df.empty:
        return None
    closed = int((df["open_time"] + tf_ms <= ts_ms).sum())
    idx = closed - 1
    if idx < 1:
        return None
    return _compute_atr14(
        df["high"].to_numpy(dtype=np.float64),
        df["low"].to_numpy(dtype=np.float64),
        df["close"].to_numpy(dtype=np.float64),
        idx,
    )


def score_call(
    call: LedgerCall,
    override: Override | None,
    df_1h: pd.DataFrame,
    df_1d: pd.DataFrame,
    as_of_ms: int,
) -> ScoredCall:
    """Resolve one call: trigger -> walk -> state + R (spec §Scoring semantics)."""
    family = (
        override.family
        if override is not None and override.family
        else tag_family(f"{call.raw_quote} {call.entry}")
    )
    if override is not None and override.skip:
        return ScoredCall(call, None, family, STATE_SKIPPED, note=override.note)
    if call.direction == "neutral":
        return ScoredCall(call, None, family, STATE_UNSCORED, note="neutral direction")
    if df_1h.empty:
        return ScoredCall(call, None, family, STATE_UNRESOLVABLE, note="no OHLCV")
    call_idx = find_call_candle(df_1h, call.call_ts_ms)
    if call_idx is None:
        return ScoredCall(
            call, None, family, STATE_UNRESOLVABLE, note="call candle missing"
        )

    ref_close = float(df_1h["close"].iloc[call_idx])
    levels = resolve_levels(call, override, ref_close)
    win_ms = window_ms(call.horizon)
    data_end_ms = int(df_1h["open_time"].iloc[-1]) + HOUR_MS
    trigger_deadline = call.call_ts_ms + win_ms

    fill = find_fill(
        df_1h,
        call_idx,
        levels.entry_px,
        levels.entry_is_thesis,
        min(trigger_deadline, as_of_ms),
    )
    if fill is None:
        if trigger_deadline <= as_of_ms and data_end_ms >= trigger_deadline:
            return ScoredCall(call, levels, family, STATE_NOT_TRIGGERED)
        if data_end_ms < min(trigger_deadline, as_of_ms):
            return ScoredCall(
                call,
                levels,
                family,
                STATE_STALE,
                note="OHLCV ends in trigger window — sync first",
            )
        return ScoredCall(call, levels, family, STATE_OPEN, note="awaiting trigger")

    fill_idx, fill_px = fill
    fill_ts = int(df_1h["open_time"].iloc[fill_idx])
    expiry_ms = fill_ts + win_ms
    dirsign = 1.0 if call.direction == "long" else -1.0
    risk = abs(fill_px - levels.stop_px) if levels.stop_px is not None else None
    # Thesis entries fill AT the close -> exits start next bar; level entries can be
    # stopped/targeted on the fill bar itself (adverse-first, conservative).
    start_idx = fill_idx + 1 if levels.entry_is_thesis else fill_idx
    scan_limit = min(expiry_ms, as_of_ms)

    state = ""
    exit_px: float | None = None
    exit_ts: int | None = None
    for i in range(start_idx, len(df_1h)):
        ot = int(df_1h["open_time"].iloc[i])
        if ot > scan_limit:
            break
        lo = float(df_1h["low"].iloc[i])
        hi = float(df_1h["high"].iloc[i])
        if levels.stop_px is not None and lo <= levels.stop_px <= hi:
            state, exit_px, exit_ts = STATE_LOSS, levels.stop_px, ot
            break
        if levels.target_px is not None and lo <= levels.target_px <= hi:
            state, exit_px, exit_ts = STATE_WIN, levels.target_px, ot
            break

    if not state:
        if expiry_ms <= as_of_ms and data_end_ms >= expiry_ms:
            exp_idx = int(df_1h["open_time"].searchsorted(expiry_ms, side="right")) - 1
            exit_px = float(df_1h["close"].iloc[exp_idx])
            exit_ts = int(df_1h["open_time"].iloc[exp_idx])
            # Expiry classified by sign; exactly flat counts as LOSS (conservative).
            state = STATE_WIN if dirsign * (exit_px - fill_px) > 0 else STATE_LOSS
        elif data_end_ms < min(expiry_ms, as_of_ms):
            return ScoredCall(
                call,
                levels,
                family,
                STATE_STALE,
                fill_ts_ms=fill_ts,
                fill_px=fill_px,
                note="OHLCV ends mid-window — sync first",
            )
        else:
            return ScoredCall(
                call,
                levels,
                family,
                STATE_OPEN,
                fill_ts_ms=fill_ts,
                fill_px=fill_px,
                note="in position",
            )

    assert exit_px is not None and exit_ts is not None
    r: float | None = None
    if risk is not None and risk > 0:
        if state == STATE_LOSS and exit_px == levels.stop_px:
            r = -1.0
        elif (
            state == STATE_WIN
            and levels.target_px is not None
            and exit_px == levels.target_px
        ):
            r = abs(levels.target_px - fill_px) / risk
        else:  # expiry exit with a known stop
            r = dirsign * (exit_px - fill_px) / risk
    tf_ms = HOUR_MS if call.horizon == "intraday" else DAY_MS
    atr = atr14_before(df_1h if call.horizon == "intraday" else df_1d, fill_ts, tf_ms)
    atr_r = dirsign * (exit_px - fill_px) / atr if atr else None
    return ScoredCall(
        call,
        levels,
        family,
        state,
        fill_ts_ms=fill_ts,
        fill_px=fill_px,
        exit_ts_ms=exit_ts,
        exit_px=exit_px,
        r=r,
        atr_r=atr_r,
        win=state == STATE_WIN,
    )


@dataclass
class CellStats:
    """Roll-up counters for one report cell (author, or family x direction)."""

    n: int = 0
    triggered: int = 0
    open_: int = 0
    resolved: int = 0
    wins: int = 0
    r_sum: float = 0.0
    r_n: int = 0
    atr_r_sum: float = 0.0
    atr_r_n: int = 0

    def add(self, sc: ScoredCall) -> None:
        self.n += 1
        if sc.fill_ts_ms is not None:
            self.triggered += 1
        if sc.state == STATE_OPEN:
            self.open_ += 1
        if sc.state in RESOLVED_STATES:
            self.resolved += 1
            if sc.win:
                self.wins += 1
            if sc.r is not None:
                self.r_sum += sc.r
                self.r_n += 1
            if sc.atr_r is not None:
                self.atr_r_sum += sc.atr_r
                self.atr_r_n += 1

    @property
    def hit_rate(self) -> float | None:
        return self.wins / self.resolved if self.resolved else None

    @property
    def avg_r(self) -> float | None:
        return self.r_sum / self.r_n if self.r_n else None

    @property
    def avg_atr_r(self) -> float | None:
        return self.atr_r_sum / self.atr_r_n if self.atr_r_n else None


def aggregate(
    scored: list[ScoredCall], key_fn: Callable[[ScoredCall], str]
) -> dict[str, CellStats]:
    cells: dict[str, CellStats] = {}
    for sc in scored:
        cells.setdefault(key_fn(sc), CellStats()).add(sc)
    return cells


def _fmt(x: float | None, nd: int = 2) -> str:
    return f"{x:.{nd}f}" if x is not None else "—"


def _fmt_ts(ts_ms: int | None) -> str:
    if ts_ms is None:
        return "—"
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def _cell_table(cells: dict[str, CellStats], label: str, min_n: int) -> list[str]:
    lines = [
        f"| {label} | n | trig | open | resolved | wins | losses "
        "| hit% | avg R | avg ATR-R | |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key in sorted(cells):
        c = cells[key]
        hit = f"{100 * c.hit_rate:.0f}%" if c.hit_rate is not None else "—"
        mark = f"⚠ n<{min_n}" if c.n < min_n else ""
        lines.append(
            f"| {key} | {c.n} | {c.triggered} | {c.open_} | {c.resolved} "
            f"| {c.wins} | {c.resolved - c.wins} "
            f"| {hit} | {_fmt(c.avg_r)} | {_fmt(c.avg_atr_r)} | {mark} |"
        )
    return lines


def render_report(
    scored: list[ScoredCall], warnings: list[str], as_of_iso: str, min_n: int
) -> str:
    """Full markdown report: roll-ups + per-call audit trail (spec §Outputs)."""
    lines = [
        "# Pundit-ledger scorecard",
        "",
        f"- as-of: {as_of_iso} · calls: {len(scored)} · ledger warnings: {len(warnings)}",
        "- Descriptive priors only — NO verdicts; cells below min-n are markers, not gates.",
        "",
    ]
    for w in warnings:
        lines.append(f"- WARNING: {w}")
    if warnings:
        lines.append("")
    lines += ["## Per author", ""]
    lines += _cell_table(aggregate(scored, lambda sc: sc.call.author), "author", min_n)
    lines += ["", "## Per setup-family × direction", ""]
    lines += _cell_table(
        aggregate(scored, lambda sc: f"{sc.family}/{sc.call.direction}"),
        "family/direction",
        min_n,
    )
    lines += ["", "## Audit trail", ""]
    lines += [
        "| author | symbol | dir | call ts (UTC) | entry | stop | target | conf | family | state | R | ATR-R | note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for sc in scored:
        lv = sc.levels
        lines.append(
            f"| {sc.call.author} | {sc.call.symbol} | {sc.call.direction} "
            f"| {_fmt_ts(sc.call.call_ts_ms)} "
            f"| {_fmt(lv.entry_px) if lv else '—'}{' (thesis)' if lv and lv.entry_is_thesis else ''} "
            f"| {_fmt(lv.stop_px) if lv else '—'} | {_fmt(lv.target_px) if lv else '—'} "
            f"| {lv.parse_confidence if lv else '—'} | {sc.family} | {sc.state} "
            f"| {_fmt(sc.r)} | {_fmt(sc.atr_r)} | {sc.note} |"
        )
    return "\n".join(lines) + "\n"


def _cell_dict(c: CellStats) -> dict[str, object]:
    return {
        "n": c.n,
        "triggered": c.triggered,
        "open": c.open_,
        "resolved": c.resolved,
        "wins": c.wins,
        "hit_rate": c.hit_rate,
        "avg_r": c.avg_r,
        "avg_atr_r": c.avg_atr_r,
    }


def build_priors(
    scored: list[ScoredCall], as_of_iso: str, generated_at_iso: str, min_n: int
) -> dict[str, object]:
    """Machine-readable priors (spec §Outputs) — the daily-brief / trade-card hook."""
    by_author = aggregate(scored, lambda sc: sc.call.author)
    by_family = aggregate(scored, lambda sc: f"{sc.family}/{sc.call.direction}")
    authors: dict[str, object] = {}
    for author in sorted(by_author):
        fam_counts: dict[str, dict[str, int]] = {}
        for sc in scored:
            if sc.call.author == author:
                fam_counts.setdefault(sc.family, {"n": 0})["n"] += 1
        authors[author] = _cell_dict(by_author[author]) | {"families": fam_counts}
    # Nested {family: {direction: stats}} — the binding shape for the downstream
    # daily-brief consumer (analytics/brief/pundit.py::build_board on
    # feat/market-brief), NOT a flat "family/direction" key (spec §Outputs).
    families: dict[str, dict[str, object]] = {}
    for fam_key in sorted(by_family):  # fam_key == "family/direction"
        family, _, direction = fam_key.rpartition("/")
        families.setdefault(family, {})[direction] = _cell_dict(by_family[fam_key])
    return {
        "generated_at": generated_at_iso,
        "as_of": as_of_iso,
        "policy": {
            "windows": {"intraday": "48h", "swing": "30d", "unspecified": "14d"},
            "atr": "atr14 1h intraday / 1d swing",
            "min_n_marker": min_n,
        },
        "authors": authors,
        "families": families,
    }


def load_ohlcv_for_calls(
    conn: duckdb.DuckDBPyConnection, calls: list[LedgerCall], as_of_ms: int
) -> dict[tuple[str, str], pd.DataFrame]:
    """1h + 1d frames per symbol, buffered back far enough for ATR14 warm-up."""
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for symbol in sorted({c.symbol for c in calls}):
        start = min(c.call_ts_ms for c in calls if c.symbol == symbol)
        for tf, buffer_ms in (("1h", 20 * HOUR_MS), ("1d", 20 * DAY_MS)):
            df = get_ohlcv(conn, symbol, tf, start - buffer_ms, as_of_ms)
            out[(symbol, tf)] = df.sort_values("open_time").reset_index(drop=True)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", type=Path, default=Path("docs/plans/pundit-calls.jsonl"))
    p.add_argument(
        "--overrides", type=Path, default=Path("docs/plans/pundit-overrides.jsonl")
    )
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument("--as-of", dest="as_of", default=None, help="ISO UTC; default: now")
    p.add_argument("--json", type=Path, default=Path("docs/plans/pundit-priors.json"))
    p.add_argument("--min-n", dest="min_n", type=int, default=5)
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.as_of is not None:
        as_of_dt = datetime.fromisoformat(str(args.as_of).replace("Z", "+00:00"))
    else:
        as_of_dt = datetime.now(tz=UTC)
    as_of_ms = int(as_of_dt.timestamp() * 1000)
    as_of_iso = as_of_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    calls, warnings = load_ledger(args.ledger)
    overrides = load_overrides(args.overrides)
    with duckdb.connect(str(args.db), read_only=True) as conn:
        data = load_ohlcv_for_calls(conn, calls, as_of_ms)
    scored = [
        score_call(
            c,
            overrides.get(c.url),
            data.get((c.symbol, "1h"), pd.DataFrame()),
            data.get((c.symbol, "1d"), pd.DataFrame()),
            as_of_ms,
        )
        for c in calls
    ]
    print(render_report(scored, warnings, as_of_iso, args.min_n))
    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    priors = build_priors(scored, as_of_iso, generated_at, args.min_n)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(priors, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"priors written: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
