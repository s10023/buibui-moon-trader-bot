# /research-distil Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a skill that turns book/repo/paper knowledge into **at most three** power-priced,
pre-registered hypotheses per run, on top of one consolidated effect-size inversion.

**Architecture:** Promote the duplicated required-effect-size inversion into
`analytics/research_guards/power.py` and make both existing tools delegate to it. Add a thin CLI
(`tools/distil_power.py`) that the skill must run for its power gate. Add the skill itself as a
single `SKILL.md` matching `/ingest-x`, routing into the existing `thesis-inbox.md` /
`mechanics-backlog.md` intake behind one human review gate.

**Tech Stack:** Python 3.11+, Poetry, pytest, mypy strict, ruff. Pure stdlib maths — no new
dependency.

**Spec:** `docs/superpowers/specs/2026-08-14-research-distil-design.md`

## Global Constraints

- `make lint-py` (ruff format + lint), `make typecheck` (mypy strict), `make test` must all pass
  before each commit. **State which you ran.**
- `make test-regression` **does NOT apply** — the diff touches `analytics/research_guards/`,
  `tools/`, `tests/` and `.claude/skills/`, none of which is in the backtest surface
  (`analytics/backtest/`, `analytics/strategies/`, `analytics/signal_config.py`,
  `config/*signal_watch*.toml`, `config/strategy_params.toml`, `tests/fixtures/`, `poetry.lock`).
  Say so explicitly rather than silently skipping it.
- **All functions carry type annotations including return types** (`-> None` on tests). mypy is
  strict.
- `analytics/research_guards/` is pure math: **no DB, no IO, no network**. Keep it that way.
- **Never restate the powered-null criterion.** `analytics.audit_guard.powered_null` owns
  two-sided containment; `analytics.sl_horizon.negative_claim_licensed` owns the one-sided
  best-of-k variant. Call them.
- `analytics.db` is 321 MB and present. Both tools read it **read-only**. A systemd timer owns
  writes to it at `:01/16/31/46` — do not start any daemon beside it.
- Branch is `docs/research-canon-audit`, already 4 commits deep. **A second Claude session
  (`buibui-moon-trader-bot-87`) shares this working tree and has agreed not to move HEAD.** Do
  not switch branches.
- ONE PR at the end, not per task.

---

### Task 1: Consolidate the effect-size inversion

**Files:**

- Create: `analytics/research_guards/power.py`
- Modify: `analytics/research_guards/__init__.py`
- Test: `tests/test_research_guards_power.py`

**Interfaces:**

- Consumes: `analytics.research_guards.dsr.deflated_sharpe_ratio`,
  `analytics.research_guards.psr.probabilistic_sharpe_ratio`,
  `analytics.research_guards.gate.GATE_DSR`
- Produces: `required_sharpe(n_obs, *, n_trials=None, sr_variance=None, benchmark_sr=None,
  skew=0.0, kurtosis=3.0, target=GATE_DSR) -> float`, re-exported from
  `analytics.research_guards`. Returns `math.inf` when unreachable. Tasks 2, 3 and 4 all call it.

**Context the implementer needs.** Two implementations of this inversion exist today and agree
to 0.0000% across a 16-cell grid — measured 2026-08-14. This is therefore a **de-duplication
with no expected numeric change**. If any number moves, the promotion is wrong; do not
"fix" the number.

`probabilistic_sharpe_ratio` takes its benchmark as `sr_benchmark` (not `benchmark`) and
**raises `ValueError`** on a non-positive variance term. `tools/multi_regime_power.required_sr`
treats that same degenerate case as `z = +inf`, i.e. *attained*. The new function must mirror
that, or Task 3's byte-identical check will fail.

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_guards_power.py`:

```python
"""Tests for `analytics/research_guards/power.py` — the effect-size bar.

The 16-cell grid below is an EQUIVALENCE PIN, not a sample. It was measured on
2026-08-14 against the two pre-promotion implementations
(`tools/era_power_price.required_sharpe` and `tools/multi_regime_power.required_sr`),
which agreed to 0.0000%. Pinning it turns that agreement from an observation
into an invariant: if a future edit moves any of these, the bar and the gate
have silently drifted apart.
"""

from __future__ import annotations

import math

import pytest

from analytics.research_guards import expected_max_sharpe, required_sharpe

# (n_obs, n_trials, sr_variance) -> required Sharpe, measured 2026-08-14.
PIN: dict[tuple[int, int, float], float] = {
    (200, 1, 0.0): 0.116999,
    (200, 16, 0.05): 0.527028,
    (200, 44, 0.05): 0.625443,
    (200, 320, 0.05): 0.785352,
    (1000, 1, 0.0): 0.052076,
    (1000, 16, 0.05): 0.457288,
    (1000, 44, 0.05): 0.553837,
    (1000, 320, 0.05): 0.710213,
    (4000, 1, 0.0): 0.026015,
    (4000, 16, 0.05): 0.429779,
    (4000, 44, 0.05): 0.525698,
    (4000, 320, 0.05): 0.680847,
    (20000, 1, 0.0): 0.011632,
    (20000, 16, 0.05): 0.414715,
    (20000, 44, 0.05): 0.510313,
    (20000, 320, 0.05): 0.664831,
}


@pytest.mark.parametrize(("key", "expected"), sorted(PIN.items()))
def test_multiplicity_path_matches_the_pin(
    key: tuple[int, int, float], expected: float
) -> None:
    n_obs, n_trials, sr_variance = key
    got = required_sharpe(n_obs, n_trials=n_trials, sr_variance=sr_variance)
    assert got == pytest.approx(expected, abs=5e-6)


@pytest.mark.parametrize(("key", "expected"), sorted(PIN.items()))
def test_benchmark_path_agrees_with_multiplicity_path(
    key: tuple[int, int, float], expected: float
) -> None:
    """The two entry paths must not disagree — that is the whole point."""
    n_obs, n_trials, sr_variance = key
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    got = required_sharpe(n_obs, benchmark_sr=sr0)
    assert got == pytest.approx(expected, abs=5e-6)


def test_rises_with_trial_count() -> None:
    """Trial count dominates n — the bar must move a long way on it."""
    one = required_sharpe(4000, n_trials=1, sr_variance=0.05)
    many = required_sharpe(4000, n_trials=320, sr_variance=0.05)
    assert many > one * 20


def test_falls_with_sample_size() -> None:
    assert required_sharpe(20000, n_trials=16, sr_variance=0.05) < required_sharpe(
        200, n_trials=16, sr_variance=0.05
    )


def test_unreachable_returns_inf_not_a_big_number() -> None:
    """A bar no Sharpe can clear is a finding, and `inf` is how it is said.

    The PSR z-statistic is bounded above by ~sqrt(2*(n_obs-1)), so at small
    `n_obs` the gate is unreachable at ANY Sharpe rather than merely large.
    """
    assert required_sharpe(2, n_trials=320, sr_variance=0.05) == math.inf


def test_n_obs_below_two_is_unreachable() -> None:
    assert required_sharpe(1, n_trials=1, sr_variance=0.0) == math.inf


def test_rejects_both_benchmark_sources() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        required_sharpe(1000, n_trials=16, sr_variance=0.05, benchmark_sr=0.4)


def test_rejects_neither_benchmark_source() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        required_sharpe(1000)


def test_rejects_half_a_multiplicity_pair() -> None:
    with pytest.raises(ValueError, match="both required"):
        required_sharpe(1000, n_trials=16)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_research_guards_power.py -q`

Expected: collection error — `ImportError: cannot import name 'required_sharpe' from
'analytics.research_guards'`.

- [ ] **Step 3: Write the implementation**

Create `analytics/research_guards/power.py`:

```python
"""Required-effect-size inversion — how large a Sharpe the gate demands.

Promoted 2026-08-14 from two independent implementations that agreed to
0.0000% across a 16-cell grid but were held to it by nothing:
``tools/era_power_price.required_sharpe``, which inverted the production
``deflated_sharpe_ratio``, and ``tools/multi_regime_power.required_sr``, which
re-derived the PSR z-formula by hand and bisected on that. The second is the
shape the first's own docstring names as "how a spec and a driver come to
disagree", which is why the hand-derived copy is the one that goes.

This module owns the **effect-size bar only**. The powered-null containment
criterion belongs to :func:`analytics.audit_guard.powered_null`, and the
one-sided best-of-k variant to
:func:`analytics.sl_horizon.negative_claim_licensed`. Neither is restated here,
and neither should be: six sites in this repo have already spelled that rule
six different ways.
"""

from __future__ import annotations

import math

from analytics.research_guards.dsr import deflated_sharpe_ratio
from analytics.research_guards.gate import GATE_DSR
from analytics.research_guards.psr import probabilistic_sharpe_ratio

__all__ = ["required_sharpe"]

_MAX_SHARPE = 1e6
_ITERS = 200


def required_sharpe(
    n_obs: int,
    *,
    n_trials: int | None = None,
    sr_variance: float | None = None,
    benchmark_sr: float | None = None,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    target: float = GATE_DSR,
) -> float:
    """Smallest Sharpe whose deflated/probabilistic SR reaches ``target``.

    Provide **exactly one** source of the benchmark, mirroring
    :func:`deflated_sharpe_ratio`'s own contract:

    * ``n_trials`` + ``sr_variance`` — inverts :func:`deflated_sharpe_ratio`, so
      the benchmark is the expected maximum Sharpe of that trial family.
    * ``benchmark_sr`` — inverts :func:`probabilistic_sharpe_ratio` against a
      benchmark already in hand.

    Both paths invert a **production** function rather than a hand-derived
    closed form, which is what keeps the bar and the gate in agreement by
    construction.

    ``kurtosis`` is **non-excess** (normal = 3.0), matching
    :func:`probabilistic_sharpe_ratio`.

    Returns ``math.inf`` when ``target`` is unreachable at any finite Sharpe.
    That is a finding rather than an error: the PSR z-statistic is bounded above
    by roughly ``sqrt(2 * (n_obs - 1))``, so a small sample against a wide trial
    family cannot clear the gate at *any* effect size, and reporting a huge
    finite number instead would read as "nearly there".
    """
    has_multiplicity = n_trials is not None or sr_variance is not None
    if has_multiplicity == (benchmark_sr is not None):
        raise ValueError(
            "provide exactly one of (n_trials + sr_variance) or benchmark_sr"
        )
    if has_multiplicity and (n_trials is None or sr_variance is None):
        raise ValueError("n_trials and sr_variance are both required together")
    if n_obs < 2:
        return math.inf

    def attained(sr: float) -> float:
        """Probability reached at ``sr``; degenerate variance counts as attained.

        ``probabilistic_sharpe_ratio`` raises on a non-positive variance term
        where the pre-promotion ``required_sr`` treated it as ``z = +inf``.
        Mirroring that keeps the promoted reports byte-identical.
        """
        try:
            if benchmark_sr is not None:
                return probabilistic_sharpe_ratio(
                    sr,
                    n_obs,
                    skew=skew,
                    kurtosis=kurtosis,
                    sr_benchmark=benchmark_sr,
                )
            assert n_trials is not None and sr_variance is not None
            return deflated_sharpe_ratio(
                sr,
                n_obs,
                n_trials=n_trials,
                sr_variance=sr_variance,
                skew=skew,
                kurtosis=kurtosis,
            )
        except ValueError:
            return 1.0

    lo, hi = 0.0, 1.0
    for _ in range(_ITERS):
        if attained(hi) >= target:
            break
        lo, hi = hi, hi * 2.0
        if hi > _MAX_SHARPE:
            return math.inf
    for _ in range(_ITERS):
        mid = (lo + hi) / 2.0
        if attained(mid) >= target:
            hi = mid
        else:
            lo = mid
    return hi
```

- [ ] **Step 4: Export it**

In `analytics/research_guards/__init__.py`, add the import beside the existing ones (keep
alphabetical order within the import block) and add `"required_sharpe"` to `__all__`:

```python
from analytics.research_guards.power import required_sharpe
```

- [ ] **Step 5: Run the tests**

Run: `poetry run pytest tests/test_research_guards_power.py -q`

Expected: PASS, all cases.

If `test_unreachable_returns_inf_not_a_big_number` fails because a finite value came back, do
**not** raise `_MAX_SHARPE`. Report it — it means the asymptote reasoning in the docstring is
wrong and Task 3's boundary check needs rethinking before anything else proceeds.

- [ ] **Step 6: Gates and commit**

```bash
make lint-py && make typecheck && make test
git add analytics/research_guards/power.py analytics/research_guards/__init__.py tests/test_research_guards_power.py
git commit -m "feat(research-guards): own the effect-size bar in one function

Two implementations of the required-Sharpe inversion existed and agreed to
0.0000% across 16 cells, held to it by nothing. Promotes the one that inverts
the production deflated_sharpe_ratio and pins the grid as an equivalence test.
Does NOT touch the powered-null criterion, which audit_guard.powered_null
already owns."
```

State in the commit report that `make test-regression` does not apply and why.

---

### Task 2: Delegate `era_power_price`, and drop its threshold copy

**Files:**

- Modify: `tools/era_power_price.py:63` (local `GATE_DSR`), `:185-230` (`required_sharpe`)

**Interfaces:**

- Consumes: `required_sharpe` from Task 1.
- Produces: no new symbols. `tools/era_power_price.py`'s CLI and printed report are **unchanged**.

**Context.** This file already imports from `analytics.research_guards` (line 60) and from
`analytics.audit_guard` (line 57), yet redefines `GATE_DSR = 0.95` locally at line 63. That is a
hardcoded copy of a threshold whose single home is `analytics/research_guards/gate.py`, and it
is the same pattern that produced four copies of the gate itself. Remove it in this task.

Leave `containment_half_width` and `null_is_licensable` **exactly as they are** — they already
delegate to `audit_guard.powered_null`, which is correct.

- [ ] **Step 1: Capture the before-report**

```bash
poetry run python tools/era_power_price.py --db analytics.db > /tmp/era_before.txt 2>&1
wc -l /tmp/era_before.txt
```

Expected: a non-empty report. If it errors, stop and report — the byte-identical proof is the
only evidence this refactor is safe, and without a baseline there is none.

- [ ] **Step 2: Replace the local `required_sharpe` body with a delegation**

Delete the whole `def required_sharpe(...)` block (lines ~185-230) and its now-unused `math`
usage inside it. Add to the import block:

```python
from analytics.research_guards import deflated_sharpe_ratio, required_sharpe
```

Remove the standalone `from analytics.research_guards import deflated_sharpe_ratio` line if
`deflated_sharpe_ratio` is no longer referenced anywhere else in the file — check with
`grep -n deflated_sharpe_ratio tools/era_power_price.py` before deleting.

- [ ] **Step 3: Delete the local threshold and import the real one**

Remove line 63 (`GATE_DSR = 0.95`) and add `GATE_DSR` to the `analytics.research_guards`
import. The `target: float = GATE_DSR` default at the old line 192 disappears with the function
body; confirm no other reference remains:

```bash
grep -n "GATE_DSR" tools/era_power_price.py
```

Expected: only the import line, or nothing at all.

- [ ] **Step 4: Prove the report is byte-identical**

```bash
poetry run python tools/era_power_price.py --db analytics.db > /tmp/era_after.txt 2>&1
diff /tmp/era_before.txt /tmp/era_after.txt && echo "BYTE-IDENTICAL"
```

Expected: `BYTE-IDENTICAL`.

**If the diff is non-empty, stop.** Do not adjust the expected values. A diff means the two
implementations were never actually equivalent, which reverses Decision Log #1 in the spec —
report it and await a decision.

- [ ] **Step 5: Gates and commit**

```bash
make lint-py && make typecheck && make test
git add tools/era_power_price.py
git commit -m "refactor(tools): era_power_price delegates the effect-size bar

Also drops its local GATE_DSR = 0.95, a fourth hardcoded copy of a threshold
whose single home is research_guards/gate.py. Report verified byte-identical
before and after. null_is_licensable is untouched — it already calls
audit_guard.powered_null."
```

---

### Task 3: Delegate `multi_regime_power`, and check the unreachable boundary

**Files:**

- Modify: `tools/multi_regime_power.py:58-86` (`required_sr`), `:201` (call site)

**Interfaces:**

- Consumes: `required_sharpe` from Task 1.
- Produces: `required_sr(n_obs, sr0, skew, kurt) -> float | None` keeps its **exact current
  signature and `None`-on-unreachable return**. Its call site at line 201 is unchanged.

**Context — read before writing code.** This is the riskier of the two delegations, because the
two functions disagree about *where* unreachable starts.

`required_sr` searches `lo, hi = sr0, sr0 + 5.0` and returns `None` when `z_of(sr0 + 5) <
Z_GATE`. The promoted `required_sharpe` doubles `hi` up to `1e6` before giving up. The PSR
z-statistic is bounded above by roughly `sqrt(2 * (n_obs - 1))`, so genuinely-unreachable cases
are unreachable in both — **but a case whose solution lies beyond `sr0 + 5` would be `None`
under the old code and finite under the new one.** That is a behaviour change in a research
number, so it gets measured rather than assumed.

- [ ] **Step 1: Capture the before-report**

```bash
poetry run python tools/multi_regime_power.py > /tmp/mrp_before.txt 2>&1
wc -l /tmp/mrp_before.txt
```

- [ ] **Step 2: Write the boundary-agreement check as a temporary script**

Create `/tmp/boundary_check.py` (temporary, not committed):

```python
"""Does the sr0+5 window ever disagree with the 1e6 search? Measured, not assumed."""

from __future__ import annotations

import importlib.util
import math
import sys

from analytics.research_guards import required_sharpe


def load(path: str, name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mrp = load("tools/multi_regime_power.py", "mrp")

disagreements = 0
for n_obs in (2, 3, 5, 10, 25, 50, 100, 200, 1000, 4000, 20000):
    for sr0 in (0.0, 0.2, 0.4026, 0.4979, 0.6520, 1.0, 2.0):
        old = mrp.required_sr(n_obs, sr0, 0.0, 3.0)
        new = required_sharpe(n_obs, benchmark_sr=sr0)
        old_unreachable = old is None
        new_unreachable = math.isinf(new)
        if old_unreachable != new_unreachable:
            disagreements += 1
            print(f"BOUNDARY DISAGREE n_obs={n_obs} sr0={sr0}: old={old} new={new}")
        elif not old_unreachable:
            assert old is not None
            if abs(old - new) > 5e-6:
                disagreements += 1
                print(f"VALUE DISAGREE n_obs={n_obs} sr0={sr0}: {old} vs {new}")
print(f"disagreements: {disagreements}")
```

- [ ] **Step 3: Run it and record the answer**

Run: `poetry run python /tmp/boundary_check.py`

- **If `disagreements: 0`** — delegate straightforwardly in Step 4.
- **If any disagreement is printed** — the old window semantics are load-bearing. Preserve them
  by keeping the guard at the call site rather than changing reported numbers, i.e. Step 4's
  body becomes:

```python
    if z_of(sr0 + 5.0) < Z_GATE:
        return None
    got = required_sharpe(n_obs, benchmark_sr=sr0, skew=skew, kurtosis=kurt)
    return None if math.isinf(got) else got
```

Report which branch you took, with the script's output pasted.

- [ ] **Step 4: Replace the body**

Keep the signature and docstring intent; replace the hand-derived bisection. The `Z_GATE`
constant and the `NormalDist` import stay only if the disagreement branch above needs them —
otherwise remove them and their now-unused imports.

```python
def required_sr(n_obs: int, sr0: float, skew: float, kurt: float) -> float | None:
    """Smallest per-trade Sharpe clearing ``DSR >= GATE_DSR`` at ``n_obs``, given ``sr0``.

    Delegates to :func:`analytics.research_guards.required_sharpe`, which inverts
    the production ``probabilistic_sharpe_ratio`` rather than a hand-derived
    closed form. Returns ``None`` when the gate is unreachable at any Sharpe,
    which is a finding rather than an error: a bar no cell can clear reports
    "everything is suspect" as an artifact.
    """
    if n_obs < 2:
        return None
    got = required_sharpe(n_obs, benchmark_sr=sr0, skew=skew, kurtosis=kurt)
    return None if math.isinf(got) else got
```

Add `import math` if not already present (it is — line 4 area; verify with
`grep -n "^import math" tools/multi_regime_power.py`).

- [ ] **Step 5: Prove the report is byte-identical**

```bash
poetry run python tools/multi_regime_power.py > /tmp/mrp_after.txt 2>&1
diff /tmp/mrp_before.txt /tmp/mrp_after.txt && echo "BYTE-IDENTICAL"
```

Expected: `BYTE-IDENTICAL`. If not, stop and report — same rule as Task 2.

- [ ] **Step 6: Gates and commit**

```bash
make lint-py && make typecheck && make test
git add tools/multi_regime_power.py
git commit -m "refactor(tools): multi_regime_power delegates the effect-size bar

Removes the hand-derived PSR z-formula bisection in favour of inverting the
production function. Unreachable-boundary agreement measured explicitly
because the two searches used different windows. Report verified
byte-identical before and after."
```

---

### Task 4: `tools/distil_power.py` — the CLI the skill must run

**Files:**

- Create: `tools/distil_power.py`
- Test: `tests/test_distil_power.py`

**Interfaces:**

- Consumes: `required_sharpe` (Task 1), `analytics.audit_guard.powered_null`.
- Produces: `price(args) -> list[str]` returning the report lines, and `main(argv) -> int`. The
  skill calls the CLI, never the functions.

**Context.** This exists so the power gate is *run* rather than estimated. `--units` is
mandatory with no default because this repo has two filed defects from numbers that looked
portable and changed meaning with the panel. `--n-series` + `--n-eff` apply the correlation
deflator: effective observations are `n_obs * n_eff / n_series`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_distil_power.py`:

```python
"""Tests for `tools/distil_power.py` — the G3 gate's CLI."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "distil_power.py"
    spec = importlib.util.spec_from_file_location("distil_power", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["distil_power"] = mod
    spec.loader.exec_module(mod)
    return mod


distil_power = _load()


def test_units_is_mandatory(capsys: pytest.CaptureFixture[str]) -> None:
    """An undeclared unit is how a portable-looking number changes meaning."""
    with pytest.raises(SystemExit):
        distil_power.main(
            ["--n-obs", "1000", "--n-trials", "16", "--sr-variance", "0.05"]
        )


def test_benign_family_is_reachable(capsys: pytest.CaptureFixture[str]) -> None:
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--n-obs",
            "20000",
            "--n-trials",
            "1",
            "--sr-variance",
            "0.0",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "REACHABLE" in out
    assert "UNREACHABLE" not in out


def test_hostile_family_is_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    """A tiny sample against a wide trial family cannot clear the gate at any effect size.

    n_obs=2 is the ONLY value that works here, and it was measured rather than
    reasoned: the PSR z-statistic is bounded above by ``sqrt(2*(n_obs-1))``, so
    against ``Z_GATE(0.95) = 1.6449`` n=2 gives z_max 1.4142 (unreachable) but
    n=3 gives 2.0000 and a finite required Sharpe of 4.6463. Do not raise this
    number to make the test "more realistic" — it stops testing anything.
    """
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--n-obs",
            "2",
            "--n-trials",
            "320",
            "--sr-variance",
            "0.05",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "UNREACHABLE" in out


def test_correlation_deflator_raises_the_bar(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pooling correlated series must make the bar HARDER, never easier."""
    base = distil_power.price(
        distil_power.parse_args(
            [
                "--units",
                "per_alert",
                "--n-obs",
                "4000",
                "--n-trials",
                "16",
                "--sr-variance",
                "0.05",
            ]
        )
    )
    deflated = distil_power.price(
        distil_power.parse_args(
            [
                "--units",
                "per_alert",
                "--n-obs",
                "4000",
                "--n-trials",
                "16",
                "--sr-variance",
                "0.05",
                "--n-series",
                "25",
                "--n-eff",
                "2.92",
            ]
        )
    )
    # "effective n" appears in BOTH branches, so asserting on it tests nothing.
    # "deflated by" is printed only when the deflator actually applied.
    assert "deflated by" in "\n".join(deflated)
    assert "no deflator applied" in "\n".join(base)
    assert "\n".join(base) != "\n".join(deflated)


def test_effect_size_reported_when_sd_given(capsys: pytest.CaptureFixture[str]) -> None:
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--n-obs",
            "4000",
            "--n-trials",
            "16",
            "--sr-variance",
            "0.05",
            "--sd",
            "1.2",
            "--corpus-best",
            "1.196",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "required effect" in out
    assert "corpus best" in out


def test_null_containment_uses_the_owning_predicate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--bar must report a containment verdict, never an |delta| < MDE comparison."""
    code = distil_power.main(
        [
            "--units",
            "per_alert",
            "--n-obs",
            "4000",
            "--n-trials",
            "1",
            "--sr-variance",
            "0.0",
            "--sd",
            "1.0",
            "--bar",
            "0.05",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "powered null" in out.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_distil_power.py -q`

Expected: FAIL at import — `tools/distil_power.py` does not exist.

- [ ] **Step 3: Write the implementation**

Create `tools/distil_power.py`:

```python
"""Price a candidate hypothesis BEFORE it is written into the inbox.

The G3 gate of ``/research-distil``. Prints the effect size the gate demands at
the declared ``n`` and trial family, beside the corpus best, so a claim is
*priced* rather than estimated.

Two things this exists to prevent, both with filed precedent in this repo:

* **A power claim the model did the arithmetic for.** ST28's spec and its driver
  disagreed on the detection threshold while both halves stayed internally
  consistent, so no gate, grep or review surface caught it. Running one tracked
  tool is the only structural defence.
* **A number whose units are implied.** ``--units`` is mandatory and has no
  default. The H15 ``bar``-units trap and the 25-symbol 2.92x deflator reused on
  panels whose true deflator is 1.628x or 3.331x are the same defect: a figure
  that looks portable and silently changes meaning with the panel.

The null-containment verdict is delegated to
:func:`analytics.audit_guard.powered_null`. The comparison is never restated here.
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence

from analytics.audit_guard import powered_null
from analytics.research_guards import GATE_DSR, required_sharpe

Z_95 = 1.959963984540054

UNITS = ("per_trade", "per_alert", "per_book_day")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="distil_power",
        description="Price a hypothesis against the gate before designing it.",
    )
    parser.add_argument("--units", required=True, choices=UNITS)
    parser.add_argument("--n-obs", type=int, required=True)
    parser.add_argument("--n-trials", type=int, required=True)
    parser.add_argument("--sr-variance", type=float, required=True)
    parser.add_argument("--n-series", type=int, default=None)
    parser.add_argument("--n-eff", type=float, default=None)
    parser.add_argument("--sd", type=float, default=None)
    parser.add_argument("--bar", type=float, default=None)
    parser.add_argument("--corpus-best", type=float, default=None)
    parser.add_argument("--skew", type=float, default=0.0)
    parser.add_argument("--kurtosis", type=float, default=3.0)
    return parser.parse_args(argv)


def effective_n(n_obs: int, n_series: int | None, n_eff: float | None) -> int:
    """Deflate pooled observations by the correlation deflator.

    Pooling ``k`` correlated series carries the noise reduction of ``n_eff``
    independent ones, so the naive ``n`` overstates the information. Omitting
    the deflator on a pooled multi-symbol panel is a declared error rather than
    a default, which is why both flags must be supplied together.
    """
    if n_series is None and n_eff is None:
        return n_obs
    if n_series is None or n_eff is None:
        raise ValueError("--n-series and --n-eff must be supplied together")
    if n_series < 1 or n_eff <= 0.0:
        raise ValueError("--n-series must be >= 1 and --n-eff must be > 0")
    return max(2, int(n_obs * n_eff / n_series))


def price(args: argparse.Namespace) -> list[str]:
    """Build the report. Returns lines; printing is the caller's job."""
    n_used = effective_n(args.n_obs, args.n_series, args.n_eff)
    sr = required_sharpe(
        n_used,
        n_trials=args.n_trials,
        sr_variance=args.sr_variance,
        skew=args.skew,
        kurtosis=args.kurtosis,
    )
    reachable = not math.isinf(sr)

    out = [
        "distil_power - G3 power gate",
        f"  units             {args.units}",
        f"  n_obs (declared)  {args.n_obs:,}",
    ]
    if n_used != args.n_obs:
        out.append(
            f"  effective n       {n_used:,}"
            f"  (deflated by n_eff {args.n_eff} / {args.n_series} series)"
        )
    else:
        out.append("  effective n       (no deflator applied)")
    out += [
        f"  trial family      {args.n_trials} trials, sr_variance {args.sr_variance}",
        f"  gate target       DSR >= {GATE_DSR}",
        "",
    ]

    if not reachable:
        out.append("  VERDICT           UNREACHABLE at any finite Sharpe")
        out.append(
            "                    Not underpowered - unreachable. More data of this"
        )
        out.append(
            "                    shape cannot fix it; the trial family must shrink."
        )
    else:
        out.append(f"  required Sharpe   {sr:.6f}")
        if args.sd is not None:
            out.append(
                f"  required effect   {sr * args.sd:+.4f} per {args.units.replace('per_', '')}"
                f"  (sd {args.sd})"
            )
        if args.corpus_best is not None:
            out.append(f"  corpus best       {args.corpus_best:+.4f}")
            if args.sd is not None and sr * args.sd > args.corpus_best:
                out.append(
                    "  VERDICT           REACHABLE, but the bar EXCEEDS the corpus best"
                )
            else:
                out.append("  VERDICT           REACHABLE")
        else:
            out.append("  VERDICT           REACHABLE")

    if args.bar is not None and args.sd is not None:
        half = Z_95 * args.sd / math.sqrt(n_used)
        licensed = powered_null(-half, half, bar=args.bar)
        out += [
            "",
            f"  null bar          +/-{args.bar}",
            f"  CI half-width     {half:.4f}  (best case, point estimate exactly 0)",
            f"  powered null      {'LICENSABLE' if licensed else 'NOT LICENSABLE'}"
            f"  (analytics.audit_guard.powered_null)",
        ]
        if not licensed:
            out.append(
                "                    A null here would be INSUFFICIENT, never 'no effect'."
            )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    for line in price(args):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests**

Run: `poetry run pytest tests/test_distil_power.py -q`

Expected: PASS, all seven cases.

- [ ] **Step 5: Smoke-run the two shapes the skill will actually use**

```bash
poetry run python tools/distil_power.py --units per_alert --n-obs 4609 --n-trials 1 \
  --sr-variance 0.0 --sd 1.0 --corpus-best 1.196
poetry run python tools/distil_power.py --units per_trade --n-obs 4000 --n-trials 320 \
  --sr-variance 0.05 --sd 1.2 --corpus-best 1.196
```

Expected: the first REACHABLE, the second showing the bar exceeding the corpus best. Paste both
outputs into the commit report — this is the tool's own evidence that it bites.

- [ ] **Step 6: Gates and commit**

```bash
make lint-py && make typecheck && make test
git add tools/distil_power.py tests/test_distil_power.py
git commit -m "feat(tools): distil_power - the G3 gate runs, it does not estimate

Mandatory --units, optional correlation deflator, and a null-containment
verdict delegated to audit_guard.powered_null. UNREACHABLE is a first-class
result: not underpowered, unreachable."
```

---

### Task 5: The skill

**Files:**

- Create: `.claude/skills/research-distil/SKILL.md`
- Modify: `CLAUDE.md` (cadence line only)

**Interfaces:**

- Consumes: `tools/distil_power.py` (Task 4).
- Produces: the `/research-distil` slash command.

**Context.** Match `/ingest-x` — a single `SKILL.md`, an inline self-contained rubric that a
subagent can be handed without re-reading memory or the SoT, and **one** human review gate for
the whole batch. `.claude/skills/*/SKILL.md` is already covered by both `.gitignore` re-includes
and the markdownlint globs; verified, no config change needed.

Do **not** add the skill to `CLAUDE.md`'s skill list — that list is deliberately not duplicated
there because the harness injects it. Only the cadence bullet gets a line.

- [ ] **Step 1: Write `SKILL.md`**

Frontmatter (`description` must be one line and trigger-rich, matching the house style):

```markdown
---
name: research-distil
description: >
  Distil books, GitHub repos and papers into AT MOST THREE power-priced,
  pre-registered hypotheses per run, routed into the existing thesis-inbox /
  mechanics-backlog intake behind one human review gate. Four gates reject on
  novelty (filed no-edge verdicts), new-information (a re-slice of held
  price/order-flow is 7-for-7 dead), power (must RUN tools/distil_power.py) and
  cost (net of 2(fee+slip)*entry/risk). "Unreachable, do not build" is a
  successful output. Invoke when the user says "/research-distil", points at a
  book-to-skill slug, asks "what should we test from this book/repo/paper", or
  wants research turned into a testable hypothesis.
---
```

Body sections, in order:

1. **`## Why this throttles`** — quote `thesis-inbox.md`'s own "the bottleneck is testing
   capacity, not idea capture", and the measured "1 → 320 trials moves the bar +0.049R →
   +1.035R against a corpus best of +1.196R". State that emitting forty hypotheses makes every
   cell unreachable.
2. **`## Flow`** — invoke → one `sonnet` subagent per source extracting claims only → main
   thread runs G1–G4 → one consolidated review digest → route on approval.
3. **`## Inline extraction rubric`** — self-contained, pasted into the subagent prompt. Returns
   `{claim, mechanism, data_required, author_effect_size, citation}`. No verdicts, no gates.
   Tolerate malformed JSON at the consuming end.
4. **`## The four gates`** — G1 with the closed-verdict list written out inline (meta-labelling
   and ensemble/confluence DSR 0.7030 · exit tuning as a P&L lever · weekend/DOW DSR 0.672 ·
   HTF agreement · reference-level proximity · conditioning axes 6-for-6-plus-one-amended ·
   XS reversal · EWMAC regime attribution · spot-perp CVD · `sl_pct` sweeping), G2, G3 with the
   literal command, G4 with the drag formula.
5. **`## Refusals`** — no row without pasted G3 output; hard cap of three with every dropped
   item logged and its reason; no re-opening a G1 match without a written operator override; no
   parameters, `tp_r`, thresholds or code.
6. **`## Routing`** — alpha to `docs/plans/thesis-inbox.md`, execution/cost to
   `docs/plans/mechanics-backlog.md`, each row carrying all four gate results, the pasted power
   output and a Decision Log naming the reversing observable. Spec doc only on promotion.
7. **`## Guardrails`** — costs are modelled not realised, so every figure is an optimistic bound
   whose error runs one way; `sr_variance` unknown ⇒ INSUFFICIENT, never a pass; abort if
   `distil_power.py` errors rather than degrading to an estimate.

- [ ] **Step 2: Add the cadence line to `CLAUDE.md`**

In the Agent Skills section's cadence bullet, extend the existing list:

```markdown
  `/research-distil` after any book/repo/paper ingest — and never more than 3 survivors a run.
```

- [ ] **Step 3: Lint**

Run: `make lint-md`

Expected: `0 issues`.

- [ ] **Step 4: Live acceptance run**

Run `/research-distil` against the one source available without a book PDF: the repo
`robcarver17/pysystemtrade`.

**The run must produce at least one G1 rejection or one `UNREACHABLE`.** A first run that emits
three glowing survivors is evidence the gates are not biting — report that as a failure, not a
success, and do not commit until it is diagnosed.

- [ ] **Step 5: Commit**

```bash
make lint-md
git add .claude/skills/research-distil/SKILL.md CLAUDE.md
git commit -m "feat(skills): /research-distil - four gates, hard cap of three

Books and repos become a fourth source into the existing thesis-inbox /
mechanics-backlog intake rather than a new pipeline. The job is to throttle,
not amplify: the inbox's own header says the bottleneck is testing capacity,
and trial count dominates n."
```

---

## After all tasks

- [ ] Invoke `/post-branch` — Steps 1–5b and 7 **before** `gh pr create`, Steps 6 and 10a/10c
  after. This is what keeps it one CI run instead of two.
- [ ] One PR for the whole plan, including the four documentation commits already on the branch.
- [ ] Ping `buibui-wifey-wall-street-bot-3a` on merge with the confirmed port shape (**per-repo,
  copy verbatim, then swap only the G1 list and the G4 drag formula**), per the standing
  agreement.

## Self-review notes

Checked against the spec on 2026-08-14:

- **Spec coverage.** §3.1 → Task 1; §3.2 → Task 4; §3.3 → Task 5; §4 gates → Task 5 Step 1;
  §5 flow and routing → Task 5 Step 1; §6 refusals → Task 5 Step 1; §7 testing → Tasks 1–4 plus
  Task 5 Step 4; §8 failure modes → Task 5 Step 1 §7 and Task 4's `effective_n` guard; §9 files
  → all tasks.
- **One deliberate divergence from the spec, resolved here.** §7 asks for a test that
  `null_is_licensable` rejects a case `|Δ| < MDE` would have licensed. That function is **not
  this plan's code** — it lives in `tools/era_power_price.py` and delegates to
  `audit_guard.powered_null`, which `tests/test_audit_guard.py` already covers. Re-testing it
  here would duplicate coverage of code we are not touching, so it is dropped; Task 4's
  `test_null_containment_uses_the_owning_predicate` covers the part this plan actually adds.
- **Type consistency.** `required_sharpe` returns `float` (`math.inf` when unreachable) in
  Tasks 1, 2, 3 and 4. `required_sr` returns `float | None` and maps `inf → None` at exactly one
  place, Task 3 Step 4. `price()` returns `list[str]` and `main()` returns `int` in both Task 4's
  test and implementation.
