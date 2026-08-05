# H15 USD/JPY Carry-Unwind Data-Axis Test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the filed USD/JPY carry-unwind thesis through the repo's pre-committed
research gate and produce a BUILD / AVOID / NO-EDGE verdict.

**Architecture:** Extract H14's shared gate machinery into `analytics/state_audit.py`
so one implementation serves every state-tag audit; add a keyless Yahoo daily fetcher
to `analytics/venue_fetch.py`; build the yen-strength state logic as a pure module
`analytics/fx_carry.py`; drive it from `tools/carry_unwind_audit.py`.

**Tech Stack:** Python 3.13, pandas, numpy, DuckDB, pytest, ruff, mypy (strict).

**Spec:** `docs/superpowers/specs/2026-08-04-h15-usdjpy-carry-unwind-design.md` — read
§5 (states + causality rule), §6 (unit + `BAR_VOL`) and §7 (the seven gate legs) before
starting. The spec is authoritative; this plan implements it.

## Global Constraints

- **Python 3.13**, mypy **strict** — every function needs annotations including
  `-> None`. Tests too.
- **No network in tests.** Fetchers take an injected `get: Getter`; tests pass a
  fake. This is the existing house pattern in `analytics/venue_fetch.py`.
- **DuckDB tests use `duckdb.connect(":memory:")`.** Never touch the real
  `analytics.db`.
- **`DEFAULT_DB_PATH` is imported from a re-export** (`analytics.store` or
  `analytics.data_store`) — never redefined in a runner. It lives in
  `analytics/store/_common.py`, **not** `schema.py`.
- **Definition of Done, every task:** `make lint-py`, `make typecheck`, `make test`
  all green. `make test-regression` on the final task. State each result plainly; if
  a step was skipped or failed, say so.
- **Pre-registered constants are not to be tuned.** `BAR_VOL = 0.02`, `MIN_N = 30`,
  `DSR_FLOOR = 0.95`, `PBO_CEIL = 0.5`, `MINTRL_CONFIDENCE = 0.95`,
  `MAG_THRESHOLD = 1.0`, `MAG_WINDOW = 52`, `MAG_SPAN = 4`, `VOL_WINDOW = 30`. If one
  looks wrong, stop and raise it — do not adjust it to make a verdict appear.
- **`make lint-py` runs `ruff format .`, which reformats python fences inside
  markdown — including this plan file.** It has already been ruff-formatted and
  committed in that state, so `make lint-py` will not churn it. If you add a python
  fence to any `.md`, run `ruff format` on it before committing rather than letting
  the next task's gate produce an unrelated diff.

## File Structure

| File | Responsibility |
| --- | --- |
| `analytics/state_audit.py` | **NEW.** Shared state-tag audit machinery: day collapse, cell construction, per-family DSR/PBO, stability check, the verdict map, and the pre-committed gate constants. No H14- or H15-specific vocabulary. |
| `analytics/venue_premium.py` | **MODIFY.** Keeps H14's series + label logic and its own family key; imports everything else from `state_audit`. Re-exports moved names so existing importers keep working. |
| `analytics/venue_fetch.py` | **MODIFY.** Adds `fetch_yahoo_daily` + `YahooFetchError`. |
| `analytics/fx_carry.py` | **NEW.** Pure USD/JPY state logic: London-date keying, ISO-week aggregation, run counter, magnitude z-score, causal day expansion. No IO. |
| `tools/carry_unwind_audit.py` | **NEW.** CLI runner: refresh → build states → balance report → join outcomes → cells → gate → table → verdict. |
| `tests/test_state_audit.py` | **NEW.** Extraction inertness + gate-leg tests. |
| `tests/test_fx_carry.py` | **NEW.** DST, ISO weeks, run counter, magnitude sign, causality mutation guard. |
| `tests/test_venue_fetch_yahoo.py` | **NEW.** Yahoo parsing + failure modes, network-free. |

---

### Task 1: Extract shared gate machinery into `analytics/state_audit.py`

**Files:**

- Create: `analytics/state_audit.py`
- Modify: `analytics/venue_premium.py`
- Test: `tests/test_state_audit.py`

**Interfaces:**

- Consumes: `analytics.audit_guard` (`AuditCell`, `CellVerdict`, `evaluate_audit_cells`,
  `DECISION_ENABLE`, `DECISION_DISABLE`, `DECISION_CONCENTRATE`,
  `DECISION_INSUFFICIENT`); `analytics.research_guards`
  (`cscv_pbo`, `deflated_sharpe_ratio`, `min_track_record_length`).
- Produces, for Tasks 3 and 4:
  - `causal_zscore(series: pd.Series, window: int) -> pd.Series`
  - `collapse_to_daily(trades: pd.DataFrame, states: pd.Series, *, lag_days: int = 1) -> pd.DataFrame`
  - `build_state_cells(daily: pd.DataFrame) -> list[AuditCell]`
  - `cell_sharpe(arr: npt.NDArray[np.float64]) -> float`
  - `family_pbo(arrays: list[npt.NDArray[np.float64]]) -> float | None`
  - `family_dsr(target_r, family_arrays) -> float`
  - `sign_agrees_early_late(values: list[float]) -> bool`
  - `map_verdict(decision: str, *, n_supp: int, n_days_ok: bool, dsr: float | None, pbo: float | None, stable: bool) -> str`
  - `evaluate_states(cells: list[AuditCell], family_key: Callable[[str], tuple[str, str]], *, bar: float = BAR, alpha: float = ALPHA, min_n: int = MIN_N) -> list[tuple[str, str]]`
  - Constants `BAR`, `ALPHA`, `MIN_N`, `DSR_FLOOR`, `PBO_CEIL`, `MINTRL_CONFIDENCE`,
    `VERDICT_BUILD`, `VERDICT_AVOID`, `VERDICT_NO_EDGE`, `VERDICT_INSUFFICIENT`, `DAY_MS`.

**Why this task exists.** The directional-DSR defect shipped in H8 because the gate
logic was duplicated. H14 fixed its own copy. A third copy for H15 would be a third
site for the same class of defect. This task consolidates to one.

**This is a pure move.** No logic changes, no threshold changes, no renamed
semantics. The only edits are: private `_name` → public `name`, and
`evaluate_premium_states`' hard-coded `_cell_family_key` becomes a `family_key`
parameter. If you find yourself "improving" anything here, stop — the inertness proof
in Step 6 will fail and you will not know which change broke it.

- [ ] **Step 1: Capture the H14 baseline BEFORE touching anything**

The extraction is only provably inert against a recorded baseline. Capture it first.

```bash
poetry run python tools/premium_state_audit.py --source both > /tmp/h14-baseline.txt 2>&1
wc -l /tmp/h14-baseline.txt && head -30 /tmp/h14-baseline.txt
```

If this command fails (e.g. a DuckDB lock from a running `signal watch` daemon —
check with `ps -o pid,etime,cmd -p <pid>`), **stop and report it.** Do not proceed
without a baseline; without it the extraction cannot be verified.

- [ ] **Step 2: Write the failing test**

Create `tests/test_state_audit.py`:

```python
"""Shared state-audit machinery: the extraction must be behaviour-preserving."""

from __future__ import annotations

import numpy as np

from analytics.audit_guard import (
    DECISION_CONCENTRATE,
    DECISION_DISABLE,
    DECISION_ENABLE,
    DECISION_INSUFFICIENT,
    AuditCell,
)
from analytics.state_audit import (
    DSR_FLOOR,
    MIN_N,
    PBO_CEIL,
    VERDICT_AVOID,
    VERDICT_BUILD,
    VERDICT_INSUFFICIENT,
    VERDICT_NO_EDGE,
    cell_sharpe,
    evaluate_states,
    map_verdict,
    sign_agrees_early_late,
)


def _family_key(label: str) -> tuple[str, str]:
    state, direction = label.rsplit("|", 1)
    return ("axis_a" if state.startswith("a_") else "axis_b"), direction


def test_map_verdict_inverts_audit_guard_sign() -> None:
    """DISABLE means the slice is reliably POSITIVE -> BUILD. Do not 'fix' this."""
    kw = {"n_supp": 100, "n_days_ok": True, "dsr": 0.99, "pbo": 0.1, "stable": True}
    assert map_verdict(DECISION_DISABLE, **kw) == VERDICT_BUILD
    assert map_verdict(DECISION_ENABLE, **kw) == VERDICT_AVOID
    assert map_verdict(DECISION_CONCENTRATE, **kw) == VERDICT_NO_EDGE


def test_map_verdict_splits_insufficient_on_n() -> None:
    """Underpowered is INSUFFICIENT; powered-but-null is NO-EDGE."""
    kw = {"n_days_ok": False, "dsr": None, "pbo": None, "stable": False}
    assert (
        map_verdict(DECISION_INSUFFICIENT, n_supp=MIN_N - 1, **kw)
        == VERDICT_INSUFFICIENT
    )
    assert map_verdict(DECISION_INSUFFICIENT, n_supp=MIN_N + 1, **kw) == VERDICT_NO_EDGE


def test_sign_agrees_early_late() -> None:
    assert sign_agrees_early_late([1.0, 2.0, 3.0, 4.0]) is True
    assert sign_agrees_early_late([-1.0, -2.0, -3.0, -4.0]) is True
    assert sign_agrees_early_late([5.0, 5.0, -5.0, -5.0]) is False
    assert sign_agrees_early_late([1.0]) is False


def test_cell_sharpe_zero_dispersion_is_zero() -> None:
    assert cell_sharpe(np.array([0.3, 0.3, 0.3])) == 0.0
    assert cell_sharpe(np.array([1.0])) == 0.0


def test_evaluate_states_accepts_a_family_key() -> None:
    """The parameterised family_key is what lets H15 reuse this unchanged."""
    rng = np.random.default_rng(0)
    cells = [
        AuditCell(
            label="a_hi|long", supp_r=list(rng.normal(0.4, 0.2, 80)), kept_r=[0.0] * 80
        ),
        AuditCell(
            label="a_lo|long", supp_r=list(rng.normal(-0.4, 0.2, 80)), kept_r=[0.0] * 80
        ),
    ]
    out = evaluate_states(cells, _family_key)
    assert [label for label, _ in out] == ["a_hi|long", "a_lo|long"]
    assert all(
        v in {VERDICT_BUILD, VERDICT_AVOID, VERDICT_NO_EDGE, VERDICT_INSUFFICIENT}
        for _, v in out
    )
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `poetry run pytest tests/test_state_audit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.state_audit'`

- [ ] **Step 4: Create `analytics/state_audit.py` by moving code verbatim**

Move these from `analytics/venue_premium.py` **without changing their bodies**, renaming
only the leading underscore off the five private ones:

| From `venue_premium.py` | To `state_audit.py` |
| --- | --- |
| `causal_zscore` | `causal_zscore` |
| `collapse_to_daily` | `collapse_to_daily` |
| `build_state_cells` | `build_state_cells` |
| `_cell_sharpe` | `cell_sharpe` |
| `_family_pbo` | `family_pbo` |
| `_family_dsr` | `family_dsr` |
| `_sign_agrees_early_late` | `sign_agrees_early_late` |
| `_map_verdict` | `map_verdict` |
| `_DAY_MS` | `DAY_MS` |
| `_PBO_PERIODS`, `_PBO_SPLITS` | `_PBO_PERIODS`, `_PBO_SPLITS` (stay private) |
| `VERDICT_*`, `BAR`, `ALPHA`, `MIN_N`, `DSR_FLOOR`, `PBO_CEIL`, `MINTRL_CONFIDENCE` | same names |

**Keep every docstring verbatim.** They record why the sign inversion, the `abs()`
folds, and the `day - lag_days` mapping are the way they are — that reasoning is the
main thing protecting the next reader.

`Z_WINDOW` moves too (it is `causal_zscore`'s default), but `Z_THRESHOLD`,
`CHANGE_SPAN`, `LEVEL_*`, `CHANGE_*`, `_LEVEL_STATES`, `_CHANGE_STATES` and
`_cell_family_key` **stay in `venue_premium.py`** — they are H14 vocabulary.

Then add the parameterised evaluator, which is `evaluate_premium_states`' body with
`_cell_family_key` swapped for a parameter and `bar` exposed:

```python
def evaluate_states(
    cells: list[AuditCell],
    family_key: Callable[[str], tuple[str, str]],
    *,
    bar: float = BAR,
    alpha: float = ALPHA,
    min_n: int = MIN_N,
) -> list[tuple[str, str]]:
    """Pre-committed verdict per cell, sharing ONE Holm haircut family.

    ``family_key`` maps a cell label to its ``(axis, direction)`` DSR/PBO
    sub-family. All ``cells`` share one Holm family; DSR/PBO are computed per
    sub-family, mirroring H8's per-indicator-axis grouping.

    ``bar`` is the effect-size floor IN THE UNITS OF THE OBSERVATION. H14 passes
    R-multiples and uses the 0.05R default; H15's forward panel passes
    vol-normalised returns and its own BAR_VOL. Passing an R-scaled bar to a
    raw-return panel makes every verdict unreachable — see spec Sec.6.
    """
    if not cells:
        return []
    cell_verdicts = evaluate_audit_cells(cells, bar=bar, alpha=alpha, min_n=min_n)

    by_family: dict[tuple[str, str], list[int]] = {}
    for i, c in enumerate(cells):
        by_family.setdefault(family_key(c.label), []).append(i)

    out: list[tuple[str, str]] = []
    for cell, cv in zip(cells, cell_verdicts, strict=True):
        if cv.decision in (DECISION_INSUFFICIENT, DECISION_CONCENTRATE):
            out.append(
                (
                    cell.label,
                    map_verdict(
                        cv.decision,
                        n_supp=cv.n_supp,
                        n_days_ok=False,
                        dsr=None,
                        pbo=None,
                        stable=False,
                    ),
                )
            )
            continue

        supp = np.asarray(cell.supp_r, dtype=np.float64)
        sharpe = cell_sharpe(supp)
        mintrl = min_track_record_length(
            abs(sharpe), target_sr=0.0, confidence=MINTRL_CONFIDENCE
        )
        n_days_ok = float(cv.n_supp) >= mintrl

        family_idx = by_family[family_key(cell.label)]
        family_arrays = [
            np.asarray(cells[j].supp_r, dtype=np.float64) for j in family_idx
        ]
        out.append(
            (
                cell.label,
                map_verdict(
                    cv.decision,
                    n_supp=cv.n_supp,
                    n_days_ok=n_days_ok,
                    dsr=family_dsr(supp, family_arrays),
                    pbo=family_pbo(family_arrays),
                    stable=sign_agrees_early_late(list(cell.supp_r)),
                ),
            )
        )
    return out
```

- [ ] **Step 5: Rewrite `venue_premium.py` to import from `state_audit`**

Replace the moved definitions with imports, and re-export so existing importers and
tests keep working unchanged:

```python
from analytics.state_audit import (
    ALPHA,
    BAR,
    DAY_MS,
    DSR_FLOOR,
    MIN_N,
    MINTRL_CONFIDENCE,
    PBO_CEIL,
    VERDICT_AVOID,
    VERDICT_BUILD,
    VERDICT_INSUFFICIENT,
    VERDICT_NO_EDGE,
    Z_WINDOW,
    build_state_cells,
    causal_zscore,
    cell_sharpe,
    collapse_to_daily,
    evaluate_states,
    family_dsr,
    family_pbo,
    map_verdict,
    sign_agrees_early_late,
)

__all__ = [
    "ALPHA",
    "BAR",
    "CHANGE_FALLING",
    "CHANGE_RISING",
    "CHANGE_SPAN",
    "DAY_MS",
    "DSR_FLOOR",
    "LEVEL_DEPRESSED",
    "LEVEL_ELEVATED",
    "LEVEL_NEUTRAL",
    "MIN_N",
    "MINTRL_CONFIDENCE",
    "PBO_CEIL",
    "VERDICT_AVOID",
    "VERDICT_BUILD",
    "VERDICT_INSUFFICIENT",
    "VERDICT_NO_EDGE",
    "Z_THRESHOLD",
    "Z_WINDOW",
    "build_premium_series",
    "build_state_cells",
    "causal_zscore",
    "cell_sharpe",
    "collapse_to_daily",
    "evaluate_premium_states",
    "evaluate_states",
    "family_dsr",
    "family_pbo",
    "label_changes",
    "label_levels",
    "map_verdict",
    "sign_agrees_early_late",
]
```

and reduce `evaluate_premium_states` to a wrapper that preserves its docstring:

```python
def evaluate_premium_states(cells: list[AuditCell]) -> list[tuple[str, str]]:
    """Pre-committed verdict per cell (spec Sec.7).

    H14's family is level(3) x direction(2) + change(2) x direction(2) = 10 cells
    on ``prem_adj``. Thin wrapper over the shared ``evaluate_states`` — the gate
    itself lives in ``analytics/state_audit.py`` so H14, H15 and later state tags
    cannot drift apart.
    """
    return evaluate_states(cells, _cell_family_key)
```

`tools/premium_state_audit.py` imports `_family_pbo` from `venue_premium` via
`# noqa: SLF001`. Update that import to `from analytics.state_audit import family_pbo`
and drop the now-unneeded `noqa`.

- [ ] **Step 6: Run the tests, then prove inertness against the baseline**

```bash
poetry run pytest tests/test_state_audit.py tests/ -q -k "premium or state_audit or venue"
poetry run python tools/premium_state_audit.py --source both > /tmp/h14-after.txt 2>&1
diff /tmp/h14-baseline.txt /tmp/h14-after.txt && echo "INERT ✓"
```

Expected: tests PASS and `diff` reports **no differences**.

**If the diff is non-empty the extraction is wrong — do not proceed and do not
"explain away" the delta.** A moved gate that produces different numbers is the exact
failure this task exists to prevent.

- [ ] **Step 7: Full gate + commit**

```bash
make lint-py && make typecheck && make test
git add analytics/state_audit.py analytics/venue_premium.py tools/premium_state_audit.py tests/test_state_audit.py
git commit -m "refactor(research): extract shared state-tag gate into state_audit.py

Behaviour-preserving move of H14's cell/family/verdict machinery so H14, H15
and later state tags share ONE gate implementation. The directional-DSR defect
shipped in H8 precisely because this logic was duplicated; a third copy for
H15 would be a third site for it.

Inertness proven by diffing tools/premium_state_audit.py output against the
pre-extraction baseline: identical.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `fetch_yahoo_daily` in `analytics/venue_fetch.py`

**Files:**

- Modify: `analytics/venue_fetch.py`
- Test: `tests/test_venue_fetch_yahoo.py`

**Interfaces:**

- Consumes: existing `Getter`, `http_get_json`, `_frame`, `DAY_MS` in the same module.
- Produces: `fetch_yahoo_daily(symbol: str, start_ms: int, end_ms: int, *, get: Getter = http_get_json) -> pd.DataFrame`
  returning columns `open_time` (int64 ms) and `close` (float64), sorted and
  de-duplicated. Also `YahooFetchError(RuntimeError)`.

**Measured facts you can rely on** (probed 2026-08-04, do not re-derive):

- The endpoint is keyless: `https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={s}&period2={e}&interval=1d`, `period1`/`period2` in **seconds**.
- **`http_get_json`'s existing `buibui-research/1.0` UA is accepted.** No UA change is
  needed. A request with *no* UA gets HTTP 429; a full Chrome UA string also got 429
  from this host, so do **not** "improve" the UA to a browser string.
- Bar timestamps are in **seconds** and must be multiplied by 1000.
- The response carries nulls: 7 null closes in the daily series. They must be dropped,
  not forward-filled.

- [ ] **Step 1: Write the failing test**

Create `tests/test_venue_fetch_yahoo.py`:

```python
"""Yahoo chart parsing — network-free via an injected getter."""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

from analytics.venue_fetch import YahooFetchError, fetch_yahoo_daily

_START_MS = 1_546_300_800_000  # 2019-01-01
_END_MS = 1_546_646_400_000  # 2019-01-05


def _payload(ts: list[int], close: list[float | None]) -> dict[str, Any]:
    return {
        "chart": {
            "result": [{"timestamp": ts, "indicators": {"quote": [{"close": close}]}}],
            "error": None,
        }
    }


def test_parses_seconds_into_millisecond_open_times() -> None:
    got = fetch_yahoo_daily(
        "JPY=X",
        _START_MS,
        _END_MS,
        get=lambda url: _payload([1_546_300_800, 1_546_387_200], [109.5, 109.7]),
    )
    assert list(got["open_time"]) == [1_546_300_800_000, 1_546_387_200_000]
    assert list(got["close"]) == [109.5, 109.7]
    assert got["open_time"].dtype == "int64"


def test_drops_null_closes_rather_than_filling() -> None:
    got = fetch_yahoo_daily(
        "JPY=X",
        _START_MS,
        _END_MS,
        get=lambda url: _payload([1, 2, 3], [109.5, None, 109.9]),
    )
    assert list(got["close"]) == [109.5, 109.9]


def test_requests_seconds_not_milliseconds() -> None:
    seen: list[str] = []

    def _get(url: str) -> Any:
        seen.append(url)
        return _payload([1], [109.5])

    fetch_yahoo_daily("JPY=X", _START_MS, _END_MS, get=_get)
    assert f"period1={_START_MS // 1000}" in seen[0]
    assert f"period2={_END_MS // 1000}" in seen[0]
    assert "interval=1d" in seen[0]


def test_rate_limit_raises_named_error_not_empty_frame() -> None:
    """A 429 must be loud. An empty frame would read as 'no data' downstream."""

    def _get(url: str) -> Any:
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    with pytest.raises(YahooFetchError, match="429"):
        fetch_yahoo_daily("JPY=X", _START_MS, _END_MS, get=_get)


def test_api_level_error_raises() -> None:
    def _get(url: str) -> Any:
        return {"chart": {"result": None, "error": {"description": "No data found"}}}

    with pytest.raises(YahooFetchError, match="No data found"):
        fetch_yahoo_daily("BOGUS=X", _START_MS, _END_MS, get=_get)


def test_symbol_is_url_quoted() -> None:
    seen: list[str] = []

    def _get(url: str) -> Any:
        seen.append(url)
        return _payload([1], [1.0])

    fetch_yahoo_daily("JPY=X", _START_MS, _END_MS, get=_get)
    assert "JPY%3DX" in seen[0]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_venue_fetch_yahoo.py -q`
Expected: FAIL — `ImportError: cannot import name 'YahooFetchError'`

- [ ] **Step 3: Implement**

Append to `analytics/venue_fetch.py` (and add `import urllib.parse` and
`import urllib.error` to the existing imports):

```python
_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"


class YahooFetchError(RuntimeError):
    """Yahoo returned no usable series — rate limit, bad symbol, or empty result.

    Raised rather than returning an empty frame on purpose: an empty frame is
    indistinguishable downstream from "this symbol legitimately has no bars in
    the window", which would silently produce an audit over no data.
    """


def fetch_yahoo_daily(
    symbol: str, start_ms: int, end_ms: int, *, get: Getter = http_get_json
) -> pd.DataFrame:
    """Daily closes for a Yahoo Finance symbol (e.g. ``JPY=X``), keyless.

    ``period1``/``period2`` are SECONDS, and returned bar timestamps are seconds
    — both converted here so callers stay in the repo's millisecond convention.

    Null closes are dropped, never forward-filled: a synthetic close would enter
    the weekly aggregation in Task 3 and silently alter a run count.
    """
    url = (
        f"{_YAHOO_CHART}/{urllib.parse.quote(symbol, safe='')}"
        f"?period1={start_ms // 1000}&period2={end_ms // 1000}&interval=1d"
    )
    try:
        payload = get(url)
    except urllib.error.HTTPError as exc:
        raise YahooFetchError(f"Yahoo HTTP {exc.code} for {symbol}") from exc

    chart = (payload or {}).get("chart") or {}
    err = chart.get("error")
    if err:
        desc = err.get("description") if isinstance(err, dict) else str(err)
        raise YahooFetchError(f"Yahoo error for {symbol}: {desc}")

    results = chart.get("result") or []
    if not results:
        raise YahooFetchError(f"Yahoo returned no result for {symbol}")

    block = results[0]
    stamps = block.get("timestamp") or []
    quotes = (block.get("indicators") or {}).get("quote") or [{}]
    closes = quotes[0].get("close") or []

    rows: list[tuple[int, float]] = [
        (int(t) * 1000, float(c))
        for t, c in zip(stamps, closes, strict=False)
        if c is not None
    ]
    if not rows:
        raise YahooFetchError(f"Yahoo returned no usable closes for {symbol}")
    return _frame(rows)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_venue_fetch_yahoo.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Smoke-test against the real endpoint once, by hand**

```bash
poetry run python -c "
from analytics.venue_fetch import fetch_yahoo_daily
df = fetch_yahoo_daily('JPY=X', 1483228800000, 2075000000000)
print(len(df), df['open_time'].min(), df['open_time'].max(), df['close'].iloc[-1])
"
```

Expected: ~2,500 rows and a plausible USD/JPY close (150–165 as of 2026-08). This is
the only network call in the task; if it 429s, wait a minute and retry rather than
changing the UA.

- [ ] **Step 6: Gate + commit**

```bash
make lint-py && make typecheck && make test
git add analytics/venue_fetch.py tests/test_venue_fetch_yahoo.py
git commit -m "feat(research): add keyless Yahoo daily fetcher for H15

fetch_yahoo_daily follows the existing injectable-getter pattern so the suite
stays network-free. Seconds/milliseconds converted at the boundary; null closes
dropped rather than filled; 429 and API-level errors raise YahooFetchError
instead of returning an empty frame that reads as 'no data' downstream.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `analytics/fx_carry.py` — the yen-strength state logic

**Files:**

- Create: `analytics/fx_carry.py`
- Test: `tests/test_fx_carry.py`

**Interfaces:**

- Consumes: `analytics.state_audit.causal_zscore`, `DAY_MS` (Task 1).
- Produces, for Task 4:
  - `london_trading_date(ts_ms: int) -> str` — `YYYY-MM-DD`
  - `iso_week_key(date_str: str) -> str` — `YYYY-Www`
  - `build_weekly_from_daily(daily: pd.DataFrame) -> pd.DataFrame` — columns
    `week_key`, `close`, `known_at_ms`, `end_date`
  - `yen_strength_runs(close: pd.Series) -> pd.Series`
  - `label_run_states(runs: pd.Series) -> pd.Series`
  - `label_magnitude_states(close: pd.Series) -> pd.Series`
  - `expand_to_days(weekly: pd.DataFrame, state_col: str, days: pd.Index) -> pd.Series`
  - `carry_family_key(label: str) -> tuple[str, str]`
  - Constants `RUN_LE1`, `RUN_EQ2`, `RUN_GE3`, `YEN_STRONG`, `MAG_NEUTRAL`, `YEN_WEAK`,
    `MAG_WINDOW`, `MAG_SPAN`, `MAG_THRESHOLD`

**Three traps, each measured or recorded — read before writing code:**

1. **Yahoo bars are anchored to Europe/London midnight, not UTC.** 1,157 of 1,982
   daily bars carry a 23:00 UTC timestamp (BST) and 824 carry 00:00 (GMT), so the
   UTC date is off by one for ~58% of the sample and the error flips with DST. Key
   every bar by its **London** date.
2. **Yen strength is USD/JPY going DOWN.** The magnitude axis therefore labels a
   **negative** z-score as `yen_strong`. Getting this backwards inverts the entire
   verdict and nothing else in the pipeline would notice.
3. **The state must be strictly causal.** A week's information is complete only after
   its last daily bar closes, so `known_at_ms = last_bar_open_ms + DAY_MS`. Day *D*
   may only use weeks with `known_at_ms <= D`'s 00:00 UTC.

- [ ] **Step 1: Write the failing test**

Create `tests/test_fx_carry.py`:

```python
"""USD/JPY carry-unwind state logic (H15). Pure — no network, no DB."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.fx_carry import (
    MAG_SPAN,
    MAG_WINDOW,
    RUN_EQ2,
    RUN_GE3,
    RUN_LE1,
    YEN_STRONG,
    YEN_WEAK,
    build_weekly_from_daily,
    carry_family_key,
    expand_to_days,
    iso_week_key,
    label_magnitude_states,
    label_run_states,
    london_trading_date,
    yen_strength_runs,
)

_DAY_MS = 86_400_000


def test_london_date_in_bst_uses_london_not_utc() -> None:
    """A 23:00 UTC bar in BST is the NEXT day in London — the 58% case."""
    # 2024-07-14 23:00 UTC == 2024-07-15 00:00 London (BST, UTC+1)
    assert london_trading_date(1_721_001_600_000) == "2024-07-15"


def test_london_date_in_gmt_matches_utc() -> None:
    # 2024-01-15 00:00 UTC == 2024-01-15 00:00 London (GMT)
    assert london_trading_date(1_705_276_800_000) == "2024-01-15"


def test_iso_week_key_is_monday_anchored_and_year_safe() -> None:
    assert iso_week_key("2024-01-01") == "2024-W01"  # Monday
    assert iso_week_key("2024-01-07") == "2024-W01"  # Sunday, same ISO week
    assert iso_week_key("2024-01-08") == "2024-W02"
    # 2019-12-30 is a Monday belonging to ISO week 2020-W01
    assert iso_week_key("2019-12-30") == "2020-W01"


def test_weekly_takes_last_close_in_each_iso_week() -> None:
    # Four consecutive GMT-season days: Thu, Fri (W02), Mon, Tue (W03)
    base = 1_705_276_800_000  # 2024-01-15 Monday 00:00 UTC
    daily = pd.DataFrame(
        {
            "open_time": [base, base + _DAY_MS, base + 7 * _DAY_MS, base + 8 * _DAY_MS],
            "close": [140.0, 141.0, 142.0, 143.0],
        }
    )
    wk = build_weekly_from_daily(daily)
    assert list(wk["week_key"]) == ["2024-W03", "2024-W04"]
    assert list(wk["close"]) == [141.0, 143.0]
    # known_at is one full day after the last bar's open
    assert list(wk["known_at_ms"]) == [base + 2 * _DAY_MS, base + 9 * _DAY_MS]


def test_run_counter_counts_consecutive_down_weeks() -> None:
    close = pd.Series([150.0, 149.0, 148.0, 147.0, 148.0, 147.0])
    runs = yen_strength_runs(close)
    assert np.isnan(runs.iloc[0])
    assert list(runs.iloc[1:]) == [1.0, 2.0, 3.0, 0.0, 1.0]


def test_run_states_bucket_at_2_and_3() -> None:
    runs = pd.Series([np.nan, 0.0, 1.0, 2.0, 3.0, 5.0])
    states = label_run_states(runs)
    assert states.isna().iloc[0]
    assert list(states.iloc[1:]) == [RUN_LE1, RUN_LE1, RUN_EQ2, RUN_GE3, RUN_GE3]


def test_magnitude_labels_falling_usdjpy_as_yen_strong() -> None:
    """Yen strength is USD/JPY DOWN, so a NEGATIVE z is yen_strong.

    Inverting this flips the whole verdict and nothing downstream would notice.
    """
    rng = np.random.default_rng(7)
    quiet = 150.0 + np.cumsum(rng.normal(0.0, 0.05, MAG_WINDOW + MAG_SPAN + 5))
    crash = quiet[-1] - np.arange(1, MAG_SPAN + 1) * 4.0
    spike = quiet[-1] + np.arange(1, MAG_SPAN + 1) * 4.0

    down = label_magnitude_states(pd.Series(np.concatenate([quiet, crash])))
    up = label_magnitude_states(pd.Series(np.concatenate([quiet, spike])))
    assert down.iloc[-1] == YEN_STRONG
    assert up.iloc[-1] == YEN_WEAK


def test_expand_to_days_is_strictly_causal() -> None:
    """Day D may only see weeks fully known BEFORE D 00:00 UTC."""
    known = 10 * _DAY_MS  # week becomes known at day 10 00:00 UTC
    weekly = pd.DataFrame({"known_at_ms": [known], "state": [RUN_GE3]})
    days = pd.Index([9, 10, 11], dtype="int64")
    out = expand_to_days(weekly, "state", days)
    assert pd.isna(out.loc[9])  # before it is known
    assert out.loc[10] == RUN_GE3  # known exactly at 00:00
    assert out.loc[11] == RUN_GE3


def test_expand_to_days_mutation_guard() -> None:
    """MUTATION PROOF: shifting the tag one week earlier must change day 9.

    Positive control first — without it, 'day 9 is NaN' is equally satisfied by
    correct causality and by a perturbation that did nothing at all.
    """
    week_ms = 7 * _DAY_MS
    weekly = pd.DataFrame({"known_at_ms": [10 * _DAY_MS], "state": [RUN_GE3]})
    days = pd.Index([9], dtype="int64")

    assert pd.isna(expand_to_days(weekly, "state", days).loc[9])

    leaked = weekly.assign(known_at_ms=weekly["known_at_ms"] - week_ms)
    # POSITIVE CONTROL: the perturbation genuinely moves this row.
    assert expand_to_days(leaked, "state", days).loc[9] == RUN_GE3


def test_carry_family_key_separates_the_two_axes() -> None:
    assert carry_family_key(f"{RUN_GE3}|market") == ("run", "market")
    assert carry_family_key(f"{YEN_STRONG}|market") == ("magnitude", "market")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_fx_carry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.fx_carry'`

- [ ] **Step 3: Implement `analytics/fx_carry.py`**

```python
"""H15 USD/JPY carry-unwind state tag — pure library.

Spec: docs/superpowers/specs/2026-08-04-h15-usdjpy-carry-unwind-design.md

PURE: no DB, no network, no file IO. Every threshold here is a-priori and was
written down in the spec before any conditional outcome was computed.

Three traps this module exists to contain, all measured 2026-08-04:

1. Yahoo bars are anchored to Europe/London midnight, so a bar's UTC date is
   off by one for ~58% of the sample and the error flips with DST. Everything
   is keyed by ``london_trading_date``.
2. Yen strength is USD/JPY going DOWN, so ``label_magnitude_states`` maps a
   NEGATIVE z-score to ``YEN_STRONG``. Inverting this flips the verdict and
   nothing downstream would notice.
3. A week is only known after its last bar closes. ``build_weekly_from_daily``
   publishes ``known_at_ms``, and ``expand_to_days`` admits a week only once
   ``known_at_ms <= day_start``.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from analytics.state_audit import DAY_MS, causal_zscore

_LONDON = ZoneInfo("Europe/London")

RUN_LE1 = "run_le1"
RUN_EQ2 = "run_eq2"
RUN_GE3 = "run_ge3"

YEN_STRONG = "yen_strong"
MAG_NEUTRAL = "mag_neutral"
YEN_WEAK = "yen_weak"

MAG_WINDOW = 52
MAG_SPAN = 4
MAG_THRESHOLD = 1.0

_RUN_STATES = frozenset({RUN_LE1, RUN_EQ2, RUN_GE3})
_MAG_STATES = frozenset({YEN_STRONG, MAG_NEUTRAL, YEN_WEAK})


def london_trading_date(ts_ms: int) -> str:
    """The bar's Europe/London calendar date — the true FX trading day."""
    return datetime.fromtimestamp(ts_ms / 1000, _LONDON).strftime("%Y-%m-%d")


def iso_week_key(date_str: str) -> str:
    """``YYYY-Www`` ISO-8601 week key — Monday-anchored and year-boundary safe."""
    iso = date.fromisoformat(date_str).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def build_weekly_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    """Collapse London-dated daily bars into ISO weeks (spec Sec.4).

    Weekly bars are derived here rather than taken from Yahoo's ``1wk`` feed
    because that feed inherits the London-midnight anchoring — the two
    anchorings disagree by a whole run step on the thesis's headline event.

    ``known_at_ms`` is one full day after the week's last bar OPEN, i.e. the
    moment that bar has closed. This is the timestamp ``expand_to_days`` gates
    on; using the bar's open would leak up to 24h of look-ahead.
    """
    cols = ["week_key", "close", "known_at_ms", "end_date"]
    if daily.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in cols})
    df = daily.sort_values("open_time").reset_index(drop=True).copy()
    df["end_date"] = [london_trading_date(int(t)) for t in df["open_time"]]
    df["week_key"] = [iso_week_key(d) for d in df["end_date"]]
    last = df.groupby("week_key", as_index=False).last()
    last["known_at_ms"] = last["open_time"].astype("int64") + DAY_MS
    return last.sort_values("week_key").reset_index(drop=True)[cols]


def yen_strength_runs(close: pd.Series) -> pd.Series:
    """Consecutive weeks in which USD/JPY closed BELOW the prior week.

    The first element is NaN — it has no predecessor, so its run is undefined
    rather than 0. Warm-up NaN is preserved throughout this module so a
    warm-up row can never masquerade as a real ``run_le1`` observation.
    """
    prior = close.shift(1)
    down = close < prior
    runs: list[float] = []
    current = 0
    for i in range(len(close)):
        if bool(prior.isna().iloc[i]):
            runs.append(float("nan"))
            continue
        current = current + 1 if bool(down.iloc[i]) else 0
        runs.append(float(current))
    return pd.Series(runs, index=close.index, dtype="float64")


def label_run_states(runs: pd.Series) -> pd.Series:
    """Pre-registered buckets: <=1, exactly 2, >=3 (spec Sec.5)."""
    out: pd.Series = pd.Series(
        np.where(runs >= 3, RUN_GE3, np.where(runs == 2, RUN_EQ2, RUN_LE1)),
        index=runs.index,
    )
    return out.where(runs.notna(), other=np.nan)


def label_magnitude_states(close: pd.Series) -> pd.Series:
    """Causal z of the trailing ``MAG_SPAN``-week USD/JPY log return (spec Sec.5).

    SIGN: yen strength is USD/JPY FALLING, so a NEGATIVE z is ``YEN_STRONG``.
    This is the single easiest thing in H15 to invert, and an inversion would
    flip the verdict silently — ``test_magnitude_labels_falling_usdjpy_as_yen_
    strong`` is the guard.
    """
    ret = np.log(close.astype("float64")).diff(MAG_SPAN)
    z = causal_zscore(ret, window=MAG_WINDOW)
    out: pd.Series = pd.Series(
        np.where(
            z <= -MAG_THRESHOLD,
            YEN_STRONG,
            np.where(z >= MAG_THRESHOLD, YEN_WEAK, MAG_NEUTRAL),
        ),
        index=z.index,
    )
    return out.where(z.notna(), other=np.nan)


def expand_to_days(weekly: pd.DataFrame, state_col: str, days: pd.Index) -> pd.Series:
    """Map each UTC day number to the last week KNOWN before that day started.

    ``days`` holds day numbers (``ms // DAY_MS``). A day is tagged with the most
    recent week whose ``known_at_ms <= day * DAY_MS``; days preceding any known
    week are NaN and get dropped by the caller.

    ``searchsorted(..., side="right") - 1`` is the causality guarantee. Do not
    replace it with a positional ``shift`` — the day index has gaps, and a
    positional shift slides VALUES across a gap, silently borrowing a
    neighbouring week's state.
    """
    usable = weekly.dropna(subset=[state_col]).sort_values("known_at_ms")
    if usable.empty or len(days) == 0:
        return pd.Series([np.nan] * len(days), index=days, dtype="object")
    known = usable["known_at_ms"].to_numpy(dtype="int64")
    states = usable[state_col].to_numpy(dtype=object)
    day_start = days.to_numpy(dtype="int64") * DAY_MS
    pos = np.searchsorted(known, day_start, side="right") - 1
    tagged = np.where(pos >= 0, states[np.clip(pos, 0, None)], None)
    out: pd.Series = pd.Series(tagged, index=days, dtype="object")
    return out.where(out.notna(), other=np.nan)


def carry_family_key(label: str) -> tuple[str, str]:
    """``"state|direction"`` -> ``(axis, direction)`` DSR/PBO sub-family.

    The two axes are NEVER crossed (spec Sec.5) — crossing takes the family
    from 6 cells to 9 plus complements, which this repo's own H14 spec names a
    multiple-testing machine.
    """
    state, direction = label.rsplit("|", 1)
    if state in _RUN_STATES:
        return "run", direction
    if state in _MAG_STATES:
        return "magnitude", direction
    raise ValueError(f"unrecognized state token {state!r} in label {label!r}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_fx_carry.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Run the mutation manually to confirm the guard is not vacuous**

A guard test that cannot fail is worse than no test. Prove this one bites:

```bash
# Temporarily break causality: side="right" -> side="left" without the -1
poetry run python - <<'PY'
import re, pathlib
p = pathlib.Path("analytics/fx_carry.py")
s = p.read_text()
p.write_text(s.replace('side="right") - 1', 'side="right")'))
PY
poetry run pytest tests/test_fx_carry.py -q -k causal_or_mutation || echo "GUARD BITES ✓"
git checkout analytics/fx_carry.py
poetry run pytest tests/test_fx_carry.py -q
```

Expected: the mutated run **FAILS** (`test_expand_to_days_is_strictly_causal`), and
the restored run passes. If the mutated run passes, the guard is vacuous — fix the
test before continuing.

- [ ] **Step 6: Gate + commit**

```bash
make lint-py && make typecheck && make test
git add analytics/fx_carry.py tests/test_fx_carry.py
git commit -m "feat(h15): add USD/JPY yen-strength state logic

Pure module: London-date keying (Yahoo bars are London-midnight anchored, so
the UTC date is wrong for ~58% of the sample and flips with DST), ISO-week
aggregation derived in-repo rather than from Yahoo's 1wk feed, the
consecutive-down-week run counter, and the causal 4-week magnitude z-score.

Yen strength is USD/JPY DOWN, so a negative z is yen_strong — guarded by an
explicit sign test, since an inversion would flip the verdict silently.

expand_to_days admits a week only once known_at_ms <= day start, and its
causality guard is mutation-proven with a positive control.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `tools/carry_unwind_audit.py` — the runner and the verdict

**Files:**

- Create: `tools/carry_unwind_audit.py`
- Modify: `Makefile`
- Test: extends `tests/test_fx_carry.py` with the outcome-panel helpers

**Interfaces:**

- Consumes: everything produced by Tasks 1–3, plus
  `analytics.store.DEFAULT_DB_PATH` and `analytics.audit_guard.AuditCell`.
- Produces: a printed markdown table plus a verdict summary; exit code 0.

**Model this file on `tools/premium_state_audit.py`** — same shape, same flag
conventions, same table discipline. Read it first.

**Pre-registered constants for this task** (spec §6, §7):

```python
BAR_VOL = 0.02  # sigma-units, forward panel  (0.065%/day, 0.38 ann-Sharpe)
BAR_LEDGER = 0.05  # R-units, ledger panel — unchanged from H14
VOL_WINDOW = 30  # causal trailing realized-vol window, days
FX_START_MS = 1_483_228_800_000  # 2017-01-01, z-score warm-up margin only
```

- [ ] **Step 1: Write the failing test for the outcome panel**

Append to `tests/test_fx_carry.py`:

```python
def test_vol_normalise_is_causal_and_dimensionless() -> None:
    """r_t / sigma_{t-1}: today's return may never touch today's vol estimate."""
    from tools.carry_unwind_audit import VOL_WINDOW, vol_normalise

    rng = np.random.default_rng(3)
    r = pd.Series(rng.normal(0.0, 0.03, 200))
    z = vol_normalise(r)

    assert z.iloc[:VOL_WINDOW].isna().all()  # warm-up stays NaN
    assert 0.7 < float(z.dropna().std(ddof=1)) < 1.5  # dimensionless, ~1

    # Causality: perturbing ONLY the last return must not move any earlier z.
    bumped = r.copy()
    bumped.iloc[-1] = bumped.iloc[-1] + 10.0
    z2 = vol_normalise(bumped)
    pd.testing.assert_series_equal(z.iloc[:-1], z2.iloc[:-1])
    # POSITIVE CONTROL: the perturbation genuinely moved the final value.
    assert float(z2.iloc[-1]) != float(z.iloc[-1])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_fx_carry.py -q -k vol_normalise`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.carry_unwind_audit'`

- [ ] **Step 3: Implement the runner**

Create `tools/carry_unwind_audit.py`. Structure, mirroring `premium_state_audit.py`:

```python
#!/usr/bin/env python3
"""H15 — USD/JPY carry-unwind state tag audit.

Spec: docs/superpowers/specs/2026-08-04-h15-usdjpy-carry-unwind-design.md

Runs the pre-committed gate over two panels:
  forward — BTCUSDT daily log return / causal trailing 30d vol  (decides)
  ledger  — per-UTC-day mean R of backtest/live trades          (secondary)

The two panels use DIFFERENT bars because their observations have different
units: BAR_VOL = 0.02 sigma-units vs BAR_LEDGER = 0.05 R. Passing the R bar to
the forward panel makes every verdict unreachable — spec Sec.6.
"""
```

Then, in order:

- **(a)** `sys.path` bootstrap + imports, copying the `REPO_ROOT` pattern from
  `premium_state_audit.py:41-50`.
- **(b)** `refresh_fx_prices(db: Path) -> None` — `fetch_yahoo_daily("JPY=X",
  FX_START_MS, now_ms)`, upsert into an `fx_prices` table (`symbol`, `open_time`,
  `close`). Create the table if absent. Use the repo's existing upsert helper;
  **never** switch `_upsert` to an implicit replacement scan.
- **(c)** `vol_normalise(returns: pd.Series) -> pd.Series`:

```python
def vol_normalise(returns: pd.Series) -> pd.Series:
    """``r_t / sigma_{t-1}`` with a causal trailing ``VOL_WINDOW`` vol.

    The ``.shift(1)`` is the causality guarantee: today's return must not enter
    its own scaling denominator. Asserted by a perturbation test with a
    positive control — do not remove it as a warm-up convenience.
    """
    sigma = returns.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std(ddof=1).shift(1)
    return returns / sigma.replace(0.0, np.nan)
```

- **(d)** `load_btc_daily(conn) -> pd.DataFrame` — `SELECT open_time, close FROM ohlcv
  WHERE symbol='BTCUSDT' AND timeframe='1d' ORDER BY open_time`.
- **(e)** `build_forward_panel(btc, weekly) -> pd.DataFrame` — daily log returns →
  `vol_normalise` → day numbers → `expand_to_days` for each axis → long frame with
  columns `day`, `direction` (constant `"market"`), `mean_r`, `state`.
  **Reuse `build_state_cells` unchanged** by supplying the constant direction.
- **(f)** `build_ledger_panel(conn, weekly, source) -> pd.DataFrame` — reuse
  `collapse_to_daily` with the trade query from `premium_state_audit.py:94-101`.
- **(g)** Cells per axis, concatenated into ONE Holm family per panel, then
  `evaluate_states(cells, carry_family_key, bar=...)`.
- **(h)** A balance report (state → n_days, share) printed before the verdict table —
  H14's `_level_balance_report` is the model.
- **(i)** Table columns: `axis | state | n_days | mean | ci_lo | ci_hi | adj_p | dsr |
  pbo | mintrl | stable | verdict`.
- **(j)** A per-state risk block — realized vol, downside semideviation, worst day —
  printed under a heading that says **"descriptive, not gated"**.
- **(k)** `build_parser()` with `--db`, `--source {forward,ledger,both}` (default
  `both`), `--min-n` (default 30), `--refresh`.
- **(l)** `main() -> int` returning 0.

**Effective-finding-count line.** After the table, print:

```text
NOTE: each axis has 3 mutually exclusive states, so its 3 cell-vs-complement
tests are ~2 effective comparisons, not 3. A cell count is not a finding count.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_fx_carry.py -q`
Expected: PASS (12 tests)

- [ ] **Step 5: Add the Makefile target**

The recipe line must be indented with a **real tab**, not spaces — make will fail with
"missing separator" otherwise.

<!-- markdownlint-disable MD010 -->

```makefile
buibui-carry-unwind-audit:  ## H15 USD/JPY carry-unwind state audit
	poetry run python tools/carry_unwind_audit.py $(ARGS)
```

<!-- markdownlint-enable MD010 -->

Note the `$(ARGS)` passthrough — it matches the other `buibui-*` targets, and the
Makefile `$(ARGS)` inconsistency is a known item on the cheap queue.

- [ ] **Step 6: Run the audit for real and capture the output**

```bash
make buibui-carry-unwind-audit ARGS="--refresh --source both" 2>&1 | tee /tmp/h15-audit.txt
```

If it fails with `IOException: Could not set lock on file analytics.db`, a
`signal watch` daemon holds it — DuckDB is single-writer. Identify the holder with
`ps -o pid,etime,cmd -p <pid>` and wait rather than treating it as a data problem.

**Sanity-check the balance report against the spec's §3 table before reading any
verdict.** It should show roughly 82% / 10% / 8% across `run_le1` / `run_eq2` /
`run_ge3`. If it does not, the tag pipeline is wrong and the verdict is meaningless —
stop and report.

- [ ] **Step 7: Full gate + regression**

```bash
make lint-py && make typecheck && make test && make test-regression
```

`make test-regression` must show goldens **unmoved** — H15 adds a new tool and does
not change the backtest pipeline, so any golden movement means something leaked into
shared code. Investigate rather than regenerating.

- [ ] **Step 8: Commit**

```bash
git add tools/carry_unwind_audit.py tests/test_fx_carry.py Makefile
git commit -m "feat(h15): add the USD/JPY carry-unwind audit runner

Two panels through the pre-committed gate: forward (BTCUSDT daily log return
over causal trailing 30d vol, BAR_VOL 0.02 sigma-units, decides) and ledger
(per-UTC-day mean R, BAR_LEDGER 0.05 R, secondary and inheriting the frozen
detector family).

Risk descriptives are printed but never gated, and the table carries an
effective-finding-count note: 3 mutually exclusive states per axis are ~2
effective comparisons, not 3.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Write the verdict and close the axis

**Files:**

- Create: `docs/audits/2026-08-04-h15-usdjpy-carry-unwind.md`
- Modify: `docs/plans/thesis-inbox.md`, `CLAUDE.md`

**This task is not optional and not cosmetic.** The whole point of the session is
converting a filed thesis into a closed one; an audit whose result is never written
down has not closed anything.

- [ ] **Step 1: Write the verdict doc**

Model it on `docs/audits/2026-08-04-h14-coinbase-premium-state-tag.md`. It must state,
in plain language before any statistics:

- the headline in one sentence,
- the money translation (what the effect is worth per year at operator scale, or that
  there is none),
- backtest-vs-live standing,
- the effective finding count, not the cell count,
- **every gate leg's value for each BUILD/AVOID cell** — the H8 lesson is that an
  unreported leg is an unimplemented leg,
- whether the `abs()` fold was applied, and the disclosure that folding makes the gate
  marginally more permissive.

- [ ] **Step 2: Update `thesis-inbox.md`**

Change the 2026-08-03 @CakeBaBa entry's `Status: NEW` to the verdict, with a one-line
link to the audit doc. Record explicitly that the entry's own "n is tiny, will fail
MinTRL" caveat was **wrong** — measured, ≥2 fires in 18.0% of weeks — so the next
reader does not inherit the mistake.

- [ ] **Step 3: Update the conditioning scoreboard in `CLAUDE.md`**

Add H15 to the sleeve/axis verdict record. If the verdict is NO-EDGE, say so as
plainly as the shelved sleeves are recorded — a negative result is a result.

- [ ] **Step 4: Gate + commit**

```bash
make lint-md
git add docs/audits/2026-08-04-h15-usdjpy-carry-unwind.md docs/plans/thesis-inbox.md CLAUDE.md
git commit -m "docs(h15): record the USD/JPY carry-unwind verdict"
```

Note: `docs/plans/` is gitignored — `thesis-inbox.md` will not stage. That is expected;
edit it anyway for local continuity and do **not** `git add -f` it (the repo's
visibility oscillates and it may be world-readable).

---

## Self-Review

**Spec coverage:**

| Spec section | Implemented by |
| --- | --- |
| §3 data, 2017 fetch start | Task 2 Step 3, Task 4 `FX_START_MS` |
| §4 London anchoring + ISO weeks | Task 3 `london_trading_date`, `build_weekly_from_daily` |
| §5 run axis, magnitude axis, causality rule | Task 3 `label_run_states`, `label_magnitude_states`, `expand_to_days` |
| §5 axes never crossed | Task 3 `carry_family_key` |
| §6 unit = one UTC day | Task 4 `build_forward_panel`, `collapse_to_daily` |
| §6 vol normalisation + `BAR_VOL` | Task 4 `vol_normalise`, `BAR_VOL` |
| §6 secondary + descriptive panels | Task 4 steps 6, 10 |
| §7 seven gate legs | Task 1 `evaluate_states` / `map_verdict` |
| §7 `abs()` fold | Task 1 (inherited verbatim; Task 5 discloses) |
| §7 effective finding count | Task 4 Step 3 item 12, Task 5 Step 1 |
| §8 decision rule | Task 5 Step 1 |
| §9 architecture | Tasks 1–4 |
| §10 five named tests | T1 S6, T3 S1/S5, T2 S1, T4 S1 |
| §11 deliverables | Task 5 |

**Placeholder scan:** none — every code step carries runnable code, and Task 4's
step 3 enumerates twelve concrete functions rather than "implement the runner".

**Type consistency:** `expand_to_days(weekly, state_col, days)` is called with the
same signature in Tasks 3 and 4. `build_weekly_from_daily` produces `known_at_ms`,
which is the only column `expand_to_days` gates on. `evaluate_states(cells,
family_key, *, bar=...)` is defined in Task 1 and called in Task 4 with
`carry_family_key` from Task 3. `cell_sharpe` / `family_pbo` / `family_dsr` /
`sign_agrees_early_late` / `map_verdict` are de-underscored consistently everywhere.
