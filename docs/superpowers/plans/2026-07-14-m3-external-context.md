# Brief-v2 M3 External Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operator-dropped Coinglass/MMT heatmap + liquidation-map screenshots become verified, age-stamped external levels in the daily Brief's new per-symbol "External" block.

**Architecture:** A `/ingest-charts` skill (sonnet vision per image, one review digest, write-on-approval) produces `docs/plans/external-context/*.json`; a pure conn-free reader `analytics/brief/external.py` (M1/M2 adapter pattern: returns `(state | None, unprefixed_notes)`) feeds an additive `SymbolPanel.external` field rendered after the Sessions block. `tools/chart_drops.py` owns all testable writer-side logic (filename parse, sha256 ledger, schema-validated writes) and imports the validator from the reader so both share one contract.

**Tech Stack:** Python 3.11 + Poetry, frozen dataclasses, pytest (tmp_path, `tests/_brief_fixtures.py` DuckDB :memory: fixtures), Svelte 5 for the one UI block. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-14-m3-external-context-design.md` (v1.1). Two plan-time adaptations, reflected back into the spec: config lives as `BriefConfig` fields (no brief TOML exists in this codebase), and the output filename carries the window segment when present.

## Global Constraints

- mypy strict: every function annotated, including test functions (`-> None`).
- Tests: no network, no LLM, no vision, no real `analytics.db` — `tmp_path` + `tests/_brief_fixtures.py` (`make_conn`, `seed_symbol`, `START_MS`, `DAY_MS`) only.
- Additive-pure: `SymbolPanel.external` is appended LAST with default `None` so every existing construction site, serialisation, and golden stays byte-identical.
- Exact constants (verbatim everywhere): schema `"external-levels-v1"`; panels `("liq_heatmap", "book_heatmap", "liq_map")`; kinds `("liq", "book")`; intensities `("high", "med", "low")`; scopes `("pair", "agg")`; spot-deviation threshold `0.10`; defaults `Path("docs/plans/external-context")`, `48.0` hours, `3` rows/side, sources `("coinglass", "mmt")`; drop dir `Path("docs/plans/chart-drops")`; ledger `Path(".cache/chart-drops/processed.json")`.
- MYT is a fixed UTC+8 offset (`timezone(timedelta(hours=8))`) — no pytz/zoneinfo.
- `docs/plans/` and `.cache/` are already gitignored — never `git add` drops, snapshots, or the ledger.
- Run per-task tests with `poetry run pytest tests/<file>.py -v`; the final task runs the full gate (`make lint-py`, `make typecheck`, `make test`, `make test-regression`, `make lint-md`).
- Conventional commits; end commit messages with the trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` (blank line before it).
- When a task's test snippet shows `import` lines, merge them into the test file's TOP import block (ruff flags mid-file imports).
- Notes returned by the reader are UNPREFIXED — the bundle adds `f"{symbol}: {n}"` (M1/M2 contract).

---

### Task 1: External dataclasses + `SymbolPanel.external`

**Files:**

- Modify: `analytics/brief/types.py` (3 new dataclasses before `SymbolPanel` at line ~161; one field appended to `SymbolPanel`)
- Create: `tests/test_brief_external.py`

**Interfaces:**

- Consumes: nothing new.
- Produces: `ExternalClusterRow(price_lo, price_hi, kind, intensity, label, dist_atr)`, `ExternalSnapshot(source, panel, window, scope, captured_at_ms, age_hours, spot_price_hint, spot_hint_deviation, clusters_above, clusters_below)`, `ExternalState(snapshots)` — all frozen; `SymbolPanel.external: ExternalState | None = None` (LAST field, defaulted).

- [ ] **Step 1: Write the failing test**

Create `tests/test_brief_external.py`:

```python
"""brief/external — M3 external-context types, contract, and loader."""

from dataclasses import asdict

from analytics.brief.types import (
    ExternalClusterRow,
    ExternalSnapshot,
    ExternalState,
    error_panel,
)

HOUR_MS = 3_600_000


def _row(dist_atr: float = 1.8) -> ExternalClusterRow:
    return ExternalClusterRow(
        price_lo=65_800.0,
        price_hi=66_200.0,
        kind="liq",
        intensity="high",
        label="long-liq shelf",
        dist_atr=dist_atr,
    )


def test_external_types_serialise() -> None:
    snap = ExternalSnapshot(
        source="coinglass",
        panel="liq_heatmap",
        window="24h",
        scope="pair",
        captured_at_ms=1_000,
        age_hours=14.0,
        spot_price_hint=63_250.0,
        spot_hint_deviation=False,
        clusters_above=[_row()],
        clusters_below=[],
    )
    data = asdict(ExternalState(snapshots=[snap]))
    assert data["snapshots"][0]["clusters_above"][0]["price_lo"] == 65_800.0
    assert data["snapshots"][0]["window"] == "24h"


def test_symbol_panel_external_defaults_none() -> None:
    assert error_panel("BTCUSDT", "boom").external is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_brief_external.py -v`
Expected: FAIL — `ImportError: cannot import name 'ExternalClusterRow'`

- [ ] **Step 3: Write minimal implementation**

In `analytics/brief/types.py`, insert immediately BEFORE the `@dataclass(frozen=True)` line of `class SymbolPanel`:

```python
@dataclass(frozen=True)
class ExternalClusterRow:
    price_lo: float
    price_hi: float
    kind: str  # "liq" | "book"
    intensity: str  # "high" | "med" | "low"
    label: str
    dist_atr: float  # (band midpoint - ref) / atr14: + = above price


@dataclass(frozen=True)
class ExternalSnapshot:
    source: str  # "coinglass" | "mmt" (config-extensible)
    panel: str  # "liq_heatmap" | "book_heatmap" | "liq_map"
    window: str | None  # from the visible timeframe selector, e.g. "24h"
    scope: str | None  # "pair" | "agg"
    captured_at_ms: int
    age_hours: float
    spot_price_hint: float | None
    spot_hint_deviation: bool  # |hint - ref| / ref > 0.10
    clusters_above: list[ExternalClusterRow]  # nearest-first
    clusters_below: list[ExternalClusterRow]  # nearest-first


@dataclass(frozen=True)
class ExternalState:
    snapshots: list[ExternalSnapshot]
```

Then append ONE field to `SymbolPanel` as its LAST field (after `error: str | None`) so no construction site breaks:

```python
    external: ExternalState | None = None
```

Do NOT touch `error_panel` — the default covers it.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_brief_external.py tests/test_brief_types.py -v`
Expected: PASS (both files — proves no existing construction site broke)

- [ ] **Step 5: Commit**

```bash
git add analytics/brief/types.py tests/test_brief_external.py
git commit -m "feat(brief): M3 external-context dataclasses + SymbolPanel.external"
```

---

### Task 2: Contract validator `validate_snapshot_dict`

**Files:**

- Create: `analytics/brief/external.py`
- Test: `tests/test_brief_external.py` (extend)

**Interfaces:**

- Consumes: types from Task 1 (imports only in later tasks; this task is pure validation).
- Produces: `SCHEMA_VERSION: str`, `ALLOWED_PANELS/ALLOWED_KINDS/ALLOWED_INTENSITIES/ALLOWED_SCOPES: tuple[str, ...]`, `validate_snapshot_dict(data: object) -> list[str]` ([] = valid). Task 6 imports the validator; Task 3 builds the loader in this same module.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief_external.py`:

```python
from typing import Any

from analytics.brief.external import validate_snapshot_dict

AS_OF = 1_789_400_000_000


def _valid_snapshot(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema": "external-levels-v1",
        "source": "coinglass",
        "symbol": "BTCUSDT",
        "panel": "liq_heatmap",
        "window": "24h",
        "scope": "pair",
        "captured_at_ms": AS_OF - 14 * HOUR_MS,
        "ingested_at_ms": AS_OF - 13 * HOUR_MS,
        "verified": True,
        "spot_price_hint": 63_250.0,
        "clusters": [
            {
                "price_lo": 65_800.0,
                "price_hi": 66_200.0,
                "kind": "liq",
                "intensity": "high",
                "label": "",
            },
            {
                "price_lo": 61_200.0,
                "price_hi": 61_500.0,
                "kind": "liq",
                "intensity": "med",
                "label": "100x-heavy",
            },
        ],
        "notes": "",
    }
    data.update(overrides)
    return data


def test_validate_accepts_contract() -> None:
    assert validate_snapshot_dict(_valid_snapshot()) == []
    assert validate_snapshot_dict(_valid_snapshot(window=None, scope=None)) == []
    assert validate_snapshot_dict(_valid_snapshot(spot_price_hint=None)) == []


def test_validate_rejects_bad_shapes() -> None:
    assert validate_snapshot_dict("nope") != []
    assert validate_snapshot_dict(_valid_snapshot(extra_key=1)) != []
    assert validate_snapshot_dict(_valid_snapshot(schema="external-levels-v2")) != []
    assert validate_snapshot_dict(_valid_snapshot(panel="oi_chart")) != []
    assert validate_snapshot_dict(_valid_snapshot(scope="global")) != []
    assert validate_snapshot_dict(_valid_snapshot(verified=False)) != []
    assert validate_snapshot_dict(_valid_snapshot(clusters=[])) != []
    bad = _valid_snapshot()
    bad["clusters"][0]["price_lo"] = 99_999.0  # lo > hi
    assert validate_snapshot_dict(bad) != []
    missing = _valid_snapshot()
    del missing["window"]
    assert validate_snapshot_dict(missing) != []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_brief_external.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.brief.external'`

- [ ] **Step 3: Write the implementation**

Create `analytics/brief/external.py`:

```python
"""External-context reader (M3): verified heatmap/liq-map snapshots.

Pure and deterministic: validates + loads operator-verified JSON snapshots
(written by /ingest-charts via tools/chart_drops.py) and derives the
per-symbol External block against the brief's ref price. Disk is untrusted
(hand-editable): validation failures become notes, never exceptions.
Conn-free; the bundle passes everything in. The validator is shared with
the writer side (tools/chart_drops.py imports it) so writer and reader
hold one contract.
"""

from __future__ import annotations

SCHEMA_VERSION = "external-levels-v1"
ALLOWED_PANELS = ("liq_heatmap", "book_heatmap", "liq_map")
ALLOWED_KINDS = ("liq", "book")
ALLOWED_INTENSITIES = ("high", "med", "low")
ALLOWED_SCOPES = ("pair", "agg")
_SPOT_DEVIATION_FRAC = 0.10
_MS_PER_HOUR = 3_600_000

_REQUIRED_KEYS = {
    "schema",
    "source",
    "symbol",
    "panel",
    "window",
    "scope",
    "captured_at_ms",
    "ingested_at_ms",
    "verified",
    "spot_price_hint",
    "clusters",
    "notes",
}
_CLUSTER_KEYS = {"price_lo", "price_hi", "kind", "intensity", "label"}


def _is_num(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_cluster(idx: int, item: object) -> list[str]:
    if not isinstance(item, dict):
        return [f"clusters[{idx}]: not an object"]
    keys = set(item)
    if keys != _CLUSTER_KEYS:
        bad = sorted(keys.symmetric_difference(_CLUSTER_KEYS))
        return [f"clusters[{idx}]: bad keys {bad}"]
    problems: list[str] = []
    if not _is_num(item["price_lo"]) or not _is_num(item["price_hi"]):
        problems.append(f"clusters[{idx}]: prices not numbers")
    elif float(item["price_lo"]) > float(item["price_hi"]):
        problems.append(f"clusters[{idx}]: price_lo > price_hi")
    elif float(item["price_lo"]) <= 0:
        problems.append(f"clusters[{idx}]: price_lo <= 0")
    if item["kind"] not in ALLOWED_KINDS:
        problems.append(f"clusters[{idx}]: kind {item['kind']!r} invalid")
    if item["intensity"] not in ALLOWED_INTENSITIES:
        problems.append(f"clusters[{idx}]: intensity {item['intensity']!r} invalid")
    if not isinstance(item["label"], str):
        problems.append(f"clusters[{idx}]: label not a string")
    return problems


def validate_snapshot_dict(data: object) -> list[str]:
    """Contract check shared by writer and reader. [] means valid.

    Unknown top-level keys are rejected — the schema version bumps instead.
    """
    if not isinstance(data, dict):
        return ["snapshot is not a JSON object"]
    problems: list[str] = []
    keys = set(data)
    missing = _REQUIRED_KEYS - keys
    unknown = keys - _REQUIRED_KEYS
    if missing:
        problems.append(f"missing keys: {sorted(missing)}")
    if unknown:
        problems.append(f"unknown keys: {sorted(unknown)}")
    if problems:
        return problems
    if data["schema"] != SCHEMA_VERSION:
        problems.append(f"schema {data['schema']!r} != {SCHEMA_VERSION!r}")
    for key in ("source", "symbol"):
        if not isinstance(data[key], str) or not data[key]:
            problems.append(f"{key}: not a non-empty string")
    if not isinstance(data["notes"], str):
        problems.append("notes: not a string")
    if data["panel"] not in ALLOWED_PANELS:
        problems.append(f"panel {data['panel']!r} not in {ALLOWED_PANELS}")
    if data["window"] is not None and not isinstance(data["window"], str):
        problems.append("window: not a string or null")
    if data["scope"] is not None and data["scope"] not in ALLOWED_SCOPES:
        problems.append(f"scope {data['scope']!r} not in {ALLOWED_SCOPES}")
    for key in ("captured_at_ms", "ingested_at_ms"):
        value = data[key]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append(f"{key}: not a positive integer")
    if data["verified"] is not True:
        problems.append("verified: must be true")
    if data["spot_price_hint"] is not None and not _is_num(data["spot_price_hint"]):
        problems.append("spot_price_hint: not a number or null")
    if not isinstance(data["clusters"], list) or not data["clusters"]:
        problems.append("clusters: not a non-empty list")
    else:
        for idx, item in enumerate(data["clusters"]):
            problems.extend(_check_cluster(idx, item))
    return problems
```

(The module has NO imports beyond `__future__` at this point — `json`, `Path`, `Any`, and the three type imports arrive with Task 3's loader, keeping this task's `make lint-py` clean.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_brief_external.py -v && make lint-py`
Expected: PASS, lint clean

- [ ] **Step 5: Commit**

```bash
git add analytics/brief/external.py tests/test_brief_external.py
git commit -m "feat(brief): M3 snapshot contract validator (external-levels-v1)"
```

---

### Task 3: Loader `load_external_state`

**Files:**

- Modify: `analytics/brief/external.py`
- Test: `tests/test_brief_external.py` (extend)

**Interfaces:**

- Consumes: Task 1 types, Task 2 validator.
- Produces: `load_external_state(dir_path: Path, symbol: str, ref_close: float, atr14: float, as_of_ms: int, allowed_sources: tuple[str, ...], max_age_hours: float, max_rows_per_side: int) -> tuple[ExternalState | None, list[str]]` — notes UNPREFIXED; Task 4's bundle wiring prefixes the symbol.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief_external.py`:

```python
import json
from pathlib import Path

from analytics.brief.external import load_external_state


def _load(
    tmp_path: Path,
    *,
    ref: float = 63_000.0,
    atr: float = 500.0,
    max_age: float = 48.0,
    rows: int = 3,
) -> tuple[object, list[str]]:
    return load_external_state(
        dir_path=tmp_path,
        symbol="BTCUSDT",
        ref_close=ref,
        atr14=atr,
        as_of_ms=AS_OF,
        allowed_sources=("coinglass", "mmt"),
        max_age_hours=max_age,
        max_rows_per_side=rows,
    )


def _write(tmp_path: Path, name: str, data: dict[str, Any]) -> None:
    (tmp_path / name).write_text(json.dumps(data))


def test_loader_missing_dir_is_silent(tmp_path: Path) -> None:
    state, notes = _load(tmp_path / "absent")
    assert state is None and notes == []


def test_loader_happy_path_sides_and_distances(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", _valid_snapshot())
    state, notes = _load(tmp_path)
    assert notes == []
    assert state is not None
    snap = state.snapshots[0]  # type: ignore[attr-defined]
    assert len(snap.clusters_above) == 1 and len(snap.clusters_below) == 1
    # midpoints: 66,000 -> +6.0 ATR; 61,350 -> -3.3 ATR (atr=500, ref=63,000)
    assert snap.clusters_above[0].dist_atr == 6.0
    assert snap.clusters_below[0].dist_atr == -3.3
    assert snap.age_hours == 14.0
    assert snap.spot_hint_deviation is False


def test_loader_latest_per_source_panel_window(tmp_path: Path) -> None:
    _write(tmp_path, "old.json", _valid_snapshot(captured_at_ms=AS_OF - 20 * HOUR_MS))
    _write(tmp_path, "new.json", _valid_snapshot(captured_at_ms=AS_OF - 2 * HOUR_MS))
    _write(
        tmp_path,
        "map.json",
        _valid_snapshot(
            panel="liq_map", window="1d", captured_at_ms=AS_OF - 3 * HOUR_MS
        ),
    )
    state, _ = _load(tmp_path)
    assert state is not None
    snaps = state.snapshots  # type: ignore[attr-defined]
    assert len(snaps) == 2  # heatmap (latest of the two) + map
    heat = [s for s in snaps if s.panel == "liq_heatmap"][0]
    assert heat.age_hours == 2.0


def test_loader_ignores_other_symbols_silently(tmp_path: Path) -> None:
    _write(tmp_path, "eth.json", _valid_snapshot(symbol="ETHUSDT"))
    state, notes = _load(tmp_path)
    assert state is None and notes == []


def test_loader_notes_on_bad_files(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json")
    _write(tmp_path, "invalid.json", _valid_snapshot(panel="oi_chart"))
    _write(tmp_path, "rogue.json", _valid_snapshot(source="hyblock"))
    state, notes = _load(tmp_path)
    assert state is None
    assert any("unreadable" in n for n in notes)
    assert any("invalid" in n for n in notes)
    assert any("unknown source" in n for n in notes)


def test_loader_all_stale_note(tmp_path: Path) -> None:
    _write(tmp_path, "old.json", _valid_snapshot(captured_at_ms=AS_OF - 80 * HOUR_MS))
    state, notes = _load(tmp_path)
    assert state is None
    assert any("stale" in n and "re-drop" in n for n in notes)


def test_loader_atr_zero_omits_block(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", _valid_snapshot())
    state, notes = _load(tmp_path, atr=0.0)
    assert state is None
    assert any("ATR unavailable" in n for n in notes)


def test_loader_spot_deviation_flag(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", _valid_snapshot(spot_price_hint=40_000.0))
    state, notes = _load(tmp_path)
    assert state is not None
    assert state.snapshots[0].spot_hint_deviation is True  # type: ignore[attr-defined]
    assert any("spot hint deviates" in n for n in notes)


def test_loader_caps_rows_nearest_first(tmp_path: Path) -> None:
    clusters = [
        {"price_lo": p, "price_hi": p, "kind": "liq", "intensity": "low", "label": ""}
        for p in (64_000.0, 65_000.0, 66_000.0, 67_000.0, 68_000.0)
    ]
    _write(tmp_path, "a.json", _valid_snapshot(clusters=clusters))
    state, _ = _load(tmp_path, rows=2)
    assert state is not None
    above = state.snapshots[0].clusters_above  # type: ignore[attr-defined]
    assert [r.price_lo for r in above] == [64_000.0, 65_000.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_brief_external.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_external_state'`

- [ ] **Step 3: Write the implementation**

Append to `analytics/brief/external.py` (and add the imports deferred from Task 2 — final import block of the module):

```python
import json
from pathlib import Path
from typing import Any

from analytics.brief.types import ExternalClusterRow, ExternalSnapshot, ExternalState
```

```python
def _build_snapshot(
    data: dict[str, Any],
    ref_close: float,
    atr14: float,
    as_of_ms: int,
    max_rows_per_side: int,
    notes: list[str],
) -> ExternalSnapshot:
    above: list[ExternalClusterRow] = []
    below: list[ExternalClusterRow] = []
    for item in data["clusters"]:
        lo, hi = float(item["price_lo"]), float(item["price_hi"])
        mid = (lo + hi) / 2.0
        row = ExternalClusterRow(
            price_lo=lo,
            price_hi=hi,
            kind=str(item["kind"]),
            intensity=str(item["intensity"]),
            label=str(item["label"]),
            dist_atr=(mid - ref_close) / atr14,
        )
        (above if mid >= ref_close else below).append(row)
    above.sort(key=lambda r: r.dist_atr)  # nearest first
    below.sort(key=lambda r: -r.dist_atr)  # nearest first (least negative)
    hint = data["spot_price_hint"]
    deviation = (
        hint is not None
        and ref_close > 0
        and abs(float(hint) - ref_close) / ref_close > _SPOT_DEVIATION_FRAC
    )
    if deviation:
        notes.append(
            f"external: spot hint deviates ({data['source']} {data['panel']})"
            " — check symbol/axis read"
        )
    return ExternalSnapshot(
        source=str(data["source"]),
        panel=str(data["panel"]),
        window=data["window"],
        scope=data["scope"],
        captured_at_ms=int(data["captured_at_ms"]),
        age_hours=(as_of_ms - int(data["captured_at_ms"])) / _MS_PER_HOUR,
        spot_price_hint=None if hint is None else float(hint),
        spot_hint_deviation=bool(deviation),
        clusters_above=above[:max_rows_per_side],
        clusters_below=below[:max_rows_per_side],
    )


def load_external_state(
    dir_path: Path,
    symbol: str,
    ref_close: float,
    atr14: float,
    as_of_ms: int,
    allowed_sources: tuple[str, ...],
    max_age_hours: float,
    max_rows_per_side: int,
) -> tuple[ExternalState | None, list[str]]:
    """(state, notes) for one symbol. Notes are UNPREFIXED (bundle adds it).

    Latest fresh snapshot per (source, panel, window); absent dir or no
    files for this symbol -> (None, []) silently (feature is opt-in by
    usage); all-stale -> the re-drop note; malformed/unknown-source files
    -> per-file notes, never exceptions.
    """
    if not dir_path.is_dir():
        return None, []
    notes: list[str] = []
    fresh: dict[tuple[str, str, str], dict[str, Any]] = {}
    stale_latest_ms: int | None = None
    for path in sorted(dir_path.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            notes.append(f"external: unreadable {path.name} ({exc})")
            continue
        problems = validate_snapshot_dict(data)
        if problems:
            notes.append(f"external: invalid {path.name} ({problems[0]})")
            continue
        if data["symbol"] != symbol:
            continue  # another panel's file — not an error
        if data["source"] not in allowed_sources:
            notes.append(f"external: unknown source {data['source']!r} in {path.name}")
            continue
        captured = int(data["captured_at_ms"])
        if captured > as_of_ms:
            notes.append(f"external: {path.name} captured in the future — skipped")
            continue
        if (as_of_ms - captured) / _MS_PER_HOUR > max_age_hours:
            if stale_latest_ms is None or captured > stale_latest_ms:
                stale_latest_ms = captured
            continue
        key = (str(data["source"]), str(data["panel"]), str(data["window"] or ""))
        kept = fresh.get(key)
        if kept is None or captured > int(kept["captured_at_ms"]):
            fresh[key] = data
    if not fresh:
        if stale_latest_ms is not None:
            age_days = (as_of_ms - stale_latest_ms) / _MS_PER_HOUR / 24.0
            notes.append(
                f"external context stale (latest {age_days:.1f}d) — re-drop screenshots"
            )
        return None, notes
    if atr14 <= 0:
        notes.append("external: ATR unavailable — block omitted")
        return None, notes
    snapshots = [
        _build_snapshot(
            fresh[key], ref_close, atr14, as_of_ms, max_rows_per_side, notes
        )
        for key in sorted(fresh)
    ]
    return ExternalState(snapshots=snapshots), notes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_brief_external.py -v && make lint-py && make typecheck`
Expected: PASS, clean

- [ ] **Step 5: Commit**

```bash
git add analytics/brief/external.py tests/test_brief_external.py
git commit -m "feat(brief): M3 external-context loader — latest per (source, panel, window)"
```

---

### Task 4: `BriefConfig` fields + bundle wiring

**Files:**

- Modify: `analytics/brief/config.py` (4 fields appended to `BriefConfig`)
- Modify: `analytics/brief/bundle.py` (import + one call in `_compute_panel` + one kwarg in `SymbolPanel(...)`)
- Test: `tests/test_brief_external.py` (extend)

**Interfaces:**

- Consumes: `load_external_state` (Task 3).
- Produces: `BriefConfig.external_dir: Path`, `.external_max_age_hours: float`, `.external_max_rows_per_side: int`, `.external_allowed_sources: tuple[str, ...]`; panels computed by `compute_brief` now carry `.external`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_brief_external.py`:

```python
import duckdb

from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig
from tests._brief_fixtures import DAY_MS, START_MS, make_conn, seed_symbol

BUNDLE_AS_OF = START_MS + 60 * DAY_MS


def _bundle_cfg(tmp_path: Path, external_dir: Path) -> BriefConfig:
    return BriefConfig(
        symbols=("BTCUSDT",),
        as_of_ms=BUNDLE_AS_OF,
        ledger_path=tmp_path / "absent.jsonl",
        priors_path=tmp_path / "absent.json",
        external_dir=external_dir,
    )


def test_bundle_wires_external_block(tmp_path: Path) -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bare = compute_brief(conn, _bundle_cfg(tmp_path, tmp_path / "missing"))
    panel = bare.panels[0]
    assert panel.error is None and panel.external is None
    ref = panel.ref_close
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()
    snap = _valid_snapshot(
        captured_at_ms=BUNDLE_AS_OF - HOUR_MS,
        ingested_at_ms=BUNDLE_AS_OF,
        spot_price_hint=ref,
        clusters=[
            {
                "price_lo": ref * 1.04,
                "price_hi": ref * 1.05,
                "kind": "liq",
                "intensity": "high",
                "label": "",
            },
            {
                "price_lo": ref * 0.95,
                "price_hi": ref * 0.96,
                "kind": "liq",
                "intensity": "med",
                "label": "",
            },
        ],
    )
    (ext_dir / "coinglass_liq_heatmap_BTCUSDT.json").write_text(json.dumps(snap))
    wired = compute_brief(conn, _bundle_cfg(tmp_path, ext_dir))
    ext = wired.panels[0].external
    assert ext is not None
    assert len(ext.snapshots) == 1
    assert len(ext.snapshots[0].clusters_above) == 1
    assert len(ext.snapshots[0].clusters_below) == 1
    conn.close()
```

Note: `_valid_snapshot` uses `AS_OF` timestamps by default — the overrides above re-anchor `captured_at_ms`/`ingested_at_ms` to `BUNDLE_AS_OF`; that is deliberate and required (freshness is judged against the bundle's `as_of_ms`).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_brief_external.py -v`
Expected: FAIL — `TypeError: BriefConfig.__init__() got an unexpected keyword argument 'external_dir'`

- [ ] **Step 3: Write minimal implementation**

In `analytics/brief/config.py`, append to `BriefConfig` after `priors_path`:

```python
    external_dir: Path = Path("docs/plans/external-context")
    external_max_age_hours: float = 48.0
    external_max_rows_per_side: int = 3
    external_allowed_sources: tuple[str, ...] = ("coinglass", "mmt")
```

In `analytics/brief/bundle.py`: add the import

```python
from analytics.brief.external import load_external_state
```

then in `_compute_panel`, AFTER the `sessions, sess_notes = build_session_state(...)` block and its `notes.extend(...)` line, insert:

```python
    external, ext_notes = load_external_state(
        dir_path=cfg.external_dir,
        symbol=symbol,
        ref_close=ref_close,
        atr14=atr,
        as_of_ms=as_of,
        allowed_sources=cfg.external_allowed_sources,
        max_age_hours=cfg.external_max_age_hours,
        max_rows_per_side=cfg.external_max_rows_per_side,
    )
    notes.extend(f"{symbol}: {n}" for n in ext_notes)
```

and add `external=external,` to the `SymbolPanel(...)` constructor call (place it after `sessions=sessions,`).

- [ ] **Step 4: Run the full suite**

Run: `poetry run pytest tests/test_brief_external.py -v && make test`
Expected: PASS. If `tests/test_web_brief.py` or `tests/test_brief_bundle.py` assert exact JSON key sets, the new `external` key (value `null`) appears — update those assertions additively; that is the only acceptable existing-test change.

- [ ] **Step 5: Commit**

```bash
git add analytics/brief/config.py analytics/brief/bundle.py tests/test_brief_external.py
git commit -m "feat(brief): M3 config knobs + bundle wiring for SymbolPanel.external"
```

---

### Task 5: Renderer `_external_lines`

**Files:**

- Modify: `analytics/brief/render.py`
- Test: `tests/test_brief_render.py` (extend)

**Interfaces:**

- Consumes: Task 1 types; existing `fmt_price` (≥1000 → `"66,000"`), `fmt_dist` (`"+1.83"`).
- Produces: `_external_lines(state: ExternalState | None) -> list[str]`; `_panel_lines` emits the block after `_session_lines`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief_render.py` (extend the existing `from analytics.brief.render import (...)` with `_external_lines` and the `from analytics.brief.types import (...)` with `ExternalClusterRow, ExternalSnapshot, ExternalState`):

```python
def _ext_snapshot(**overrides: object) -> ExternalSnapshot:
    base: dict[str, object] = {
        "source": "coinglass",
        "panel": "liq_heatmap",
        "window": "24h",
        "scope": "pair",
        "captured_at_ms": 0,
        "age_hours": 14.4,
        "spot_price_hint": None,
        "spot_hint_deviation": False,
        "clusters_above": [
            ExternalClusterRow(
                price_lo=66_000.0,
                price_hi=66_200.0,
                kind="liq",
                intensity="high",
                label="",
                dist_atr=1.83,
            )
        ],
        "clusters_below": [
            ExternalClusterRow(
                price_lo=61_200.0,
                price_hi=61_500.0,
                kind="liq",
                intensity="med",
                label="100x-heavy",
                dist_atr=-1.62,
            )
        ],
    }
    base.update(overrides)
    return ExternalSnapshot(**base)  # type: ignore[arg-type]


def test_external_lines_format() -> None:
    lines = _external_lines(ExternalState(snapshots=[_ext_snapshot()]))
    assert lines == [
        "External coinglass liq (24h) · 14h · "
        "above 66,000–66,200 HIGH (+1.83) · "
        "below 61,200–61,500 med 100x-heavy (-1.62)"
    ]


def test_external_lines_variants() -> None:
    assert _external_lines(None) == []
    agg = _ext_snapshot(
        source="mmt",
        panel="liq_map",
        window=None,
        scope="agg",
        spot_hint_deviation=True,
        clusters_above=[],
    )
    lines = _external_lines(ExternalState(snapshots=[_ext_snapshot(), agg]))
    assert len(lines) == 2
    assert lines[1].startswith(" " * 9 + "mmt map agg · 14h ⚠spot · above none")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_brief_render.py -v`
Expected: FAIL — `ImportError: cannot import name '_external_lines'`

- [ ] **Step 3: Write the implementation**

In `analytics/brief/render.py`: extend the `from analytics.brief.types import (...)` block with `ExternalClusterRow, ExternalSnapshot, ExternalState`. Insert after `_session_lines`:

```python
_PANEL_SHORT = {"liq_heatmap": "liq", "book_heatmap": "book", "liq_map": "map"}


def _cluster_bit(row: ExternalClusterRow) -> str:
    lo, hi = fmt_price(row.price_lo), fmt_price(row.price_hi)
    band = lo if lo == hi else f"{lo}–{hi}"
    strength = "HIGH" if row.intensity == "high" else row.intensity
    label_bit = f" {row.label}" if row.label else ""
    return f"{band} {strength}{label_bit} ({fmt_dist(row.dist_atr)})"


def _external_snapshot_bit(snap: ExternalSnapshot) -> str:
    win = f" ({snap.window})" if snap.window else ""
    scope_bit = " agg" if snap.scope == "agg" else ""
    dev = " ⚠spot" if snap.spot_hint_deviation else ""
    above = ", ".join(_cluster_bit(r) for r in snap.clusters_above) or "none"
    below = ", ".join(_cluster_bit(r) for r in snap.clusters_below) or "none"
    return (
        f"{snap.source} {_PANEL_SHORT.get(snap.panel, snap.panel)}{win}{scope_bit}"
        f" · {snap.age_hours:.0f}h{dev} · above {above} · below {below}"
    )


def _external_lines(state: ExternalState | None) -> list[str]:
    if state is None or not state.snapshots:
        return []
    bits = [_external_snapshot_bit(s) for s in state.snapshots]
    return [f"{'External':<9}{bits[0]}"] + [f"{'':9}{b}" for b in bits[1:]]
```

In `_panel_lines`, directly after `lines.extend(_session_lines(panel.sessions))`, add:

```python
    lines.extend(_external_lines(panel.external))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_brief_render.py -v && make test`
Expected: PASS including the existing end-to-end byte-stability test (no external files → block absent → unchanged output).

- [ ] **Step 5: Commit**

```bash
git add analytics/brief/render.py tests/test_brief_render.py
git commit -m "feat(brief): M3 External block renderer after Sessions"
```

---

### Task 6: `tools/chart_drops.py` — parse, ledger, scan

**Files:**

- Create: `tools/chart_drops.py`
- Create: `tests/test_chart_drops.py`

**Interfaces:**

- Consumes: `validate_snapshot_dict` from `analytics.brief.external` (imported now, used by Task 7's writer).
- Produces: `PendingDrop(path, sha256, source, symbol, captured_at_ms, ts_from_filename)` frozen dataclass; `parse_drop_filename(name, allowed_sources) -> tuple[str, str, int | None] | None`; `file_sha256(path) -> str`; `load_ledger(path) -> dict[str, dict[str, Any]]`; `mark_processed(path, sha256, filename, outcome, ingested_at_ms) -> None`; `scan_drops(drop_dir, ledger_path, allowed_sources) -> tuple[list[PendingDrop], list[str]]`; constants `DEFAULT_DROP_DIR`, `DEFAULT_OUT_DIR`, `DEFAULT_LEDGER`, `ALLOWED_SOURCES`, `OUTCOMES`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_chart_drops.py`:

```python
"""tools/chart_drops — filename parse, sha256 ledger, scan, write, CLI."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.chart_drops import (
    file_sha256,
    load_ledger,
    mark_processed,
    parse_drop_filename,
    scan_drops,
)

MYT = timezone(timedelta(hours=8))


def _myt_ms(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=MYT).timestamp() * 1000)


def test_parse_full_name() -> None:
    parsed = parse_drop_filename("coinglass_BTCUSDT_20260714-0930.png")
    assert parsed == ("coinglass", "BTCUSDT", _myt_ms(2026, 7, 14, 9, 30))


def test_parse_date_only_and_no_ts() -> None:
    assert parse_drop_filename("mmt_ETHUSDT_20260714.jpg") == (
        "mmt",
        "ETHUSDT",
        _myt_ms(2026, 7, 14),
    )
    assert parse_drop_filename("mmt_ETHUSDT.jpeg") == ("mmt", "ETHUSDT", None)


def test_parse_rejects_bad_names() -> None:
    assert parse_drop_filename("hyblock_BTCUSDT.png") is None  # unknown source
    assert parse_drop_filename("coinglass_btcusdt.png") is None  # lowercase symbol
    assert parse_drop_filename("coinglass_BTCUSDT_2026.png") is None  # bad ts
    assert parse_drop_filename("coinglass_BTCUSDT.gif") is None  # bad ext
    assert parse_drop_filename("random.png") is None


def test_ledger_roundtrip(tmp_path: Path) -> None:
    ledger = tmp_path / "sub" / "processed.json"
    assert load_ledger(ledger) == {}
    mark_processed(ledger, "abc123", "a.png", "written", 1_000)
    mark_processed(ledger, "def456", "b.png", "dropped", 2_000)
    data = load_ledger(ledger)
    assert data["abc123"]["outcome"] == "written"
    assert data["def456"]["ingested_at_ms"] == 2_000


def test_scan_drops(tmp_path: Path) -> None:
    drop = tmp_path / "drops"
    drop.mkdir()
    (drop / "coinglass_BTCUSDT_20260714-0930.png").write_bytes(b"img-a")
    (drop / "weird name.png").write_bytes(b"img-b")
    (drop / "notes.txt").write_text("not an image")
    done = drop / "done"
    done.mkdir()
    (done / "coinglass_ETHUSDT_20260713.png").write_bytes(b"img-old")
    ledger = tmp_path / "processed.json"
    pending, unparseable = scan_drops(drop, ledger)
    assert unparseable == ["weird name.png"]
    assert len(pending) == 1
    item = pending[0]
    assert item.source == "coinglass" and item.symbol == "BTCUSDT"
    assert item.ts_from_filename is True
    assert item.sha256 == file_sha256(drop / "coinglass_BTCUSDT_20260714-0930.png")
    # ledger-marked files disappear from the next scan
    mark_processed(
        ledger, item.sha256, "coinglass_BTCUSDT_20260714-0930.png", "written", 1
    )
    pending2, _ = scan_drops(drop, ledger)
    assert pending2 == []


def test_scan_mtime_fallback(tmp_path: Path) -> None:
    drop = tmp_path / "drops"
    drop.mkdir()
    img = drop / "mmt_SOLUSDT.png"
    img.write_bytes(b"img")
    pending, _ = scan_drops(drop, tmp_path / "ledger.json")
    assert pending[0].ts_from_filename is False
    assert pending[0].captured_at_ms == int(img.stat().st_mtime * 1000)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_chart_drops.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.chart_drops'`

- [ ] **Step 3: Write the implementation**

Create `tools/chart_drops.py`:

```python
"""Chart-drop helper for /ingest-charts (M3 external context).

Writer-side logic the skill shells out to: filename parsing, the sha256
processed-ledger, schema-validated snapshot writes, and moving handled
images to done/. The read side lives in analytics/brief/external.py —
this module imports its validator so writer and reader share one contract.

Run via: PYTHONPATH=. poetry run python tools/chart_drops.py scan|write|mark
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_DROP_DIR = Path("docs/plans/chart-drops")
DEFAULT_OUT_DIR = Path("docs/plans/external-context")
DEFAULT_LEDGER = Path(".cache/chart-drops/processed.json")
ALLOWED_SOURCES = ("coinglass", "mmt")
OUTCOMES = ("written", "skipped", "dropped")
_MYT = timezone(timedelta(hours=8))
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")
_NAME_RE = re.compile(
    r"^(?P<source>[a-z0-9]+)_(?P<symbol>[A-Z0-9]+)"
    r"(?:_(?P<ts>\d{8}(?:-\d{4})?))?\.(?:png|jpg|jpeg)$"
)


@dataclass(frozen=True)
class PendingDrop:
    path: str
    sha256: str
    source: str
    symbol: str
    captured_at_ms: int
    ts_from_filename: bool


def parse_drop_filename(
    name: str, allowed_sources: tuple[str, ...] = ALLOWED_SOURCES
) -> tuple[str, str, int | None] | None:
    """(source, symbol, captured_at_ms | None) or None if unparseable.

    Timestamp is the operator's wall clock — MYT (fixed UTC+8).
    """
    match = _NAME_RE.match(name)
    if match is None or match.group("source") not in allowed_sources:
        return None
    ts_ms: int | None = None
    ts = match.group("ts")
    if ts is not None:
        fmt = "%Y%m%d-%H%M" if "-" in ts else "%Y%m%d"
        try:
            parsed = datetime.strptime(ts, fmt).replace(tzinfo=_MYT)
        except ValueError:
            return None
        ts_ms = int(parsed.timestamp() * 1000)
    return match.group("source"), match.group("symbol"), ts_ms


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_ledger(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    return data if isinstance(data, dict) else {}


def mark_processed(
    path: Path, sha256: str, filename: str, outcome: str, ingested_at_ms: int
) -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome {outcome!r} not in {OUTCOMES}")
    ledger = load_ledger(path)
    ledger[sha256] = {
        "filename": filename,
        "ingested_at_ms": ingested_at_ms,
        "outcome": outcome,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2) + "\n")


def scan_drops(
    drop_dir: Path = DEFAULT_DROP_DIR,
    ledger_path: Path = DEFAULT_LEDGER,
    allowed_sources: tuple[str, ...] = ALLOWED_SOURCES,
) -> tuple[list[PendingDrop], list[str]]:
    """(pending, unparseable_names). done/ and non-images are ignored."""
    if not drop_dir.is_dir():
        return [], []
    ledger = load_ledger(ledger_path)
    pending: list[PendingDrop] = []
    unparseable: list[str] = []
    for path in sorted(drop_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        sha = file_sha256(path)
        if sha in ledger:
            continue
        parsed = parse_drop_filename(path.name, allowed_sources)
        if parsed is None:
            unparseable.append(path.name)
            continue
        source, symbol, ts_ms = parsed
        captured = ts_ms if ts_ms is not None else int(path.stat().st_mtime * 1000)
        pending.append(
            PendingDrop(
                path=str(path),
                sha256=sha,
                source=source,
                symbol=symbol,
                captured_at_ms=captured,
                ts_from_filename=ts_ms is not None,
            )
        )
    return pending, unparseable
```

(`argparse`, `time`, `asdict`, and the `validate_snapshot_dict` import arrive with Task 7, which uses them — this task's `make lint-py` stays clean. The unused-constant `DEFAULT_OUT_DIR` is fine; ruff only flags unused imports.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_chart_drops.py -v && make lint-py && make typecheck`
Expected: PASS, clean

- [ ] **Step 5: Commit**

```bash
git add tools/chart_drops.py tests/test_chart_drops.py
git commit -m "feat(tools): chart_drops scan — filename parse + sha256 dedup ledger"
```

---

### Task 7: `chart_drops` write / mark / move + CLI

**Files:**

- Modify: `tools/chart_drops.py`
- Test: `tests/test_chart_drops.py` (extend)

**Interfaces:**

- Consumes: Task 6 functions + `validate_snapshot_dict`.
- Produces: `snapshot_filename(data) -> str` (`<source>_<panel>[_<window>]_<SYMBOL>_<YYYYMMDD-HHMM MYT>.json`); `write_snapshot(snapshot, out_dir) -> Path` (raises `ValueError` on invalid); `move_to_done(image_path) -> Path`; `main(argv) -> int` with subcommands `scan` / `write --json-file --image` / `mark --image --outcome skipped|dropped`. The `/ingest-charts` skill (Task 8) drives exactly these three subcommands.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chart_drops.py`:

```python
from typing import Any

from tools.chart_drops import main, move_to_done, snapshot_filename, write_snapshot


def _snapshot(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema": "external-levels-v1",
        "source": "coinglass",
        "symbol": "BTCUSDT",
        "panel": "liq_heatmap",
        "window": "24h",
        "scope": "pair",
        "captured_at_ms": _myt_ms(2026, 7, 14, 9, 30),
        "ingested_at_ms": _myt_ms(2026, 7, 14, 11, 0),
        "verified": True,
        "spot_price_hint": 62_452.0,
        "clusters": [
            {
                "price_lo": 63_200.0,
                "price_hi": 63_400.0,
                "kind": "liq",
                "intensity": "high",
                "label": "",
            }
        ],
        "notes": "",
    }
    data.update(overrides)
    return data


def test_snapshot_filename_includes_window() -> None:
    assert (
        snapshot_filename(_snapshot())
        == "coinglass_liq_heatmap_24h_BTCUSDT_20260714-0930.json"
    )
    assert (
        snapshot_filename(_snapshot(window=None, panel="liq_map"))
        == "coinglass_liq_map_BTCUSDT_20260714-0930.json"
    )


def test_write_snapshot_validates(tmp_path: Path) -> None:
    path = write_snapshot(_snapshot(), tmp_path / "out")
    assert path.is_file()
    assert json.loads(path.read_text())["verified"] is True
    try:
        write_snapshot(_snapshot(panel="oi_chart"), tmp_path / "out")
    except ValueError as exc:
        assert "invalid snapshot" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_move_to_done_collision(tmp_path: Path) -> None:
    img = tmp_path / "coinglass_BTCUSDT.png"
    img.write_bytes(b"one")
    moved = move_to_done(img)
    assert moved == tmp_path / "done" / "coinglass_BTCUSDT.png"
    img2 = tmp_path / "coinglass_BTCUSDT.png"
    img2.write_bytes(b"two")
    moved2 = move_to_done(img2)
    assert moved2 == tmp_path / "done" / "coinglass_BTCUSDT_1.png"


def test_cli_scan_write_mark(tmp_path: Path, capsys: Any) -> None:
    drop = tmp_path / "drops"
    drop.mkdir()
    img = drop / "coinglass_BTCUSDT_20260714-0930.png"
    img.write_bytes(b"img")
    ledger = tmp_path / "ledger.json"
    out_dir = tmp_path / "ext"
    assert main(["scan", "--drop-dir", str(drop), "--ledger", str(ledger)]) == 0
    scanned = json.loads(capsys.readouterr().out)
    assert scanned["unparseable"] == []
    assert scanned["pending"][0]["symbol"] == "BTCUSDT"
    json_file = tmp_path / "approved.json"
    json_file.write_text(json.dumps(_snapshot()))
    assert (
        main(
            [
                "write",
                "--json-file",
                str(json_file),
                "--image",
                str(img),
                "--out-dir",
                str(out_dir),
                "--ledger",
                str(ledger),
            ]
        )
        == 0
    )
    assert (out_dir / "coinglass_liq_heatmap_24h_BTCUSDT_20260714-0930.json").is_file()
    assert not img.exists() and (drop / "done" / img.name).is_file()
    assert list(load_ledger(ledger).values())[0]["outcome"] == "written"
    img_b = drop / "mmt_ETHUSDT.png"
    img_b.write_bytes(b"other")
    assert (
        main(
            [
                "mark",
                "--image",
                str(img_b),
                "--outcome",
                "dropped",
                "--ledger",
                str(ledger),
            ]
        )
        == 0
    )
    outcomes = {v["filename"]: v["outcome"] for v in load_ledger(ledger).values()}
    assert outcomes["mmt_ETHUSDT.png"] == "dropped"
    assert (drop / "done" / "mmt_ETHUSDT.png").is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_chart_drops.py -v`
Expected: FAIL — `ImportError: cannot import name 'main'`

- [ ] **Step 3: Write the implementation**

Add to `tools/chart_drops.py`'s import block:

```python
import argparse
import time
from dataclasses import asdict, dataclass

from analytics.brief.external import validate_snapshot_dict
```

(i.e. extend the existing `from dataclasses import dataclass` to also import `asdict`.) Then append:

```python
def snapshot_filename(data: dict[str, Any]) -> str:
    ts = datetime.fromtimestamp(data["captured_at_ms"] / 1000, tz=_MYT)
    window = f"_{data['window']}" if data["window"] else ""
    return (
        f"{data['source']}_{data['panel']}{window}_{data['symbol']}"
        f"_{ts.strftime('%Y%m%d-%H%M')}.json"
    )


def write_snapshot(snapshot: dict[str, Any], out_dir: Path = DEFAULT_OUT_DIR) -> Path:
    problems = validate_snapshot_dict(snapshot)
    if problems:
        raise ValueError("invalid snapshot: " + "; ".join(problems))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / snapshot_filename(snapshot)
    path.write_text(json.dumps(snapshot, indent=2) + "\n")
    return path


def move_to_done(image_path: Path) -> Path:
    done = image_path.parent / "done"
    done.mkdir(exist_ok=True)
    target = done / image_path.name
    counter = 1
    while target.exists():
        target = done / f"{image_path.stem}_{counter}{image_path.suffix}"
        counter += 1
    image_path.rename(target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Chart-drop helper for /ingest-charts (scan / write / mark)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_scan = sub.add_parser("scan", help="List unprocessed drops as JSON")
    p_scan.add_argument("--drop-dir", type=Path, default=DEFAULT_DROP_DIR)
    p_scan.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    p_write = sub.add_parser("write", help="Validate + write an approved snapshot")
    p_write.add_argument("--json-file", type=Path, required=True)
    p_write.add_argument("--image", type=Path, required=True)
    p_write.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p_write.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    p_mark = sub.add_parser("mark", help="Record a skipped/dropped image")
    p_mark.add_argument("--image", type=Path, required=True)
    p_mark.add_argument("--outcome", choices=["skipped", "dropped"], required=True)
    p_mark.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args(argv)
    now_ms = int(time.time() * 1000)
    if args.cmd == "scan":
        pending, unparseable = scan_drops(args.drop_dir, args.ledger)
        print(
            json.dumps(
                {"pending": [asdict(p) for p in pending], "unparseable": unparseable},
                indent=2,
            )
        )
        return 0
    if args.cmd == "write":
        snapshot = json.loads(args.json_file.read_text())
        path = write_snapshot(snapshot, args.out_dir)
        mark_processed(
            args.ledger, file_sha256(args.image), args.image.name, "written", now_ms
        )
        moved = move_to_done(args.image)
        print(json.dumps({"written": str(path), "image_moved_to": str(moved)}))
        return 0
    mark_processed(
        args.ledger, file_sha256(args.image), args.image.name, args.outcome, now_ms
    )
    moved = move_to_done(args.image)
    print(json.dumps({"marked": args.outcome, "image_moved_to": str(moved)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_chart_drops.py -v && make lint-py && make typecheck`
Expected: PASS, clean

- [ ] **Step 5: Commit**

```bash
git add tools/chart_drops.py tests/test_chart_drops.py
git commit -m "feat(tools): chart_drops write/mark/move + CLI for /ingest-charts"
```

---

### Task 8: `/ingest-charts` skill

**Files:**

- Create: `.claude/skills/ingest-charts/SKILL.md`

**Interfaces:**

- Consumes: `tools/chart_drops.py` CLI (`scan` / `write` / `mark`, Task 7); the snapshot contract (Task 2).
- Produces: the operator-facing workflow. No CI test (same as `/ingest-x`); `.claude/` is excluded from markdownlint.

- [ ] **Step 1: Write the skill file**

Create `.claude/skills/ingest-charts/SKILL.md` with EXACTLY this content:

````markdown
---
name: ingest-charts
description: >
  Ingest operator-dropped Coinglass / MMT heatmap + liquidation-map
  screenshots from docs/plans/chart-drops/ into verified external-context
  JSON for the daily Brief (M3). Scans via tools/chart_drops.py (sha256
  dedup ledger), vision-extracts each image in a per-image sonnet subagent
  (image bytes never enter main context), presents ONE consolidated review
  digest for the whole batch, and writes docs/plans/external-context/*.json
  ONLY after the operator approves — verified:true is the only on-disk
  state. Invoke when the user says "/ingest-charts", "ingest my chart
  drops", or has dropped new heatmap screenshots. Spec:
  docs/superpowers/specs/2026-07-14-m3-external-context-design.md.
---

# /ingest-charts — chart drops → verified external-context JSON

Advisory data path only. Never write a stream file before the operator
approves. Never `git add` anything here — `docs/plans/` and `.cache/` are
gitignored.

## 1. Scan

```bash
PYTHONPATH=. poetry run python tools/chart_drops.py scan
```

- `pending` empty and `unparseable` empty → report "no new drops" and stop.
- `unparseable` non-empty → list the names and ask the operator to rename to
  `<source>_<SYMBOL>[_<YYYYMMDD[-HHMM]>].png|.jpg|.jpeg` (source ∈
  coinglass|mmt, MYT timestamp). Do NOT guess. Continue with `pending`.

## 2. Extract (one sonnet subagent per pending image)

Dispatch each image to a **sonnet** subagent (Agent tool). The prompt is
self-contained — no repo/SoT reads. Template (fill `<path>`, `<source>`,
`<symbol>`):

```text
Read the image file at <path> (one Coinglass/MMT chart screenshot) and
return ONLY a JSON object — no prose, no markdown fence.

Extractable panel types (anything else => status "skip"):
- liq_heatmap: time×price heatmap; bright horizontal bands = liquidation
  clusters. Read band price ranges against the y-axis gridlines.
- book_heatmap: same shape but resting limit-order density.
- liq_map: bar chart with PRICE on the x-axis; bars = liquidation leverage
  at that price; usually prints "Current Price:<n>" as text.

Rules:
- Bands, not points: give price_lo/price_hi rounded to the visible axis
  granularity. Max 10 clusters, highest-confidence first. Skip faint noise.
- Prefer PRINTED TEXT over axis interpolation: "Current Price:" labels and
  tapped-band tooltips are high-confidence; gridline reads are approximate.
- intensity: high = brightest/tallest, med = clear, low = faint but real.
- liq_map: cluster the tallest bar groups per side of current price; when
  one leverage tier dominates a cluster, note it in label (e.g.
  "100x-heavy").
- window: the selected timeframe button if visible ("12h","24h","48h",
  "3d","1w","1d",...), else null.
- scope: "pair" for a single-exchange pair view (e.g. Binance BTCUSDT),
  "agg" for aggregated Symbol/Exchange views, else null.
- Filename metadata is authoritative: source=<source>, symbol=<symbol>.
  If the chart clearly shows a DIFFERENT asset, set symbol_mismatch=true.

Return exactly:
{"status":"ok"|"skip","skip_reason":null|"<why>",
 "panel":"liq_heatmap"|"book_heatmap"|"liq_map"|null,
 "window":"24h"|null,"scope":"pair"|"agg"|null,
 "symbol_mismatch":false,"spot_price_hint":62452.0|null,
 "spot_source":"printed"|"axis"|null,
 "clusters":[{"price_lo":63200,"price_hi":63400,"kind":"liq"|"book",
              "intensity":"high"|"med"|"low","label":""}],
 "confidence":"high"|"med"|"low"}
```

## 3. Review digest (ONE for the whole batch — the human-verify gate)

Present a table per image: file · source · symbol · panel · window · scope ·
capture age · spot hint (+source) · clusters (band, kind, intensity, label)
· flags (symbol_mismatch, skip_reason, low confidence, spot hint far from
recent price). Ask the operator per image: **approve / correct / drop**.
Corrections are applied to the cluster list / fields before writing and
summarized in the snapshot's `notes` field. NOTHING is written before this
gate.

## 4. Write (approved images only)

For each approved image build the final snapshot dict:

- `schema` "external-levels-v1"; `source`+`symbol` from the filename;
  `panel`/`window`/`scope`/`spot_price_hint`/`clusters` from the extraction
  after operator corrections; `captured_at_ms` from the scan output;
  `ingested_at_ms` = now (ms); `verified` true; `notes` = correction
  summary or "".

Save it to a scratchpad temp file, then:

```bash
PYTHONPATH=. poetry run python tools/chart_drops.py write \
  --json-file <tmp>.json --image <pending path>
```

For skipped panels (subagent status "skip") and operator-dropped images:

```bash
PYTHONPATH=. poetry run python tools/chart_drops.py mark \
  --image <pending path> --outcome skipped|dropped
```

Every outcome moves the image to `docs/plans/chart-drops/done/` and records
the sha256 in `.cache/chart-drops/processed.json` so re-runs are no-ops.

## 5. Report

Summarize: written snapshots (paths), skipped (reasons), dropped,
unparseable-awaiting-rename. Remind: the Brief picks these up on its next
run (48h freshness window); daily capture protocol = Heatmap 24h + Map 1d
per symbol, Model 1, consistent threshold (spec "Capture protocol"
section).
````

- [ ] **Step 2: Verify the skill file**

Run: `head -20 .claude/skills/ingest-charts/SKILL.md`
Expected: frontmatter with `name: ingest-charts` renders; file saved at the exact path.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/ingest-charts/SKILL.md
git commit -m "feat(skill): /ingest-charts — chart-drop vision ingest with review gate"
```

---

### Task 9: Brief.svelte External block

**Files:**

- Modify: `web/ui/src/pages/Brief.svelte` (one block after the `{#if panel.sessions}` block, ~line 348; helpers next to the existing `fmtPrice`/`fmtDist` helpers)

**Interfaces:**

- Consumes: `panel.external` from `GET /api/brief` (shape = `asdict(ExternalState)`: `{snapshots: [{source, panel, window, scope, age_hours, spot_hint_deviation, clusters_above: [{price_lo, price_hi, intensity, label, dist_atr}], clusters_below: [...]}]}`).
- Produces: UI parity with the terminal renderer. No new CSS classes — reuse the existing `sessions muted` / `sess-name` classes (standing rule: this is pattern-following, not new visual design; no `/frontend-design` divergence).

- [ ] **Step 1: Add script helpers**

In the `<script>` section of `Brief.svelte`, next to the existing formatting helpers (`fmtPrice`, `fmtDist`, `mytDow` are already defined there — reuse them, do not redefine):

```javascript
  const PANEL_SHORT = { liq_heatmap: "liq", book_heatmap: "book", liq_map: "map" };
  function panelShort(p) {
    return PANEL_SHORT[p] ?? p;
  }
  function extCluster(c) {
    const band =
      c.price_lo === c.price_hi
        ? fmtPrice(c.price_lo)
        : `${fmtPrice(c.price_lo)}–${fmtPrice(c.price_hi)}`;
    const strength = c.intensity === "high" ? "HIGH" : c.intensity;
    return `${band} ${strength}${c.label ? " " + c.label : ""} (${fmtDist(c.dist_atr)})`;
  }
  function extClusters(rows) {
    return rows.length ? rows.map(extCluster).join(", ") : "none";
  }
```

- [ ] **Step 2: Add the block**

Directly AFTER the closing `{/if}` of the `{#if panel.sessions}` block and BEFORE `<div class="ladder">`:

```svelte
            {#if panel.external}
              <div class="sessions muted">
                {#each panel.external.snapshots as snap}
                  <div>
                    <span class="sess-name">EXT</span>
                    {snap.source} {panelShort(snap.panel)}{snap.window ? ` (${snap.window})` : ""}{snap.scope === "agg" ? " agg" : ""}
                    · {Math.round(snap.age_hours)}h{snap.spot_hint_deviation ? " ⚠spot" : ""}
                    · above {extClusters(snap.clusters_above)}
                    · below {extClusters(snap.clusters_below)}
                  </div>
                {/each}
              </div>
            {/if}
```

- [ ] **Step 3: Build**

Run: `make web-build`
Expected: production bundle builds clean (no Svelte compile errors).

- [ ] **Step 4: Commit**

```bash
git add web/ui/src/pages/Brief.svelte
git commit -m "feat(ui): Brief External block — verified heatmap levels per symbol"
```

---

### Task 10: Docs sync + full Definition of Done

**Files:**

- Modify: `CLAUDE.md` (brief/ bullet in Project Structure; `tools/` list; Agent Skills table)
- Modify: `README.md` (brief feature section)

**Interfaces:**

- Consumes: everything above.
- Produces: docs in sync; all gates green.

- [ ] **Step 1: CLAUDE.md — brief/ bullet**

In the `analytics/` → `brief/` bullet, append after the M2 session-layer sentence:

```text
M3 external-context layer: `external.py` (pure conn-free reader — validates + loads operator-verified heatmap/liq-map snapshots from gitignored `docs/plans/external-context/*.json`, latest fresh per (source, panel, window), 48h staleness vs as-of, band-midpoint side split + ATR distances, spot-hint deviation flag) into additive `SymbolPanel.external` rendered after Sessions; absent dir = silent, stale/malformed/unknown-source → health notes; snapshots are written ONLY by the `/ingest-charts` review gate via `tools/chart_drops.py`.
```

- [ ] **Step 2: CLAUDE.md — tools/ list entry**

Add to the `tools/` section (after `x_route.py`):

```text
- `chart_drops.py` — writer-side helper for `/ingest-charts` (M3): drop-filename parse (`<source>_<SYMBOL>[_<MYT ts>]`), sha256 processed-ledger (`.cache/chart-drops/processed.json`), schema-validated snapshot writes (validator imported from `analytics/brief/external.py` — one shared contract), move-to-done. Run via `PYTHONPATH=. poetry run python tools/chart_drops.py scan|write|mark`.
```

- [ ] **Step 3: CLAUDE.md — skills table row**

Add after the `ingest-x` row:

```text
| `ingest-charts` | `/ingest-charts` | Ingest dropped Coinglass/MMT heatmap + liquidation-map screenshots (`docs/plans/chart-drops/`) → per-image sonnet vision → ONE review digest → verified external-context JSON for the Brief's External block; sha256 dedup, images move to `done/` | After dropping new chart screenshots (daily capture: Heatmap 24h + Map 1d per symbol) |
```

- [ ] **Step 4: README brief section**

In the README's brief/daily-brief feature description, add one sentence:

```text
The Brief can also surface operator-verified external levels (Coinglass/MMT liquidation heatmaps and maps, ingested from manual screenshots via `/ingest-charts`) as a per-symbol External block with snapshot age and ATR distances; snapshots older than 48h drop out with a health note.
```

- [ ] **Step 5: Full gate**

Run, in order, and report each result plainly:

```bash
make lint-py
make typecheck
make test
make test-regression
make lint-md
```

Expected: all green; regression goldens UNMOVED (this feature is additive-pure — if a golden moves, something is wrong; stop and investigate, do not regenerate).

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: M3 external-context — CLAUDE.md brief/tools/skills + README sync"
```

---

## Post-plan notes for the executor

- Branch: continue on `docs/m3-external-context` or a fresh `feat/m3-external-context` off it (operator's call at execution time).
- The smoke test (spec DoD) needs real screenshots + a Claude session and is NOT part of these tasks: drop a real Coinglass Heatmap-24h + Map-1d pair, run `/ingest-charts`, approve, then `make buibui-brief` must show both External lines with window + age.
- Out of scope (spec non-goals): capture automation, vendor APIs, annotated-chart extraction, confluence markers, screenshot serving in the web UI, F2 wiring, X timelines.
