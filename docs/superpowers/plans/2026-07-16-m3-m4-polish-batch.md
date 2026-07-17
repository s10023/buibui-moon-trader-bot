# M3+M4 Polish Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every non-gated review minor from the M3 (#484) and M4
(#486/#487) final reviews plus the two operator-approved venue items —
test isolation, parity/coverage tests, `.PNG` extension, note-dup
suppression, band-collapse basis parity, External legend completeness,
and an additive `venue` field with a widened snapshot dedup key.

**Architecture:** Two PRs. PR-1 (`chore/m3-m4-polish-tests`) is
tests-plus-tiny-fixes: no schema change, brief render byte-stable for all
existing data. PR-2 (`feat/external-venue`) is the one contract change:
an optional `venue` key in `external-levels-v1` (additive — absent and
null both mean unspecified, existing snapshots stay valid), threaded
reader → writer → API model → TS → UI, with the dedup key widened from
`(source, panel, window)` to `(source, venue, scope, panel, window)`.

**Tech Stack:** Python 3.11 (mypy strict, ruff), pytest, FastAPI +
Pydantic v2, Svelte 5 + TS, DuckDB (`:memory:` in tests).

## Global Constraints

- After any Python change: `make lint-py` ✓, `make typecheck` ✓,
  `make test` green, `make test-regression` goldens UNMOVED (nothing in
  this batch touches the backtest pipeline; a moved golden = a bug).
- After any Svelte/TS change: `make web-check` clean (pre-existing
  Backtest.svelte CSS warning is the only allowed noise) + `make web-build`.
- NEVER touch `card/prompt.py`, `PROMPT_VERSION`, or the RUBRIC text —
  Task 8 adds test pins only. Rubric changes are gated card-v3 work.
- NEVER `git add` anything under `docs/plans/` or `.cache/` (gitignored
  operator data).
- All test DB access via `duckdb.connect(":memory:")` /
  `tests/_brief_fixtures.py` helpers — never the real `analytics.db`.
- Every new test constructor for `BriefConfig` goes through the Task 1
  `brief_cfg` factory (isolation by default).
- Conventional commits; branch names as given per PR section.
- Commit trailer on every commit:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

## PR-1 — `chore/m3-m4-polish-tests` (branch off main)

### Task 1: Shared isolated `brief_cfg` factory + render-test isolation

The #487 review found `tests/test_brief_render.py` has the same latent
isolation gap fixed in `test_brief_bundle.py` (Task 4b of the M4 run):
three `BriefConfig(...)` constructors use the DEFAULT `external_dir`
(`docs/plans/external-context` — the operator's REAL gitignored snapshot
dir). They stay green today only because the 2024 `AS_OF` future-skips
2026 captures. Fix by adding one shared factory and routing all test
constructors through it.

**Files:**

- Modify: `tests/_brief_fixtures.py` (add `brief_cfg` factory)
- Modify: `tests/test_brief_bundle.py:17-25` (`_cfg` delegates to factory)
- Modify: `tests/test_brief_render.py:72-78`, `:125-131`, `:197-203`
- Test: `tests/test_brief_fixtures_helpers.py` (new, tiny)

**Interfaces:**

- Produces: `brief_cfg(symbols: tuple[str, ...], as_of_ms: int,
  **overrides: Any) -> BriefConfig` in `tests/_brief_fixtures.py` —
  defaults `stats_days=60` and
  `external_dir=Path("tests/no-such-external-context")`; every later task
  that builds a test `BriefConfig` uses it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_brief_fixtures_helpers.py`:

```python
"""brief_cfg factory: isolation defaults every brief test relies on."""

from pathlib import Path

from tests._brief_fixtures import brief_cfg


def test_brief_cfg_isolates_external_dir_by_default() -> None:
    cfg = brief_cfg(("BTCUSDT",), 1_704_067_200_000)
    assert cfg.external_dir == Path("tests/no-such-external-context")
    assert not cfg.external_dir.exists()
    assert cfg.stats_days == 60


def test_brief_cfg_overrides_pass_through(tmp_path: Path) -> None:
    cfg = brief_cfg(
        ("BTCUSDT",),
        1_704_067_200_000,
        stats_days=90,
        external_dir=tmp_path,
        ledger_path=tmp_path / "calls.jsonl",
    )
    assert cfg.stats_days == 90
    assert cfg.external_dir == tmp_path
    assert cfg.ledger_path == tmp_path / "calls.jsonl"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/test_brief_fixtures_helpers.py -v`
Expected: FAIL — `ImportError: cannot import name 'brief_cfg'`

- [ ] **Step 3: Add the factory**

In `tests/_brief_fixtures.py` (add `Any` to the `typing` import and
`Path` to imports if absent; `BriefConfig` import from
`analytics.brief.config`):

```python
def brief_cfg(
    symbols: tuple[str, ...], as_of_ms: int, **overrides: Any
) -> BriefConfig:
    """BriefConfig for tests — external_dir isolated by default.

    Real operator snapshots live in the default docs/plans/external-context/
    and must never leak into the suite (the 2026-07-16 test_brief_bundle
    incident); route every test constructor through here.
    """
    overrides.setdefault("stats_days", 60)
    overrides.setdefault("external_dir", Path("tests/no-such-external-context"))
    return BriefConfig(symbols=symbols, as_of_ms=as_of_ms, **overrides)
```

- [ ] **Step 4: Route the four existing constructors through it**

`tests/test_brief_bundle.py:17-25` — `_cfg` becomes a one-liner (keep the
local name so the 10+ call sites don't churn):

```python
def _cfg(symbols: tuple[str, ...]) -> BriefConfig:
    return brief_cfg(symbols, AS_OF)
```

(import `brief_cfg` from `tests._brief_fixtures`; the `Path` import and
the old comment about isolation move into the factory docstring — delete
them here if now unused.)

`tests/test_brief_render.py` — three replacements (import `brief_cfg`
alongside the existing `_brief_fixtures` imports):

Line 72-78 (`_seeded_cfg`):

```python
    cfg = brief_cfg(
        ("BTCUSDT",),
        AS_OF,
        ledger_path=ledger,
        priors_path=tmp_path / "missing-priors.json",
    )
```

Line 125-131 (`_seeded_cfg_with_priors`):

```python
    cfg = brief_cfg(
        ("BTCUSDT",),
        AS_OF,
        ledger_path=ledger,
        priors_path=priors,
    )
```

Line 197-203 (`test_render_error_panel`):

```python
    cfg2 = brief_cfg(
        ("NODATAUSDT",),
        AS_OF,
        ledger_path=cfg.ledger_path,
        priors_path=cfg.priors_path,
    )
```

(`tests/test_brief_external.py::_bundle_cfg` already passes an explicit
`external_dir` — leave it; do NOT force it onto the factory.)

- [ ] **Step 5: Run the suites**

Run: `poetry run pytest tests/test_brief_fixtures_helpers.py tests/test_brief_bundle.py tests/test_brief_render.py tests/test_brief_external.py -q`
Expected: all PASS

- [ ] **Step 6: Lint/typecheck + commit**

```bash
make lint-py && make typecheck
git add tests/_brief_fixtures.py tests/test_brief_fixtures_helpers.py tests/test_brief_bundle.py tests/test_brief_render.py
git commit -m "test(brief): shared brief_cfg factory — external_dir isolated by default"
```

---

### Task 2: `.PNG` uppercase extension parses

`tools/chart_drops.py::scan_drops` admits `.PNG` through the image gate
(`path.suffix.lower()`), but `_NAME_RE` pins lowercase `png|jpg|jpeg`, so
an otherwise-perfect `coinglass_BTCUSDT_20260716-1040.PNG` lands in
`unparseable`. Make the extension case-insensitive (extension ONLY — the
source stays lowercase, the symbol uppercase).

**Files:**

- Modify: `tools/chart_drops.py:32-35` (`_NAME_RE`)
- Test: `tests/test_chart_drops.py`

**Interfaces:**

- Consumes/produces: `parse_drop_filename(name) -> (source, symbol,
  ts_ms|None) | None` — signature unchanged.

- [ ] **Step 1: Write the failing tests** (append to
  `tests/test_chart_drops.py`, matching its existing function-test style)

```python
def test_parse_drop_filename_uppercase_extension() -> None:
    parsed = parse_drop_filename("coinglass_BTCUSDT_20260716-1040.PNG")
    assert parsed is not None
    source, symbol, ts_ms = parsed
    assert (source, symbol) == ("coinglass", "BTCUSDT")
    assert ts_ms is not None


def test_parse_drop_filename_mixed_case_jpeg() -> None:
    assert parse_drop_filename("mmt_ETHUSDT.Jpeg") is not None
    # source/symbol case rules unchanged:
    assert parse_drop_filename("Coinglass_BTCUSDT.png") is None
    assert parse_drop_filename("coinglass_btcusdt.png") is None


def test_scan_drops_picks_up_uppercase_png(tmp_path: Path) -> None:
    drop = tmp_path / "drops"
    drop.mkdir()
    (drop / "coinglass_BTCUSDT_20260716-1040.PNG").write_bytes(b"img")
    pending, unparseable = scan_drops(drop, tmp_path / "ledger.json")
    assert unparseable == []
    assert len(pending) == 1
    assert pending[0].symbol == "BTCUSDT"
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_chart_drops.py -q -k uppercase or mixed_case`
Expected: FAIL — `parsed is None` / name in `unparseable`

- [ ] **Step 3: Fix the regex** (scoped inline flag — Python 3.11+)

```python
_NAME_RE = re.compile(
    r"^(?P<source>[a-z0-9]+)_(?P<symbol>[A-Z0-9]+)"
    r"(?:_(?P<ts>\d{8}(?:-\d{4})?))?\.(?i:png|jpg|jpeg)$"
)
```

- [ ] **Step 4: Run the chart-drops suite**

Run: `poetry run pytest tests/test_chart_drops.py -q`
Expected: all PASS

- [ ] **Step 5: Lint/typecheck + commit**

```bash
make lint-py && make typecheck
git add tools/chart_drops.py tests/test_chart_drops.py
git commit -m "fix(chart-drops): accept uppercase image extensions (.PNG/.JPG/.Jpeg)"
```

---

### Task 3: Suppress cross-symbol note duplication in the external loader

`analytics/brief/external.py::load_external_state` validates BEFORE the
symbol check, so one malformed file emits an "invalid" note into EVERY
symbol's pass (N duplicate health notes, each with a different symbol
prefix). When the file's claimed symbol is readable and different, skip
silently — the matching symbol's own pass reports it. Files whose symbol
cannot be read (unreadable JSON, non-dict, missing/empty/non-string
symbol) keep the current every-pass note: there is no owner to defer to.

**Files:**

- Modify: `analytics/brief/external.py:189-202` (loader loop head)
- Test: `tests/test_brief_external.py`

**Interfaces:**

- Consumes/produces: `load_external_state(...)` — signature unchanged;
  note-emission behavior narrowed as described.

- [ ] **Step 1: Write the failing test**

```python
def test_invalid_file_noted_only_in_its_own_symbols_pass(tmp_path: Path) -> None:
    # bad intensity -> invalid, but symbol is readable: ETHUSDT owns it.
    bad = _valid_snapshot(symbol="ETHUSDT")  # the file's existing builder (line 60)
    bad["clusters"][0]["intensity"] = "nuclear"
    (tmp_path / "coinglass_liq_map_1d_ETHUSDT.json").write_text(json.dumps(bad))

    _, btc_notes = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    _, eth_notes = load_external_state(
        tmp_path, "ETHUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert btc_notes == []
    assert any("invalid" in n for n in eth_notes)


def test_symbolless_invalid_file_still_noted_everywhere(tmp_path: Path) -> None:
    (tmp_path / "junk.json").write_text('{"schema": "external-levels-v1"}')
    _, btc_notes = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    _, eth_notes = load_external_state(
        tmp_path, "ETHUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert any("invalid" in n for n in btc_notes)
    assert any("invalid" in n for n in eth_notes)
```

(`tests/test_brief_external.py` already defines `_valid_snapshot`,
`AS_OF = 1_789_400_000_000`, and `_load`/`_write` helpers — reuse them,
never invent a second builder. `_valid_snapshot(**overrides)` accepts the
`symbol=` override directly.)

- [ ] **Step 2: Run to verify the first test fails**

Run: `poetry run pytest tests/test_brief_external.py -q -k own_symbols_pass`
Expected: FAIL — `btc_notes` contains the ETHUSDT file's invalid note

- [ ] **Step 3: Implement the skip**

In the loader loop, immediately after the `json.loads` try/except and
BEFORE `validate_snapshot_dict`:

```python
        claimed = data.get("symbol") if isinstance(data, dict) else None
        if isinstance(claimed, str) and claimed and claimed != symbol:
            continue  # another symbol's file — its own pass reports problems
        problems = validate_snapshot_dict(data)
        if problems:
            notes.append(f"external: invalid {path.name} ({problems[0]})")
            continue
```

and DELETE the now-dead post-validation symbol check
(`if data["symbol"] != symbol: continue` — after validation the symbol is
a valid non-empty string, and a mismatch already `continue`d above).

- [ ] **Step 4: Run the external suite**

Run: `poetry run pytest tests/test_brief_external.py -q`
Expected: all PASS

- [ ] **Step 5: Lint/typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/external.py tests/test_brief_external.py
git commit -m "fix(brief): invalid external snapshots note only their own symbol's pass"
```

---

### Task 4: UI parity — band-collapse basis + External legend completeness

Two Brief.svelte items from the reviews. (a) Band-collapse basis parity:
the terminal renderer collapses a cluster band when the FORMATTED prices
are equal (`fmt_price(lo) == fmt_price(hi)` —
`analytics/brief/render.py:279-284`), the UI when the RAW floats are
equal, so `64000.0–64000.4` renders `64000–64000` in the browser and
`64000` in the terminal. (b) The External legend omits the above/below
split and the `agg` scope suffix.

**Files:**

- Modify: `web/ui/src/pages/Brief.svelte:55-62` (`extCluster`)
- Modify: `web/ui/src/pages/Brief.svelte:209-217` (legend `<dd>`)

**Interfaces:** none (presentation only; no TS type change).

- [ ] **Step 1: Rewrite `extCluster` on the formatted-string basis**

```ts
  function extCluster(c: BriefExternalClusterRow): string {
    const lo = fmtPrice(c.price_lo);
    const hi = fmtPrice(c.price_hi);
    const band = lo === hi ? lo : `${lo}–${hi}`;
    const strength = c.intensity === "high" ? "HIGH" : c.intensity;
    return `${band} ${strength}${c.label ? " " + c.label : ""} (${fmtDist(c.dist_atr)})`;
  }
```

- [ ] **Step 2: Replace the External legend `<dd>` body** (lines 210-217)

```html
        <dd>
          Verified liquidation and order-book levels read from
          operator-dropped chart screenshots (Coinglass / MMT). Each line
          is one snapshot: source, panel (liq / book / map), window, an
          "agg" tag when the panel shows exchange-aggregated rather than
          pair-specific data, capture age, then price bands split above /
          below the reference price (nearest first, capped per side) with
          intensity (HIGH = brightest) and ATR distance. ⚠spot flags a
          snapshot whose printed spot price disagrees with the brief's
          reference price.
        </dd>
```

- [ ] **Step 3: Verify**

Run: `make web-check && make web-build`
Expected: 0 new errors/warnings (pre-existing Backtest.svelte CSS warning
only); build clean.

- [ ] **Step 4: Commit**

```bash
git add web/ui/src/pages/Brief.svelte
git commit -m "fix(ui): External band-collapse on formatted prices + legend above/below+agg"
```

---

### Task 5: Populated-path `/api/brief` parity test

The #484 final review's top pick: the only API regression test for
`external` asserts the null path, so a typo in the nested Pydantic mirror
models (`ExternalSnapshotModel` / `ExternalClusterRowModel`) would ship
silently. The router builds `BriefConfig` with the DEFAULT (relative)
`external_dir`, so `monkeypatch.chdir(tmp_path)` plus a seeded snapshot
under `tmp_path/docs/plans/external-context/` drives the full
dataclass → `bundle_to_dict` → Pydantic → JSON boundary POPULATED.

**Files:**

- Test: `tests/test_web_brief.py` (append; add `json`, `Path`, `pytest`
  imports as needed)

**Interfaces:**

- Consumes: `_client(conn)` helper + `make_conn`/`seed_symbol`/`START_MS`
  already in the file; `AS_OF_ISO = "2024-03-01T00:00:00Z"` →
  `AS_OF_MS = 1_709_251_200_000`.

- [ ] **Step 1: Write the test**

```python
AS_OF_MS = 1_709_251_200_000  # == AS_OF_ISO
_H1 = 3_600_000


def test_get_brief_external_populated_path_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Router uses the DEFAULT relative external_dir -> chdir into a temp
    # tree with one fresh snapshot; nested external models must round-trip.
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    ext_dir = tmp_path / "docs" / "plans" / "external-context"
    ext_dir.mkdir(parents=True)
    snapshot = {
        "schema": "external-levels-v1",
        "source": "coinglass",
        "symbol": "BTCUSDT",
        "panel": "liq_map",
        "window": "1d",
        "scope": "pair",
        "captured_at_ms": AS_OF_MS - 14 * _H1,
        "ingested_at_ms": AS_OF_MS - 13 * _H1,
        "verified": True,
        "spot_price_hint": None,
        "clusters": [
            {"price_lo": 1.0, "price_hi": 2.0, "kind": "liq",
             "intensity": "low", "label": ""},
            {"price_lo": 900_000.0, "price_hi": 1_000_000.0, "kind": "liq",
             "intensity": "high", "label": "magnet"},
        ],
        "notes": "",
    }
    (ext_dir / "coinglass_liq_map_1d_BTCUSDT.json").write_text(
        json.dumps(snapshot)
    )
    monkeypatch.chdir(tmp_path)
    client = _client(conn)
    res = client.get(
        "/api/brief",
        params={"symbols": "BTCUSDT", "days": 60, "as_of": AS_OF_ISO},
    )
    assert res.status_code == 200
    ext = res.json()["panels"][0]["external"]
    assert ext is not None
    assert len(ext["snapshots"]) == 1
    snap = ext["snapshots"][0]
    assert snap["source"] == "coinglass"
    assert snap["panel"] == "liq_map"
    assert snap["window"] == "1d"
    assert snap["scope"] == "pair"
    assert snap["captured_at_ms"] == AS_OF_MS - 14 * _H1
    assert round(snap["age_hours"]) == 14
    assert snap["spot_price_hint"] is None
    assert snap["spot_hint_deviation"] is False
    # seeded closes sit far inside (2, 900k): one cluster per side.
    assert len(snap["clusters_above"]) == 1
    assert len(snap["clusters_below"]) == 1
    row = snap["clusters_above"][0]
    assert set(row) == {
        "price_lo", "price_hi", "kind", "intensity", "label", "dist_atr",
    }
    assert row["label"] == "magnet"
    assert row["dist_atr"] > 0
```

- [ ] **Step 2: Run it — must PASS against current code**

Run: `poetry run pytest tests/test_web_brief.py -q`
Expected: all PASS. (This is a regression NET, not a bug fix — RED was
demonstrated historically by the T9 `extra="ignore"` field-drop bug. If
it FAILS, a nested-model parity bug exists: STOP and report, do not
adjust the test to fit.)

- [ ] **Step 3: Lint/typecheck + commit**

```bash
make lint-py && make typecheck
git add tests/test_web_brief.py
git commit -m "test(web): populated-path /api/brief external parity test"
```

---

### Task 6: External reader/bundle/render coverage adds

The T2/T3c/T4a/T5a gaps from the #484 final review, one commit. All are
tests over EXISTING behavior — if any fails, STOP and report (that's a
real bug surfacing, not a test to bend).

**Files:**

- Test: `tests/test_brief_external.py` (validator + loader cases)
- Test: `tests/test_brief_bundle.py` (note-prefix end-to-end)
- Test: `tests/test_brief_render.py` (renderer join/none paths)

**Interfaces:**

- Consumes: `validate_snapshot_dict`, `load_external_state` from
  `analytics.brief.external`; `_external_snapshot_bit`, `_external_lines`
  from `analytics.brief.render` (add to the existing import block);
  `ExternalClusterRow`, `ExternalSnapshot`, `ExternalState` from
  `analytics.brief.types`; `brief_cfg` from Task 1.

- [ ] **Step 1: Validator branch tests** (`tests/test_brief_external.py`;
  build from the file's existing snapshot-dict fixture builder)

```python
import pytest


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda d: d["clusters"].__setitem__(0, "not-a-dict"), "not an object"),
        (lambda d: d["clusters"][0].pop("kind"), "bad keys"),
        (lambda d: d["clusters"][0].update(price_lo="x"), "prices not numbers"),
        (lambda d: d["clusters"][0].update(price_lo=9.0, price_hi=1.0), "price_lo > price_hi"),
        (lambda d: d["clusters"][0].update(price_lo=-1.0, price_hi=2.0), "price_lo <= 0"),
        (lambda d: d["clusters"][0].update(kind="magic"), "kind"),
        (lambda d: d["clusters"][0].update(intensity="nuclear"), "intensity"),
        (lambda d: d["clusters"][0].update(label=7), "label not a string"),
        (lambda d: d.update(source=""), "source"),
        (lambda d: d.update(symbol=7), "symbol"),
        (lambda d: d.update(notes=None), "notes"),
        (lambda d: d.update(spot_price_hint="high"), "spot_price_hint"),
        (lambda d: d.update(verified=False), "verified"),
        (lambda d: d.update(clusters=[]), "clusters"),
    ],
)
def test_validate_snapshot_dict_rejects(mutate, fragment) -> None:  # type: ignore[no-untyped-def]
    data = _valid_snapshot()
    mutate(data)
    problems = validate_snapshot_dict(data)
    assert problems, f"expected a problem for {fragment}"
    assert any(fragment in p for p in problems)
```

(mypy strict note: parametrized lambdas make full annotation noisy — the
file may already use typed helpers; if the suite convention forbids the
ignore, expand to individual `-> None` test functions with the same 14
cases. Match the file's existing style.)

- [ ] **Step 2: Loader edge tests** (same file)

```python
def test_below_side_cap_keeps_nearest_first(tmp_path: Path) -> None:
    data = _valid_snapshot()
    # 4 bands below ref=100, nearest first should survive the cap of 3.
    data["clusters"] = [
        {"price_lo": lo, "price_hi": lo + 1.0, "kind": "liq",
         "intensity": "low", "label": ""}
        for lo in (90.0, 80.0, 70.0, 60.0)
    ]
    (tmp_path / "coinglass_liq_map_1d_BTCUSDT.json").write_text(json.dumps(data))
    state, _ = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert state is not None
    below = state.snapshots[0].clusters_below
    assert len(below) == 3
    mids = [(r.price_lo + r.price_hi) / 2 for r in below]
    assert mids == sorted(mids, reverse=True)  # nearest (least deep) first


def test_ref_close_zero_no_deviation_note(tmp_path: Path) -> None:
    data = _valid_snapshot()
    data["spot_price_hint"] = 123.0
    (tmp_path / "coinglass_liq_map_1d_BTCUSDT.json").write_text(json.dumps(data))
    state, notes = load_external_state(
        tmp_path, "BTCUSDT", 0.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert state is not None
    assert not any("deviates" in n for n in notes)
    assert state.snapshots[0].spot_hint_deviation is False


def test_stale_file_note_suppressed_when_fresh_exists(tmp_path: Path) -> None:
    # stale sibling skipped pre-dedup; with fresh present, no re-drop note fires
    fresh = _valid_snapshot()
    stale = _valid_snapshot()
    stale["captured_at_ms"] = AS_OF - int(72 * 3_600_000)
    (tmp_path / "a_fresh.json").write_text(json.dumps(fresh))
    (tmp_path / "b_stale.json").write_text(json.dumps(stale))
    state, notes = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert state is not None
    assert len(state.snapshots) == 1
    assert not any("stale" in n for n in notes)  # fresh exists -> no re-drop nag


def test_unreadable_directory_entry_becomes_note(tmp_path: Path) -> None:
    (tmp_path / "dir.json").mkdir()  # read_text -> IsADirectoryError (OSError)
    _, notes = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert any("unreadable dir.json" in n for n in notes)
```

- [ ] **Step 3: Bundle note-prefix end-to-end** (`tests/test_brief_bundle.py`)

```python
def test_external_notes_flow_prefixed_through_bundle(tmp_path: Path) -> None:
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "junk.json").write_text("{not json")
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(
        conn, brief_cfg(("BTCUSDT",), AS_OF, external_dir=ext)
    )
    assert any(
        n.startswith("BTCUSDT: external: unreadable junk.json")
        for n in bundle.health.notes
    )
```

(Adapt the health-notes attribute name to the `BriefBundle` field the
file already asserts on — it is asserted in the existing health tests.)

- [ ] **Step 4: Renderer join/none paths** (`tests/test_brief_render.py`)

```python
def _ext_row(lo: float, hi: float, dist: float, label: str = "") -> ExternalClusterRow:
    return ExternalClusterRow(
        price_lo=lo, price_hi=hi, kind="liq", intensity="med",
        label=label, dist_atr=dist,
    )


def test_external_snapshot_bit_joins_multiple_clusters() -> None:
    snap = ExternalSnapshot(
        source="coinglass", panel="liq_map", window="1d", scope="pair",
        captured_at_ms=1, age_hours=14.0, spot_price_hint=None,
        spot_hint_deviation=False,
        clusters_above=[_ext_row(101, 102, 0.5), _ext_row(105, 106, 1.5, "top")],
        clusters_below=[],
    )
    bit = _external_snapshot_bit(snap)
    assert ", " in bit.split("above ")[1].split(" · below")[0]
    assert bit.endswith("below none")


def test_external_snapshot_bit_both_sides_none() -> None:
    snap = ExternalSnapshot(
        source="coinglass", panel="liq_heatmap", window=None, scope="agg",
        captured_at_ms=1, age_hours=3.0, spot_price_hint=None,
        spot_hint_deviation=False, clusters_above=[], clusters_below=[],
    )
    bit = _external_snapshot_bit(snap)
    assert "above none" in bit
    assert "below none" in bit
    assert " agg " in bit
```

(PR-2 adds a `venue` field to `ExternalSnapshot`; these constructors are
keyword-only so PR-2's task extends them — fine.)

- [ ] **Step 5: Run all three suites**

Run: `poetry run pytest tests/test_brief_external.py tests/test_brief_bundle.py tests/test_brief_render.py -q`
Expected: all PASS

- [ ] **Step 6: Lint/typecheck + commit**

```bash
make lint-py && make typecheck
git add tests/test_brief_external.py tests/test_brief_bundle.py tests/test_brief_render.py
git commit -m "test(brief): external validator/loader/bundle/render coverage adds"
```

---

### Task 7: chart-drops ledger hardening + write-branch move-failure test

Three T6/T7 minors. (a) `load_ledger` re-raises a bare
`json.JSONDecodeError` traceback on a corrupt ledger — worse, if anyone
ever "fixed" that by returning `{}`, every processed image would silently
re-ingest; make it a loud, explained failure. (b) `mark_processed`'s
`ValueError` guard is untested. (c) The move-failure fail-open contract
(image safe, ledger unmarked) is tested only on the `mark` CLI branch —
add the `write` branch.

**Files:**

- Modify: `tools/chart_drops.py:74-78` (`load_ledger`)
- Test: `tests/test_chart_drops.py`

**Interfaces:**

- Produces: `load_ledger` raises
  `ValueError("corrupt ledger <path> — fix or delete it: ...")` on
  undecodable JSON (was: bare `json.JSONDecodeError`).

- [ ] **Step 1: Write the failing test**

```python
def test_load_ledger_corrupt_raises_actionable_error(tmp_path: Path) -> None:
    ledger = tmp_path / "processed.json"
    ledger.write_text("{broken")
    with pytest.raises(ValueError, match="corrupt ledger"):
        load_ledger(ledger)
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/test_chart_drops.py -q -k corrupt`
Expected: FAIL — raises `json.JSONDecodeError`, not the matched ValueError

- [ ] **Step 3: Implement**

```python
def load_ledger(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        # A corrupt dedup ledger must fail loudly: silently treating it as
        # empty would re-ingest every already-processed image on next scan.
        raise ValueError(f"corrupt ledger {path} — fix or delete it: {exc}") from exc
    return data if isinstance(data, dict) else {}
```

- [ ] **Step 4: Add the two coverage tests**

```python
def test_mark_processed_rejects_unknown_outcome(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outcome"):
        mark_processed(tmp_path / "l.json", "sha", "f.png", "exploded", 1)


def test_write_branch_move_failure_fails_open(tmp_path: Path) -> None:
    # done/ existing as a FILE makes move_to_done raise; the snapshot is
    # already written (fail-open), but the ledger MUST stay unmarked so
    # the image is still visible to the next scan.
    drop = tmp_path / "drops"
    drop.mkdir()
    image = drop / "coinglass_BTCUSDT_20260716-1040.png"
    image.write_bytes(b"img")
    (drop / "done").write_text("not a dir")
    snap_file = tmp_path / "snap.json"
    snap_file.write_text(json.dumps(_snapshot()))  # the file's existing builder (line 96)
    out_dir = tmp_path / "out"
    ledger = tmp_path / "ledger.json"
    with pytest.raises(OSError):
        main([
            "write", "--json-file", str(snap_file), "--image", str(image),
            "--out-dir", str(out_dir), "--ledger", str(ledger),
        ])
    assert image.exists()  # image untouched in the drop dir
    assert load_ledger(ledger) == {}  # unmarked -> next scan retries
```

(`_snapshot(**overrides)` is `tests/test_chart_drops.py`'s existing
snapshot-dict builder, line 96 — reuse it.)

- [ ] **Step 5: Run the suite**

Run: `poetry run pytest tests/test_chart_drops.py -q`
Expected: all PASS

- [ ] **Step 6: Lint/typecheck + commit**

```bash
make lint-py && make typecheck
git add tools/chart_drops.py tests/test_chart_drops.py
git commit -m "fix(chart-drops): actionable corrupt-ledger error + write-branch fail-open test"
```

---

### Task 8: Pin the unpinned card-v2 rubric delta phrases (test-only)

The #486 final review: contract tests pin only a subset of the card-v2
delta phrases — the book-bands, age_hours-trust, and intensity-trust
fragments are unpinned, so a silent rubric edit could drop them without a
test going red. Pin them. DO NOT touch `card/prompt.py`.

**Files:**

- Test: `tests/test_card_prompt.py` (append inside the existing rubric
  test class, matching its style at `test_rubric_external_directions`)

- [ ] **Step 1: Write the pins**

```python
    def test_rubric_external_trust_guards_pinned(self) -> None:
        # card-v2 delta fragments not covered by the original contract pins
        assert '"book" bands' in RUBRIC
        assert "higher intensity and lower age_hours" in RUBRIC
        assert "spot_hint_deviation true" in RUBRIC
```

- [ ] **Step 2: Run — must PASS against the current rubric**

Run: `poetry run pytest tests/test_card_prompt.py -q`
Expected: all PASS. If a fragment mismatches, read the exact string in
`card/prompt.py:44-48` and correct the PIN (never the rubric).

- [ ] **Step 3: Prove the pin bites (RED check, then revert)**

Temporarily change `age_hours` to `age_hrs` in `card/prompt.py`, run the
test (expect FAIL), then `git checkout -- card/prompt.py`. State the
result in the task report.

- [ ] **Step 4: Lint/typecheck + commit**

```bash
make lint-py && make typecheck
git add tests/test_card_prompt.py
git commit -m "test(card): pin book-bands/intensity/age_hours rubric trust-guard phrases"
```

---

### Task 9: PR-1 full gate + pull request

- [ ] **Step 1: Full gate**

```bash
make lint-py && make typecheck && make test && make test-regression
make web-check && make web-build
```

Expected: all green; regression goldens UNMOVED (report exact test count).

- [ ] **Step 2: Push + PR**

```bash
git push -u origin chore/m3-m4-polish-tests
gh pr create --title "chore(polish): M3+M4 review-minor batch — isolation, parity, coverage, small fixes" --body-file /tmp/pr-chore-m3-m4-polish-tests.md
```

PR body via the `.claude/skills/pr-summary` template (Background = the
PR #484 / PR #487 review-minor ledger; Summary = Tasks 1-8; test plan
pre-ticked from Step 1's actual output).

- [ ] **Step 3: Controller runs `/post-branch`** (docs sweep + MEMORY.md +
  pre-merge check) before reporting the PR URL.

---

## PR-2 — `feat/external-venue` (branch off main AFTER PR-1 merges; rebase if PR-1 is still open)

### Task 10: Optional `venue` in the snapshot schema + widened dedup key

Operator-approved (2026-07-15): Binance-pair vs exchange-aggregated vs
Hyperliquid snapshots of the same panel+window currently CLOBBER each
other — the reader keeps latest-wins per `(source, panel, window)`.
Add an OPTIONAL `venue` key to `external-levels-v1` (additive: absent and
null both mean unspecified, every existing on-disk snapshot stays valid)
and widen the dedup key to `(source, venue, scope, panel, window)`.

**Files:**

- Modify: `analytics/brief/external.py` (validator + `_build_snapshot` +
  dedup key)
- Modify: `analytics/brief/types.py:170-181` (`ExternalSnapshot`)
- Test: `tests/test_brief_external.py`

**Interfaces:**

- Produces: `ExternalSnapshot.venue: str | None` (new field, after
  `source`); `validate_snapshot_dict` accepts an optional `venue` key
  (non-empty string or null). Task 11 (writer) and Task 12 (surfaces)
  consume both.

- [ ] **Step 1: Write the failing tests**

```python
def test_venue_absent_and_null_both_valid_and_unspecified(tmp_path: Path) -> None:
    legacy = _valid_snapshot()          # no venue key at all
    nulled = _valid_snapshot()
    nulled["venue"] = None
    assert validate_snapshot_dict(legacy) == []
    assert validate_snapshot_dict(nulled) == []


def test_venue_must_be_nonempty_string_or_null() -> None:
    for bad in ("", 7, ["binance"]):
        data = _valid_snapshot()
        data["venue"] = bad
        assert any("venue" in p for p in validate_snapshot_dict(data))


def test_same_panel_window_different_venue_coexist(tmp_path: Path) -> None:
    a = _valid_snapshot()
    a["venue"] = "binance"
    b = _valid_snapshot()
    b["venue"] = "hyperliquid"
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    state, _ = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert state is not None
    assert len(state.snapshots) == 2
    assert {s.venue for s in state.snapshots} == {"binance", "hyperliquid"}


def test_same_panel_window_different_scope_coexist(tmp_path: Path) -> None:
    a = _valid_snapshot()
    a["scope"] = "pair"
    b = _valid_snapshot()
    b["scope"] = "agg"
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    state, _ = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert state is not None
    assert len(state.snapshots) == 2


def test_identical_full_key_still_latest_wins(tmp_path: Path) -> None:
    older = _valid_snapshot()
    newer = _valid_snapshot()
    newer["captured_at_ms"] = older["captured_at_ms"] + 3_600_000
    for d in (older, newer):
        d["venue"] = "binance"
    (tmp_path / "a.json").write_text(json.dumps(older))
    (tmp_path / "b.json").write_text(json.dumps(newer))
    state, _ = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert state is not None
    assert len(state.snapshots) == 1
    assert state.snapshots[0].captured_at_ms == newer["captured_at_ms"]
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_brief_external.py -q -k venue`
Expected: FAIL — `unknown keys: ['venue']` from the validator

- [ ] **Step 3: Implement**

`analytics/brief/external.py`:

```python
_OPTIONAL_KEYS = {"venue"}  # additive 2026-07-16; absent == null == unspecified
```

In `validate_snapshot_dict` change the unknown-key line and add the venue
check after the scope check:

```python
    unknown = keys - _REQUIRED_KEYS - _OPTIONAL_KEYS
```

```python
    venue = data.get("venue")
    if venue is not None and (not isinstance(venue, str) or not venue):
        problems.append("venue: not a non-empty string or null")
```

and extend the docstring: unknown keys still reject; `venue` is the one
optional key (schema stays `external-levels-v1`).

`analytics/brief/types.py` — `ExternalSnapshot`, insert after `source`:

```python
    venue: str | None  # exchange the panel shows (e.g. "binance", "hyperliquid"); None = unspecified
```

`_build_snapshot` — add to the constructor call:

```python
        venue=None if data.get("venue") is None else str(data["venue"]),
```

Dedup key in `load_external_state` (widen the `fresh` annotation to
`dict[tuple[str, str, str, str, str], dict[str, Any]]`):

```python
        key = (
            str(data["source"]),
            str(data.get("venue") or ""),
            str(data["scope"] or ""),
            str(data["panel"]),
            str(data["window"] or ""),
        )
```

Fix any test constructors of `ExternalSnapshot` that now miss `venue`
(Task 6 added keyword-only ones — add `venue=None`).

- [ ] **Step 4: Run the external + render + bundle suites**

Run: `poetry run pytest tests/test_brief_external.py tests/test_brief_render.py tests/test_brief_bundle.py tests/test_web_brief.py -q`
Expected: PASS except any `ExternalSnapshot(...)` constructor gaps —
fix those with `venue=None`, re-run to green. `tests/test_web_brief.py`
populated-path test must still pass UNCHANGED (venue reaches the API
model only in Task 12; `bundle_to_dict`'s extra key is absorbed there —
if it FAILS here, do Task 12's model edit in the same commit and say so).

- [ ] **Step 5: Lint/typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/external.py analytics/brief/types.py tests/test_brief_external.py tests/test_brief_render.py
git commit -m "feat(brief): optional venue in external snapshots + (source,venue,scope,panel,window) dedup key"
```

---

### Task 11: Writer-side venue — drop-filename token + snapshot filename

Operators name Hyperliquid-view drops with a venue token:
`coinglass-hyperliquid_BTCUSDT_20260716-1040.png`. No dash = no venue
(fully backward-compatible). The written snapshot filename carries the
same token so same-minute Binance and Hyperliquid files never collide on
disk.

**Files:**

- Modify: `tools/chart_drops.py` (`_NAME_RE`, `parse_drop_filename`,
  `PendingDrop`, `scan_drops`, `snapshot_filename`)
- Test: `tests/test_chart_drops.py`

**Interfaces:**

- Produces: `parse_drop_filename(name) -> (source, venue|None, symbol,
  ts_ms|None) | None` — 4-tuple now (BREAKING for internal callers;
  `scan_drops` is the only one). `PendingDrop.venue: str | None` (the
  `/ingest-charts` skill JSON passes it through to the snapshot dict).

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_drop_filename_with_venue_token() -> None:
    parsed = parse_drop_filename("coinglass-hyperliquid_BTCUSDT_20260716-1040.png")
    assert parsed is not None
    source, venue, symbol, ts_ms = parsed
    assert (source, venue, symbol) == ("coinglass", "hyperliquid", "BTCUSDT")
    assert ts_ms is not None


def test_parse_drop_filename_without_venue_is_none_venue() -> None:
    parsed = parse_drop_filename("coinglass_BTCUSDT_20260716-1040.png")
    assert parsed is not None
    assert parsed[1] is None


def test_scan_drops_carries_venue(tmp_path: Path) -> None:
    drop = tmp_path / "drops"
    drop.mkdir()
    (drop / "coinglass-hyperliquid_BTCUSDT_20260716-1040.png").write_bytes(b"i")
    pending, unparseable = scan_drops(drop, tmp_path / "ledger.json")
    assert unparseable == []
    assert pending[0].venue == "hyperliquid"


def test_snapshot_filename_includes_venue() -> None:
    data = _snapshot()
    data["venue"] = "hyperliquid"
    name = snapshot_filename(data)
    assert name.startswith("coinglass-hyperliquid_")
    data.pop("venue")
    assert snapshot_filename(data).startswith("coinglass_")
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_chart_drops.py -q -k venue`
Expected: FAIL — regex has no venue group / 3-tuple unpacking error

- [ ] **Step 3: Implement**

```python
_NAME_RE = re.compile(
    r"^(?P<source>[a-z0-9]+)(?:-(?P<venue>[a-z0-9]+))?_(?P<symbol>[A-Z0-9]+)"
    r"(?:_(?P<ts>\d{8}(?:-\d{4})?))?\.(?i:png|jpg|jpeg)$"
)
```

`parse_drop_filename` returns the 4-tuple (update its docstring and
return type to `tuple[str, str | None, str, int | None] | None`); add
`venue: str | None` to `PendingDrop` (after `source`); `scan_drops`
unpacks `source, venue, symbol, ts_ms = parsed` and passes
`venue=venue`. `snapshot_filename`:

```python
def snapshot_filename(data: dict[str, Any]) -> str:
    ts = datetime.fromtimestamp(data["captured_at_ms"] / 1000, tz=_MYT)
    window = f"_{data['window']}" if data["window"] else ""
    src = data["source"] + (f"-{data['venue']}" if data.get("venue") else "")
    return (
        f"{src}_{data['panel']}{window}_{data['symbol']}"
        f"_{ts.strftime('%Y%m%d-%H%M')}.json"
    )
```

Fix existing tests unpacking the old 3-tuple (Task 2 added two — update
them to the 4-tuple, asserting `venue is None`).

- [ ] **Step 4: Run the suite**

Run: `poetry run pytest tests/test_chart_drops.py -q`
Expected: all PASS

- [ ] **Step 5: Lint/typecheck + commit**

```bash
make lint-py && make typecheck
git add tools/chart_drops.py tests/test_chart_drops.py
git commit -m "feat(chart-drops): venue token in drop + snapshot filenames"
```

---

### Task 12: Venue on the surfaces — renderer, API model, TS, UI, skill doc

Thread `venue` through every presentation surface. Legacy snapshots
(`venue=None`) must render BYTE-IDENTICALLY to today on every surface.

**Files:**

- Modify: `analytics/brief/render.py:287-296` (`_external_snapshot_bit`)
- Modify: `web/api/models/brief.py:150-161` (`ExternalSnapshotModel`)
- Modify: `web/ui/src/api.ts:743-754` (`BriefExternalSnapshot`)
- Modify: `web/ui/src/pages/Brief.svelte:381` (snapshot line) + External
  legend `<dd>` (one clause)
- Modify: `.claude/skills/ingest-charts/SKILL.md` (filename grammar +
  snapshot JSON contract)
- Test: `tests/test_brief_render.py`, `tests/test_web_brief.py`

**Interfaces:**

- Consumes: `ExternalSnapshot.venue` from Task 10, `PendingDrop.venue`
  from Task 11.

- [ ] **Step 1: Write the failing tests**

`tests/test_brief_render.py` (reuses Task 6's `_ext_row` helper):

```python
def test_external_snapshot_bit_shows_venue() -> None:
    snap = ExternalSnapshot(
        source="coinglass", venue="hyperliquid", panel="liq_map",
        window="1d", scope="pair", captured_at_ms=1, age_hours=2.0,
        spot_price_hint=None, spot_hint_deviation=False,
        clusters_above=[_ext_row(101, 102, 0.5)], clusters_below=[],
    )
    assert _external_snapshot_bit(snap).startswith("coinglass/hyperliquid map (1d)")


def test_external_snapshot_bit_no_venue_unchanged() -> None:
    snap = ExternalSnapshot(
        source="coinglass", venue=None, panel="liq_map", window="1d",
        scope="pair", captured_at_ms=1, age_hours=2.0,
        spot_price_hint=None, spot_hint_deviation=False,
        clusters_above=[_ext_row(101, 102, 0.5)], clusters_below=[],
    )
    assert _external_snapshot_bit(snap).startswith("coinglass map (1d)")
```

`tests/test_web_brief.py` — extend the Task 5 populated-path test with
two lines (snapshot dict stays venue-less = legacy):

```python
    assert "venue" in snap
    assert snap["venue"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/test_brief_render.py tests/test_web_brief.py -q -k venue`
Expected: FAIL — unexpected keyword `venue` / KeyError `venue`

- [ ] **Step 3: Implement the four code surfaces**

`analytics/brief/render.py` `_external_snapshot_bit` — first line of the
return expression changes source to:

```python
    src = snap.source if snap.venue is None else f"{snap.source}/{snap.venue}"
```

```python
    return (
        f"{src} {_PANEL_SHORT.get(snap.panel, snap.panel)}{win}{scope_bit}"
        f" · {snap.age_hours:.0f}h{dev} · above {above} · below {below}"
    )
```

`web/api/models/brief.py` `ExternalSnapshotModel` — after `source`:

```python
    venue: str | None
```

`web/ui/src/api.ts` `BriefExternalSnapshot` — after `source`:

```ts
  venue: string | null;
```

`web/ui/src/pages/Brief.svelte:381` snapshot line:

```html
                    {snap.source}{snap.venue ? `/${snap.venue}` : ""} {panelShort(snap.panel)}{snap.window ? ` (${snap.window})` : ""}{snap.scope === "agg" ? " agg" : ""}
```

Legend `<dd>` — change the source clause to:

```text
one snapshot: source (with /venue when the panel shows one specific
exchange's data, e.g. coinglass/hyperliquid), panel (liq / book / map),
```

- [ ] **Step 4: Update the `/ingest-charts` skill doc**

In `.claude/skills/ingest-charts/SKILL.md`: update the drop-filename
grammar everywhere it appears from `<source>_<SYMBOL>[_<YYYYMMDD-HHMM>]`
to `<source>[-<venue>]_<SYMBOL>[_<YYYYMMDD-HHMM>]`, add the example
`coinglass-hyperliquid_BTCUSDT_20260716-1040.png`, and add `venue` to the
snapshot-JSON contract description (optional; null/absent = unspecified;
flows from the filename token via `PendingDrop.venue`).
Acceptance: `grep -c 'coinglass-hyperliquid' .claude/skills/ingest-charts/SKILL.md` ≥ 1.

- [ ] **Step 5: Run everything touched**

Run: `poetry run pytest tests/test_brief_render.py tests/test_web_brief.py tests/test_brief_external.py -q`
Expected: all PASS
Run: `make web-check && make web-build`
Expected: clean (pre-existing Backtest.svelte CSS warning only)

- [ ] **Step 6: Lint/typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/render.py web/api/models/brief.py web/ui/src/api.ts web/ui/src/pages/Brief.svelte .claude/skills/ingest-charts/SKILL.md tests/test_brief_render.py tests/test_web_brief.py
git commit -m "feat(brief): venue on render/API/TS/UI surfaces + ingest-charts grammar"
```

---

### Task 13: PR-2 full gate + pull request

- [ ] **Step 1: Full gate**

```bash
make lint-py && make typecheck && make test && make test-regression
make web-check && make web-build
```

Expected: all green; regression goldens UNMOVED.

- [ ] **Step 2: Byte-stability spot check (legacy data)**

Run: `PYTHONPATH=. poetry run python -c "
from pathlib import Path
from analytics.brief.external import load_external_state
state, notes = load_external_state(Path('docs/plans/external-context'), 'BTCUSDT', 64000.0, 2000.0, 1_790_000_000_000, ('coinglass','mmt'), 1e9, 3)
print('snapshots:', 0 if state is None else len(state.snapshots))
print('venues:', None if state is None else {s.venue for s in state.snapshots})
print('notes:', notes)
"`
Expected: legacy operator snapshots load with `venue=None`, zero invalid
notes. (Read-only; huge max-age so real files qualify regardless of date.
Skip gracefully if the dir is empty on the runner's machine — say so.)

- [ ] **Step 3: Push + PR**

```bash
git push -u origin feat/external-venue
gh pr create --title "feat(brief): external snapshot venue + widened dedup key" --body-file /tmp/pr-feat-external-venue.md
```

PR body via the `.claude/skills/pr-summary` template; Background = the
operator-approved 2026-07-15 items (Hyperliquid-via-Coinglass
confirmation view; dedup-clobber fix).

- [ ] **Step 4: Controller runs `/post-branch`** (CLAUDE.md M3 bullet +
  `.claude/context/web.md` if model fields are listed there + MEMORY.md)
  before reporting the PR URL.

---

## Parked (explicitly NOT in this batch)

- card-v3 rubric `snapshots[]` path precision — rides the next gated
  `PROMPT_VERSION` bump, never a silent edit.
- External-block presentation redesign (ladder/table, annotated
  screenshot) — design decision on [[brief-v2-roadmap]], needs a
  brainstorm first.
- `SymbolPanelModel.external = None` default style drift — triaged
  non-issue in the #484 final review (T9a).
