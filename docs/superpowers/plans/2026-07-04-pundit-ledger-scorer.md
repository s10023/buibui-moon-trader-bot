# Pundit-Ledger Scorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `tools/pundit_score.py` — a read-only scorer that resolves every
`docs/plans/pundit-calls.jsonl` call against stored OHLCV and reports hit-rate +
R proxies per author × setup-family × direction, emitting a markdown report and a
`docs/plans/pundit-priors.json` sidecar.

**Architecture:** Single module of pure functions (parse → resolve → score → render)
plus a thin argparse `main()`, mirroring `tools/journal_fetch.py`. Deterministic
free-text level parser + human-editable overrides sidecar (approach C from the spec);
1h-candle walk with pre-committed windows and adverse-first ties; the only I/O is a
read-only DuckDB connection and two gitignored files under `docs/plans/`.

**Tech Stack:** Python 3.11+, pandas, duckdb (read-only), stdlib `re`/`json`/
`argparse`/`dataclasses`. Reuses `analytics.store.market_data.get_ohlcv` and
`analytics.backtest.engine._compute_atr14`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-04-pundit-ledger-scorer-design.md` — the
plan implements it exactly; scoring semantics there are pre-committed.

## Global Constraints

- mypy strict: every function fully annotated (`-> None` for test methods).
- Tests: pytest, no network, no real `analytics.db` (in-memory `duckdb.connect(":memory:")` only).
- Read-only: the tool never writes to the DB; no schema change; goldens must not move.
- Ruff format + lint clean; conventional commits on branch `feat/pundit-score`.
- DoD per task-final commit: `make lint-py && make typecheck && poetry run pytest tests/test_pundit_score.py -q` green; full `make test` + `make test-regression` in the final task.
- Windows (pre-committed, spec §Scoring): intraday = 48h, swing = 30d, unspecified = 14d.
- Sanity gate: parsed level accepted only within `[0.2×, 5×]` of the call-candle close.
- All timestamps are Unix **ms** (matching `ohlcv.open_time` BIGINT).

## File Structure

- Create: `tools/pundit_score.py` — all logic (pure functions + `main()`).
- Create: `tests/test_pundit_score.py` — all tests (one file, grows per task).
- Modify: `Makefile` — add `buibui-pundit-score` target (Task 8).
- Modify: `CLAUDE.md` — add the tools-section bullet (Task 8).

---

### Task 1: Module scaffold + level-field parsing primitives

**Files:**

- Create: `tools/pundit_score.py`
- Create: `tests/test_pundit_score.py`

**Interfaces:**

- Produces: `ParsedField` (frozen dataclass: `zones: tuple[tuple[float, float], ...]`,
  `numbers: tuple[float, ...]`, `unspecified: bool`) and
  `parse_level_field(text: str | None) -> ParsedField`. Constants `HOUR_MS`, `DAY_MS`,
  `WINDOWS_MS`, `SANITY_LO`, `SANITY_HI`. Later tasks import these exact names.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pundit_score.py`:

```python
"""Tests for tools/pundit_score.py — pundit-ledger scorer."""

from __future__ import annotations

from tools.pundit_score import parse_level_field


class TestParseLevelField:
    def test_numeric_with_commas_and_dollar(self) -> None:
        p = parse_level_field("$61,696.80")
        assert p.numbers == (61696.80,)
        assert p.zones == ()
        assert not p.unspecified

    def test_k_suffix_expansion(self) -> None:
        assert parse_level_field("81k").numbers == (81000.0,)
        assert parse_level_field("60.5k intraweek value-area low").numbers == (60500.0,)

    def test_zone_hyphen_endash_to(self) -> None:
        for text in ("57,900-58,200", "57,900–58,200", "57,900 to 58,200"):
            p = parse_level_field(text)
            assert p.zones == ((57900.0, 58200.0),), text

    def test_zone_reversed_bounds_are_sorted(self) -> None:
        assert parse_level_field("60,700-59,500").zones == ((59500.0, 60700.0),)

    def test_unspecified_markers(self) -> None:
        for text in (None, "", "unspecified", "n/a", "None"):
            assert parse_level_field(text).unspecified, repr(text)

    def test_messy_multi_number_keeps_zone_and_singles(self) -> None:
        p = parse_level_field("Sweep range low ~57,900-58,200 then reclaim (price 58,254)")
        assert (57900.0, 58200.0) in p.zones
        assert 58254.0 in p.numbers
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.pundit_score'`.

- [ ] **Step 3: Write the implementation**

Create `tools/pundit_score.py`:

```python
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

import re
from dataclasses import dataclass

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
    numbers = tuple(_expand(nm.group(1), nm.group(2)) for nm in _NUM_RE.finditer(cleaned))
    return ParsedField(zones=tuple(zones), numbers=numbers, unspecified=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add tools/pundit_score.py tests/test_pundit_score.py
git commit -m "feat(tools): pundit_score level-field parsing primitives"
```

---

### Task 2: Ledger + overrides loading

**Files:**

- Modify: `tools/pundit_score.py`
- Modify: `tests/test_pundit_score.py`

**Interfaces:**

- Consumes: nothing from earlier tasks (standalone loaders).
- Produces: `LedgerCall` (frozen dataclass, all 12 ledger fields as `str` + optional
  `entry_px/stop_px/target_px: float | None` + `line_no: int`, property
  `call_ts_ms -> int`), `Override` (frozen dataclass: `url: str`,
  `entry_px/stop_px/target_px: float | None`, `family: str | None`, `skip: bool`,
  `note: str`), `load_ledger(path: Path) -> tuple[list[LedgerCall], list[str]]`
  (calls, warnings), `load_overrides(path: Path) -> dict[str, Override]` (keyed by
  url; missing file -> empty dict).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pundit_score.py` (add `import json`, `from datetime import UTC,
datetime`, `from pathlib import Path` to the imports):

```python
class TestLoaders:
    def _good_line(self) -> dict[str, str]:
        return {
            "source": "x",
            "author": "A",
            "url": "https://x.com/A/status/1",
            "call_ts_utc": "2026-06-20T10:00:00Z",
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry": "58,000",
            "stop": "57,000",
            "target": "60,000",
            "horizon": "swing",
            "confidence": "",
            "raw_quote": "sweep and reclaim",
        }

    def test_load_ledger_parses_and_warns(self, tmp_path: Path) -> None:
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(self._good_line()) + "\n{not json\n", encoding="utf-8")
        calls, warnings = load_ledger(p)
        assert len(calls) == 1
        assert calls[0].author == "A"
        assert calls[0].line_no == 1
        expected_ms = int(datetime(2026, 6, 20, 10, tzinfo=UTC).timestamp() * 1000)
        assert calls[0].call_ts_ms == expected_ms
        assert len(warnings) == 1 and "line 2" in warnings[0]

    def test_load_ledger_optional_numeric_px(self, tmp_path: Path) -> None:
        line = self._good_line() | {"entry_px": 58100.0}
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(line) + "\n", encoding="utf-8")
        calls, _ = load_ledger(p)
        assert calls[0].entry_px == 58100.0
        assert calls[0].stop_px is None

    def test_load_overrides_and_missing_file(self, tmp_path: Path) -> None:
        p = tmp_path / "overrides.jsonl"
        p.write_text(
            json.dumps({"url": "https://x.com/A/status/1", "stop_px": 56900.0, "skip": False})
            + "\n"
            + json.dumps({"url": "https://x.com/B/status/2", "skip": True, "note": "dup"})
            + "\n",
            encoding="utf-8",
        )
        ov = load_overrides(p)
        assert ov["https://x.com/A/status/1"].stop_px == 56900.0
        assert ov["https://x.com/B/status/2"].skip is True
        assert load_overrides(tmp_path / "absent.jsonl") == {}
```

Update the test-file import line to:

```python
from tools.pundit_score import load_ledger, load_overrides, parse_level_field
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: FAIL — `ImportError: cannot import name 'load_ledger'`.

- [ ] **Step 3: Write the implementation**

Add to `tools/pundit_score.py` (extend the imports: `import json`, `from datetime
import UTC, datetime`, `from pathlib import Path`):

```python
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
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add tools/pundit_score.py tests/test_pundit_score.py
git commit -m "feat(tools): pundit_score ledger + overrides loaders"
```

---

### Task 3: Per-call level resolution (sanity gate, zone edges, precedence, fallbacks)

**Files:**

- Modify: `tools/pundit_score.py`
- Modify: `tests/test_pundit_score.py`

**Interfaces:**

- Consumes: `ParsedField`, `parse_level_field`, `LedgerCall`, `Override`, `SANITY_LO`,
  `SANITY_HI` (Tasks 1–2).
- Produces: `ResolvedLevels` (frozen dataclass: `entry_px: float`,
  `entry_is_thesis: bool`, `stop_px: float | None`, `target_px: float | None`,
  `parse_confidence: str` in `{ok, low, override, fallback}`),
  `select_level(parsed: ParsedField, ref_close: float, role: str, direction: str) ->
  tuple[float | None, bool]` (price, low_confidence_flag), and
  `resolve_levels(call: LedgerCall, override: Override | None, ref_close: float) ->
  ResolvedLevels`.

**Rules being implemented (spec §Level parsing):** zone entry → mid; stop zone → far
edge (long → lo, short → hi); target zone → near edge (long → lo, short → hi); the
first *sane* single number wins (sanity gate `[0.2x, 5x]` of `ref_close` — this
skips date-noise like "June 25" and rejects unit confusion like "38-45" at a 58k
close); precedence override > ledger `*_px` > parsed text > fallback; entry fallback
= thesis entry at `ref_close`; confidence = `override` if any override value used,
else `fallback` if thesis entry, else `low` if any ambiguity/sanity-reject, else `ok`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pundit_score.py`:

```python
def _call(**kw: object) -> "LedgerCall":
    base: dict[str, object] = {
        "line_no": 1,
        "source": "x",
        "author": "A",
        "url": "https://x.com/A/status/1",
        "call_ts_utc": "2026-06-20T10:00:00Z",
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry": "58,000",
        "stop": "57,000",
        "target": "60,000",
        "horizon": "swing",
        "confidence": "",
        "raw_quote": "",
    }
    base.update(kw)
    return LedgerCall(**base)  # type: ignore[arg-type]


class TestResolveLevels:
    REF = 58000.0

    def test_clean_numeric_all_fields_ok(self) -> None:
        lv = resolve_levels(_call(), None, self.REF)
        assert (lv.entry_px, lv.stop_px, lv.target_px) == (58000.0, 57000.0, 60000.0)
        assert lv.parse_confidence == "ok"
        assert not lv.entry_is_thesis

    def test_zone_entry_mid_stop_far_target_near_long(self) -> None:
        lv = resolve_levels(
            _call(entry="57,900-58,200", stop="57,500-57,700", target="59,500-60,700"),
            None,
            self.REF,
        )
        assert lv.entry_px == 58050.0  # zone mid
        assert lv.stop_px == 57500.0  # far edge for a long = lower bound
        assert lv.target_px == 59500.0  # near edge for a long = lower bound

    def test_zone_edges_short(self) -> None:
        lv = resolve_levels(
            _call(direction="short", entry="58,800-59,000", stop="59,200-59,600", target="57,000-57,400"),
            None,
            self.REF,
        )
        assert lv.stop_px == 59600.0  # far edge for a short = upper bound
        assert lv.target_px == 57400.0  # near edge for a short = upper bound

    def test_sanity_gate_skips_date_noise(self) -> None:
        lv = resolve_levels(
            _call(target="liquidity below 58K (June 25 low ~58,043)"), None, self.REF
        )
        assert lv.target_px == 58000.0  # '58K' expands; '25' rejected by the gate
        assert lv.parse_confidence == "low"  # multiple sane candidates -> ambiguous

    def test_sanity_gate_rejects_all_falls_back(self) -> None:
        lv = resolve_levels(_call(entry="HTF demand ~38-45"), None, self.REF)
        assert lv.entry_is_thesis and lv.entry_px == self.REF
        assert lv.parse_confidence == "fallback"

    def test_unspecified_entry_thesis_fallback(self) -> None:
        lv = resolve_levels(_call(entry="unspecified", stop="", target=""), None, self.REF)
        assert lv.entry_is_thesis and lv.entry_px == self.REF
        assert lv.stop_px is None and lv.target_px is None
        assert lv.parse_confidence == "fallback"

    def test_ledger_px_beats_text_and_override_beats_both(self) -> None:
        call = _call(entry="55,000", entry_px=58100.0)
        assert resolve_levels(call, None, self.REF).entry_px == 58100.0
        ov = Override(url=call.url, entry_px=58200.0)
        lv = resolve_levels(call, ov, self.REF)
        assert lv.entry_px == 58200.0
        assert lv.parse_confidence == "override"
```

Update the test-file import to include `LedgerCall, Override, resolve_levels`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_levels'`.

- [ ] **Step 3: Write the implementation**

Add to `tools/pundit_score.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add tools/pundit_score.py tests/test_pundit_score.py
git commit -m "feat(tools): pundit_score level resolution with sanity gate + overrides"
```

---

### Task 4: Setup-family tagger

**Files:**

- Modify: `tools/pundit_score.py`
- Modify: `tests/test_pundit_score.py`

**Interfaces:**

- Consumes: nothing new.
- Produces: `FAMILY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...]` and
  `tag_family(text: str) -> str` returning one of `sweep_reclaim | vp_level |
  ref_level | ema_trend | flow | accumulation_zone | breakout_deviation | other`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pundit_score.py`:

```python
class TestTagFamily:
    def test_one_case_per_family(self) -> None:
        cases = {
            "sweep range low then reclaim": "sweep_reclaim",
            "rotation toward the composite POC": "vp_level",
            "PDL is the trigger": "ref_level",
            "holding the 1W 50EMA": "ema_trend",
            "CVD remains heavy, absorption at lows": "flow",
            "spot-demand accumulation zone below": "accumulation_zone",
            "break of $81 would be very positive": "breakout_deviation",
            "just vibes": "other",
        }
        for text, family in cases.items():
            assert tag_family(text) == family, text

    def test_priority_order_first_match_wins(self) -> None:
        # 'sweep' outranks 'poc' because sweep_reclaim is listed first.
        assert tag_family("sweep into the POC") == "sweep_reclaim"
```

Update the test-file import to include `tag_family`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: FAIL — `ImportError: cannot import name 'tag_family'`.

- [ ] **Step 3: Write the implementation**

Add to `tools/pundit_score.py`:

```python
FAMILY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sweep_reclaim", ("sweep", "reclaim", "sfp", "stop hunt", "stop-hunt", "deviation below", "deviation above")),
    ("vp_level", ("poc", "vah", "val ", "value area", "value-area", "volume profile", "vwap")),
    ("ref_level", ("pdl", "pdh", "pwh", "pwl", "pdval", "range low", "range high", "range-low", "range-high", "weekly open", "daily open", "monday")),
    ("ema_trend", ("ema", "moving average", "50w", "200d", "trendline", "diagonal", "downtrend", "uptrend", "higher low", "lower high")),
    ("flow", ("cvd", "open interest", " oi ", "absorption", "delta", "spot bid", "spot flow", "orderflow", "funding")),
    ("accumulation_zone", ("accumulation", "dca", "demand zone", "spot-demand", "supply zone", "demand")),
    ("breakout_deviation", ("breakout", "break of", "break above", "break below", "acceptance", "deviation")),
)


def tag_family(text: str) -> str:
    """First-match keyword family — a grouping key, not a model (spec §Family)."""
    t = f" {text.lower()} "
    for family, keywords in FAMILY_KEYWORDS:
        if any(k in t for k in keywords):
            return family
    return "other"
```

Note: `"val "` and `" oi "` keep their spaces on purpose — bare substrings would
false-match inside words ("interval", "going"). The audit trail + `family` override
field catch any residual mistags.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add tools/pundit_score.py tests/test_pundit_score.py
git commit -m "feat(tools): pundit_score setup-family tagger"
```

---

### Task 5: Windows, call-candle lookup, trigger/fill

**Files:**

- Modify: `tools/pundit_score.py`
- Modify: `tests/test_pundit_score.py`

**Interfaces:**

- Consumes: `HOUR_MS`, `WINDOWS_MS` (Task 1).
- Produces: `window_ms(horizon: str) -> int`,
  `find_call_candle(df: pd.DataFrame, ts_ms: int) -> int | None` (index of the 1h
  candle containing `ts_ms`),
  `find_fill(df: pd.DataFrame, call_idx: int, entry_px: float, is_thesis: bool,
  limit_ms: int) -> tuple[int, float] | None` (fill index + fill price). DataFrames
  are `ohlcv`-shaped (`open_time` ms ascending, `open/high/low/close` floats).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pundit_score.py` (add `import pandas as pd` to imports). Also
add this shared candle builder at module level of the test file:

```python
T0 = 1_781_949_600_000  # 2026-06-20T10:00:00Z


def _candles(prices: list[tuple[float, float, float, float]], start_ms: int = T0) -> pd.DataFrame:
    """1h OHLC frames from (open, high, low, close) tuples."""
    return pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "open_time": start_ms + i * 3_600_000,
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 1.0,
            }
            for i, (o, h, lo, c) in enumerate(prices)
        ]
    )


class TestWindowsAndFill:
    def test_window_ms_mapping(self) -> None:
        assert window_ms("intraday") == 48 * 3_600_000
        assert window_ms("swing") == 30 * 86_400_000
        assert window_ms("unspecified") == 14 * 86_400_000
        assert window_ms("weird") == 14 * 86_400_000

    def test_find_call_candle(self) -> None:
        df = _candles([(100, 110, 90, 105)] * 3)
        assert find_call_candle(df, T0) == 0
        assert find_call_candle(df, T0 + 90 * 60 * 1000) == 1  # mid-candle
        assert find_call_candle(df, T0 - 1) is None
        assert find_call_candle(df, T0 + 3 * 3_600_000) is None  # past data end

    def test_thesis_fill_at_call_close(self) -> None:
        df = _candles([(100, 110, 90, 105), (105, 120, 100, 115)])
        assert find_fill(df, 0, 105.0, True, T0 + 10 * 3_600_000) == (0, 105.0)

    def test_level_fill_on_first_touch_after_call(self) -> None:
        df = _candles([(100, 110, 90, 105), (105, 108, 101, 102), (102, 106, 95, 96)])
        # entry 98 first trades inside candle 2 (low 95).
        assert find_fill(df, 0, 98.0, False, T0 + 10 * 3_600_000) == (2, 98.0)

    def test_level_fill_respects_deadline(self) -> None:
        df = _candles([(100, 110, 90, 105), (105, 108, 101, 102), (102, 106, 95, 96)])
        # deadline before candle 2 opens -> no fill.
        assert find_fill(df, 0, 98.0, False, T0 + 3_600_000) is None
```

Update the test-file import to include `find_call_candle, find_fill, window_ms`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: FAIL — `ImportError: cannot import name 'window_ms'`.

- [ ] **Step 3: Write the implementation**

Add to `tools/pundit_score.py` (add `import pandas as pd` to imports):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add tools/pundit_score.py tests/test_pundit_score.py
git commit -m "feat(tools): pundit_score windows + trigger/fill walk"
```

---

### Task 6: Resolution walk, R computation, call states

**Files:**

- Modify: `tools/pundit_score.py`
- Modify: `tests/test_pundit_score.py`

**Interfaces:**

- Consumes: everything from Tasks 1–5 plus
  `analytics.backtest.engine._compute_atr14(highs, lows, closes, idx) -> float | None`.
- Produces: state constants `STATE_WIN, STATE_LOSS, STATE_OPEN, STATE_NOT_TRIGGERED,
  STATE_UNSCORED, STATE_UNRESOLVABLE, STATE_STALE, STATE_SKIPPED` (string values equal
  to their suffix), `ScoredCall` (frozen dataclass: `call: LedgerCall`,
  `levels: ResolvedLevels | None`, `family: str`, `state: str`,
  `fill_ts_ms/exit_ts_ms: int | None`, `fill_px/exit_px/r/atr_r: float | None`,
  `win: bool | None`, `note: str`),
  `atr14_before(df: pd.DataFrame, ts_ms: int, tf_ms: int) -> float | None`, and
  `score_call(call: LedgerCall, override: Override | None, df_1h: pd.DataFrame,
  df_1d: pd.DataFrame, as_of_ms: int) -> ScoredCall`.

**Semantics being implemented (spec §Resolution & R, §Call states):** adverse-first
(stop checked before target on every bar, including the fill bar for level entries;
thesis entries start on the next bar — the fill *is* the close); four R branches;
ATR14 horizon-matched (1h for intraday, 1d otherwise) via the engine's convention;
expiry classified WIN/LOSS by sign (flat → LOSS); OPEN covers both awaiting-trigger
and in-position at `--as-of`; STALE when OHLCV ends before resolution was possible.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pundit_score.py`:

```python
def _score(
    call: LedgerCall,
    df_1h: pd.DataFrame,
    as_of_ms: int,
    override: Override | None = None,
) -> "ScoredCall":
    df_1d = _candles([(100, 110, 90, 105)] * 20, start_ms=T0 - 20 * 86_400_000)
    df_1d["open_time"] = [T0 - (20 - i) * 86_400_000 for i in range(20)]
    df_1d["timeframe"] = "1d"
    return score_call(call, override, df_1h, df_1d, as_of_ms)


FAR = T0 + 40 * 86_400_000  # as_of far beyond every swing window


class TestScoreCall:
    def test_win_target_hit_with_stop_gives_rr(self) -> None:
        call = _call(entry="100", stop="90", target="120", horizon="intraday")
        df = _candles([(100, 101, 99, 100), (100, 100, 99, 100), (100, 125, 98, 120)])
        sc = _score(call, df, FAR)
        assert sc.state == "WIN" and sc.win is True
        assert sc.r == 2.0  # (120-100)/(100-90)
        assert sc.fill_px == 100.0 and sc.exit_px == 120.0

    def test_adverse_first_same_bar_is_loss(self) -> None:
        call = _call(entry="100", stop="90", target="120", horizon="intraday")
        df = _candles([(100, 101, 99, 100), (100, 130, 85, 110)])
        sc = _score(call, df, FAR)
        assert sc.state == "LOSS" and sc.r == -1.0

    def test_stop_no_target_expiry_exit_scales_by_risk(self) -> None:
        call = _call(entry="100", stop="95", target="unspecified", horizon="intraday")
        # 50 candles; entry touches candle 1; window 48h from fill; exit at expiry close 104.
        rows = [(100, 101, 99, 100)] + [(100, 104, 99, 104)] * 50
        sc = _score(call, _candles(rows), FAR)
        assert sc.state == "WIN"
        assert sc.r is not None and abs(sc.r - 0.8) < 1e-9  # (104-100)/5

    def test_no_stop_uses_atr_proxy_and_sign(self) -> None:
        call = _call(entry="100", stop="", target="", horizon="intraday")
        # 20 warm-up candles BEFORE the call so ATR14 has closed 1h history at fill
        # (mirrors load_ohlcv_for_calls' 20-candle back-buffer).
        rows = [(100, 101, 99, 100)] * 21 + [(100, 101, 95, 96)] * 50
        sc = _score(call, _candles(rows, start_ms=T0 - 20 * 3_600_000), FAR)
        assert sc.state == "LOSS" and sc.r is None
        assert sc.atr_r is not None and sc.atr_r < 0

    def test_short_direction_win(self) -> None:
        call = _call(direction="short", entry="100", stop="110", target="80", horizon="intraday")
        df = _candles([(100, 101, 99, 100), (100, 102, 75, 80)])
        sc = _score(call, df, FAR)
        assert sc.state == "WIN" and sc.r == 2.0  # (100-80)/(110-100)

    def test_open_in_position_before_expiry(self) -> None:
        call = _call(entry="100", stop="90", target="120", horizon="swing")
        df = _candles([(100, 101, 99, 100), (100, 101, 99, 100)])
        sc = _score(call, df, T0 + 2 * 3_600_000)
        assert sc.state == "OPEN" and sc.note == "in position"

    def test_open_awaiting_trigger(self) -> None:
        call = _call(entry="90", stop="85", target="120", horizon="swing")
        df = _candles([(100, 101, 99, 100), (100, 101, 99, 100)])
        sc = _score(call, df, T0 + 2 * 3_600_000)
        assert sc.state == "OPEN" and sc.note == "awaiting trigger"

    def test_not_triggered_after_deadline(self) -> None:
        call = _call(entry="90", stop="85", target="120", horizon="intraday")
        rows = [(100, 101, 99, 100)] * 60  # 60h of candles never touching 90
        sc = _score(call, _candles(rows), FAR)
        assert sc.state == "NOT_TRIGGERED"

    def test_stale_when_data_ends_mid_window(self) -> None:
        call = _call(entry="100", stop="90", target="120", horizon="swing")
        df = _candles([(100, 101, 99, 100), (100, 101, 99, 100)])  # 2h of data, 30d window
        sc = _score(call, df, FAR)
        assert sc.state == "STALE"

    def test_neutral_unscored_and_skip(self) -> None:
        df = _candles([(100, 101, 99, 100)])
        assert _score(_call(direction="neutral"), df, FAR).state == "UNSCORED"
        ov = Override(url="https://x.com/A/status/1", skip=True)
        assert _score(_call(), df, FAR, override=ov).state == "SKIPPED"

    def test_unresolvable_without_data(self) -> None:
        sc = _score(_call(), _candles([]), FAR)
        assert sc.state == "UNRESOLVABLE"
```

Update the test-file import to include `ScoredCall, score_call`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: FAIL — `ImportError: cannot import name 'score_call'`.

- [ ] **Step 3: Write the implementation**

Add to `tools/pundit_score.py` (add imports: `import numpy as np`,
`from analytics.backtest.engine import _compute_atr14`):

```python
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
        return ScoredCall(call, None, family, STATE_UNRESOLVABLE, note="call candle missing")

    ref_close = float(df_1h["close"].iloc[call_idx])
    levels = resolve_levels(call, override, ref_close)
    win_ms = window_ms(call.horizon)
    data_end_ms = int(df_1h["open_time"].iloc[-1]) + HOUR_MS
    trigger_deadline = call.call_ts_ms + win_ms

    fill = find_fill(
        df_1h, call_idx, levels.entry_px, levels.entry_is_thesis, min(trigger_deadline, as_of_ms)
    )
    if fill is None:
        if trigger_deadline <= as_of_ms and data_end_ms >= trigger_deadline:
            return ScoredCall(call, levels, family, STATE_NOT_TRIGGERED)
        if data_end_ms < min(trigger_deadline, as_of_ms):
            return ScoredCall(
                call, levels, family, STATE_STALE, note="OHLCV ends in trigger window — sync first"
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
                call, levels, family, STATE_STALE,
                fill_ts_ms=fill_ts, fill_px=fill_px, note="OHLCV ends mid-window — sync first",
            )
        else:
            return ScoredCall(
                call, levels, family, STATE_OPEN,
                fill_ts_ms=fill_ts, fill_px=fill_px, note="in position",
            )

    assert exit_px is not None and exit_ts is not None
    r: float | None = None
    if risk is not None and risk > 0:
        if state == STATE_LOSS and exit_px == levels.stop_px:
            r = -1.0
        elif state == STATE_WIN and levels.target_px is not None and exit_px == levels.target_px:
            r = abs(levels.target_px - fill_px) / risk
        else:  # expiry exit with a known stop
            r = dirsign * (exit_px - fill_px) / risk
    tf_ms = HOUR_MS if call.horizon == "intraday" else DAY_MS
    atr = atr14_before(df_1h if call.horizon == "intraday" else df_1d, fill_ts, tf_ms)
    atr_r = dirsign * (exit_px - fill_px) / atr if atr else None
    return ScoredCall(
        call, levels, family, state,
        fill_ts_ms=fill_ts, fill_px=fill_px, exit_ts_ms=exit_ts, exit_px=exit_px,
        r=r, atr_r=atr_r, win=state == STATE_WIN,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: all PASS. (If `test_stop_no_target_expiry_exit_scales_by_risk` disagrees on
the exit candle, check the `searchsorted` expiry index — the exit is the close of the
last candle opening at or before `fill_ts + window`.)

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add tools/pundit_score.py tests/test_pundit_score.py
git commit -m "feat(tools): pundit_score resolution walk + R branches + call states"
```

---

### Task 7: Aggregation, markdown report, priors JSON

**Files:**

- Modify: `tools/pundit_score.py`
- Modify: `tests/test_pundit_score.py`

**Interfaces:**

- Consumes: `ScoredCall`, state constants (Task 6).
- Produces: `CellStats` (mutable dataclass with `add(sc: ScoredCall) -> None` and
  properties `hit_rate/avg_r/avg_atr_r -> float | None`),
  `aggregate(scored: list[ScoredCall], key_fn: Callable[[ScoredCall], str]) ->
  dict[str, CellStats]`,
  `render_report(scored: list[ScoredCall], warnings: list[str], as_of_iso: str,
  min_n: int) -> str`, and
  `build_priors(scored: list[ScoredCall], as_of_iso: str, generated_at_iso: str,
  min_n: int) -> dict[str, object]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pundit_score.py`:

```python
def _scored_fixture() -> list[ScoredCall]:
    df = _candles([(100, 101, 99, 100), (100, 100, 99, 100), (100, 125, 98, 120)])
    win = _score(_call(entry="100", stop="90", target="120", horizon="intraday"), df, FAR)
    loss_df = _candles([(100, 101, 99, 100), (100, 130, 85, 110)])
    loss = _score(
        _call(author="B", url="https://x.com/B/status/2", entry="100", stop="90",
              target="120", horizon="intraday", raw_quote="POC rotation"),
        loss_df, FAR,
    )
    open_df = _candles([(100, 101, 99, 100), (100, 101, 99, 100)])
    open_ = _score(
        _call(author="A", url="https://x.com/A/status/3", entry="100", stop="90",
              target="120", horizon="swing"),
        open_df, T0 + 2 * 3_600_000,
    )
    return [win, loss, open_]


class TestAggregateAndOutputs:
    def test_aggregate_per_author(self) -> None:
        cells = aggregate(_scored_fixture(), lambda sc: sc.call.author)
        a, b = cells["A"], cells["B"]
        assert (a.n, a.triggered, a.open_, a.resolved, a.wins) == (2, 2, 1, 1, 1)
        assert a.hit_rate == 1.0 and a.avg_r == 2.0
        assert (b.n, b.resolved, b.wins) == (1, 1, 0)
        assert b.avg_r == -1.0

    def test_render_report_sections_and_audit_trail(self) -> None:
        report = render_report(_scored_fixture(), ["ledger line 9: skipped"], "2026-07-04T00:00:00Z", 5)
        assert "## Per author" in report
        assert "## Per setup-family" in report
        assert "## Audit trail" in report
        assert "ledger line 9" in report
        assert "OPEN" in report and "WIN" in report and "LOSS" in report
        assert "⚠" in report  # n<5 marker present at this tiny n

    def test_build_priors_schema_and_determinism(self) -> None:
        scored = _scored_fixture()
        p1 = build_priors(scored, "2026-07-04T00:00:00Z", "2026-07-04T09:00:00Z", 5)
        p2 = build_priors(scored, "2026-07-04T00:00:00Z", "2026-07-04T09:00:00Z", 5)
        assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)
        assert p1["as_of"] == "2026-07-04T00:00:00Z"
        assert p1["policy"]["windows"]["intraday"] == "48h"  # type: ignore[index]
        authors = p1["authors"]
        assert isinstance(authors, dict) and authors["A"]["n"] == 2
        assert "families" in p1
```

Update the test-file import to include `aggregate, build_priors, render_report`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: FAIL — `ImportError: cannot import name 'aggregate'`.

- [ ] **Step 3: Write the implementation**

Add to `tools/pundit_score.py` (add `from collections.abc import Callable` and
`from dataclasses import field` if needed):

```python
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
        f"| {label} | n | trig | open | resolved | hit% | avg R | avg ATR-R | |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key in sorted(cells):
        c = cells[key]
        hit = f"{100 * c.hit_rate:.0f}%" if c.hit_rate is not None else "—"
        mark = f"⚠ n<{min_n}" if c.n < min_n else ""
        lines.append(
            f"| {key} | {c.n} | {c.triggered} | {c.open_} | {c.resolved} "
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
    families = {key: _cell_dict(by_family[key]) for key in sorted(by_family)}
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add tools/pundit_score.py tests/test_pundit_score.py
git commit -m "feat(tools): pundit_score aggregation + report + priors JSON"
```

---

### Task 8: DB loaders, CLI, Makefile target, docs, full DoD

**Files:**

- Modify: `tools/pundit_score.py`
- Modify: `tests/test_pundit_score.py`
- Modify: `Makefile`
- Modify: `CLAUDE.md`

**Interfaces:**

- Consumes: everything above plus `analytics.store.DEFAULT_DB_PATH`,
  `analytics.store.market_data.get_ohlcv(conn, symbol, timeframe, start, end)`
  (Unix ms inclusive), `analytics.store.schema.init_schema` (tests only).
- Produces: `load_ohlcv_for_calls(conn: duckdb.DuckDBPyConnection,
  calls: list[LedgerCall], as_of_ms: int) -> dict[tuple[str, str], pd.DataFrame]`,
  `build_parser() -> argparse.ArgumentParser`, `main() -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pundit_score.py` (add `import duckdb` and
`from analytics.store.market_data import upsert_ohlcv` and
`from analytics.store.schema import init_schema` to imports):

```python
class TestDbAndCli:
    def test_load_ohlcv_for_calls_in_memory(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        df = _candles([(100, 110, 90, 105)] * 30)
        df["taker_buy_volume"] = 0.5  # upsert_ohlcv requires the full 9-column list
        upsert_ohlcv(conn, df)
        d1 = df.copy()
        d1["timeframe"] = "1d"
        upsert_ohlcv(conn, d1)
        calls = [_call()]
        data = load_ohlcv_for_calls(conn, calls, FAR)
        assert not data[("BTCUSDT", "1h")].empty
        assert not data[("BTCUSDT", "1d")].empty
        assert list(data[("BTCUSDT", "1h")]["open_time"]) == sorted(
            data[("BTCUSDT", "1h")]["open_time"]
        )

    def test_build_parser_defaults(self) -> None:
        args = build_parser().parse_args([])
        assert args.ledger == Path("docs/plans/pundit-calls.jsonl")
        assert args.overrides == Path("docs/plans/pundit-overrides.jsonl")
        assert args.json == Path("docs/plans/pundit-priors.json")
        assert args.min_n == 5
        assert args.as_of is None

    def test_parse_as_of(self) -> None:
        args = build_parser().parse_args(["--as-of", "2026-07-04T00:00:00Z"])
        assert args.as_of == "2026-07-04T00:00:00Z"
```

Update the test-file import to include `build_parser, load_ohlcv_for_calls`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_parser'`.

- [ ] **Step 3: Write the implementation**

Add to `tools/pundit_score.py` (add imports: `import argparse`, `import sys`,
`import duckdb`, `from analytics.store import DEFAULT_DB_PATH`,
`from analytics.store.market_data import get_ohlcv`):

```python
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
    p.add_argument("--overrides", type=Path, default=Path("docs/plans/pundit-overrides.jsonl"))
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
    args.json.write_text(json.dumps(priors, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"priors written: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_pundit_score.py -q`
Expected: all PASS.

- [ ] **Step 5: Add the Makefile target**

Add next to the other `buibui-*` tool targets in `Makefile`:

```make
.PHONY: buibui-pundit-score
buibui-pundit-score:  ## score the pundit-call ledger vs OHLCV -> priors (sync universe 1h/1d first)
    PYTHONPATH=. poetry run python tools/pundit_score.py
```

(The recipe line above is shown with spaces for the linter — the real `Makefile`
edit must indent it with a **TAB**, like every other target.)

- [ ] **Step 6: Add the CLAUDE.md tools bullet**

In `CLAUDE.md` under the `tools/` section (after the `x_route.py` bullet), add:

```markdown
  - `pundit_score.py` — read-only pundit-ledger scorer (Stream C): resolves every `docs/plans/pundit-calls.jsonl` call against 1h/1d OHLCV (deterministic free-text level parser + `docs/plans/pundit-overrides.jsonl` sidecar, pre-committed 48h/30d/14d horizon windows, adverse-first ties, ATR-proxy R for stop-less calls) → hit-rate + R proxies per author × setup-family × direction; markdown report + per-call audit trail + gitignored `docs/plans/pundit-priors.json` (the daily-brief / F2 trade-card hook). Descriptive priors only — NO verdicts (audit_guard gates deferred until a cell reaches n≥30). Spec `docs/superpowers/specs/2026-07-04-pundit-ledger-scorer-design.md`. Run via `make buibui-pundit-score` (`--as-of` for reproducible runs).
```

- [ ] **Step 7: Full DoD + commit**

```bash
make lint-py && make typecheck && make test && make test-regression && make lint-md
git add tools/pundit_score.py tests/test_pundit_score.py Makefile CLAUDE.md
git commit -m "feat(tools): pundit_score CLI + Makefile target + docs"
```

Expected: all four gates green; goldens unmoved (the tool is read-only and touches no
engine path). State each result plainly in the session log.

---

### Task 9: First real run + overrides seeding (operator-facing, after review)

**Files:** none committed (outputs are gitignored).

- [ ] **Step 1: Refresh universe data**

Run: `poetry run python buibui.py analytics sync --universe --timeframes 1h 1d`
(HYPE/INJ/ONDO were ~10 days stale at plan time.)

- [ ] **Step 2: First scored run (fixed as-of for reproducibility)**

Run: `make buibui-pundit-score` (or with an explicit
`--as-of $(date -u +%Y-%m-%dT%H:%M:%SZ)` noted in the session log).
Expected: report renders all 36 calls in the audit trail; many swing calls OPEN;
0 silent drops (every line has a state).

- [ ] **Step 3: Eyeball the audit trail, seed `docs/plans/pundit-overrides.jsonl`**

For every `low`/`fallback`-confidence row whose parse is visibly wrong, add an
override line with the correct numeric levels (or `skip: true` + note for genuinely
unscorable calls). Re-run and confirm the overrides take effect (`conf` column shows
`override`).

- [ ] **Step 4: PR + wrap-up**

Follow the repo flow: `/pr-summary` → `gh pr create` → `/post-branch` (MEMORY.md
Current State + handoff prompt rewrite).

---

## Self-review checklist (run after writing the plan)

1. **Spec coverage:** parsing rules (T1/T3), overrides + precedence (T2/T3), family
   tagging (T4), trigger/windows (T5), resolution/R/states incl. adverse-first,
   ATR-proxy, OPEN/NOT_TRIGGERED/STALE/UNSCORED/SKIPPED (T6), report + audit trail +
   priors JSON + min-n marker (T7), CLI/`--as-of`/read-only DB/Makefile/docs (T8),
   runbook + overrides seeding (T9). Error handling: malformed lines (T2), missing
   data (T6 UNRESOLVABLE), staleness (T6). ✓
2. **Placeholder scan:** every code step contains complete code; no TBDs. ✓
3. **Type consistency:** names used across tasks match the Interfaces blocks
   (`ParsedField`, `LedgerCall`, `Override`, `ResolvedLevels`, `ScoredCall`,
   `CellStats`, state constants, `window_ms`, `find_call_candle`, `find_fill`,
   `atr14_before`, `score_call`, `aggregate`, `render_report`, `build_priors`,
   `load_ohlcv_for_calls`, `build_parser`, `main`). ✓
