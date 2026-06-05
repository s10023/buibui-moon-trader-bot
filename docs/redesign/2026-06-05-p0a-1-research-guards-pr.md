# PR #1 task breakdown — `analytics/research_guards/` (P0a-1)

**Date:** 2026-06-05 · **Status:** implementation plan (no code yet) · **Parent:** `docs/redesign/2026-06-05-p0-research-guardrails-spec.md` (P0a-1) · **Branch:** `feat/research-guards-stats` · **Commit:** `feat(research): add overfitting-control stats (DSR/PBO/haircut/MinTRL/bootstrap)`.

## Scope — exactly this, nothing else

Ship a **pure, dependency-free statistics package** + tests. This PR is **math only**: no DB, no CLI, no wiring into sweeps/audits/recalibrate/P1, no cost changes. Those are later PRs (P0a-2 wiring, P0b costs). Keeping it pure makes it trivially reviewable and **regression-golden-stable** (it touches nothing existing).

**Why first:** it's the lowest-blast-radius piece of the whole roadmap — no behavioral drift, no live risk, fast deterministic tests — and every later phase (sweep gates, audit verdicts, P1 metrics) depends on it.

### Zero new dependencies (verified)

- `numpy` — already imported directly across the codebase (`engine.py` uses `np`).
- **Φ / Φ⁻¹ via `statistics.NormalDist`** (stdlib 3.11+): `NormalDist(0,1).cdf(x)` / `.inv_cdf(p)`. **Do not add scipy.**

## Files

```text
analytics/research_guards/
  __init__.py     eager re-exports (mirror analytics.strategies.__init__ pattern)
  psr.py          probabilistic_sharpe_ratio
  dsr.py          expected_max_sharpe, deflated_sharpe_ratio
  pbo.py          PBOResult, cscv_pbo
  haircut.py      HaircutResult, haircut_sharpe
  mintrl.py       min_track_record_length
  bootstrap.py    BootstrapCI, block_bootstrap_ci
tests/
  test_psr.py test_dsr.py test_pbo.py test_haircut.py test_mintrl.py test_bootstrap.py
```

## Per-module spec

### 1. `psr.py` — Probabilistic Sharpe Ratio (Bailey & LdP 2012)

```python
def probabilistic_sharpe_ratio(
    sr: float, n_obs: int, skew: float = 0.0,
    kurtosis: float = 3.0, sr_benchmark: float = 0.0,
) -> float: ...
```

- `PSR = Φ( (sr − sr_benchmark)·√(n_obs−1) / √(1 − skew·sr + ((kurtosis−1)/4)·sr²) )`.
- `kurtosis` is **non-excess** (normal = 3); document loudly (scipy default is excess → caller adds 3).
- Guards: `n_obs ≥ 2` else `ValueError`; denominator must be `> 0` else `ValueError` (degenerate moments). Result ∈ [0, 1].
- **Test anchors:** sr=0 → 0.5; sr=0.5, T=24, skew=0, kurt=3, bench=0 → **≈ 0.9881** (hand-computed); negative skew ↓ PSR; higher kurtosis ↓ PSR; larger T ↑ PSR (sr>bench). Monotonicity property tests.

### 2. `dsr.py` — Deflated Sharpe Ratio (Bailey & LdP 2014)

```python
EULER_MASCHERONI = 0.5772156649015329

def expected_max_sharpe(n_trials: int, sr_variance: float) -> float: ...
def deflated_sharpe_ratio(
    sr: float, n_obs: int, *,
    trial_srs: Sequence[float] | None = None,        # path A: derive N + V
    n_trials: int | None = None, sr_variance: float | None = None,  # path B: explicit
    skew: float = 0.0, kurtosis: float = 3.0,
) -> float: ...
```

- `SR₀ = √V · [ (1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) ]`, γ = Euler-Mascheroni.
- `deflated_sharpe_ratio = probabilistic_sharpe_ratio(sr, n_obs, skew, kurtosis, sr_benchmark=SR₀)`.
- Validate **exactly one** of {trial_srs} / {n_trials+sr_variance} provided. From `trial_srs`: `N = len`, `V = var(ddof=1)`.
- Guards: `N < 2 → SR₀ = 0.0` (no deflation); `sr_variance ≤ 0 → SR₀ = 0.0`.
- **Test anchors:** `DSR(N=1) == PSR(bench=0)`; `DSR < PSR` for N>1; `expected_max_sharpe` monotonic ↑ in N and V; DSR ↓ as N ↑.

### 3. `pbo.py` — Probability of Backtest Overfitting / CSCV (LdP 2015)

```python
@dataclass(frozen=True)
class PBOResult:
    pbo: float; logits: list[float]
    degradation_slope: float; n_combinations: int

def cscv_pbo(
    perf_matrix: npt.NDArray[np.float64],   # (T_periods, N_trials) per-period returns
    n_splits: int = 14,
    metric: Callable[[npt.NDArray[np.float64]], float] | None = None,  # default: Sharpe of a column
) -> PBOResult: ...
```

- Partition T rows into S=`n_splits` equal blocks (drop remainder); over all `C(S, S/2)` train/test combos: IS-best trial `n*` = argmax(metric on train cols); OOS relative rank `ω` of `n*` among all trials on test cols; `λ = ln(ω/(1−ω))`. **PBO = mean(λ ≤ 0)**.
- `degradation_slope` = OLS slope of (OOS metric of n*) on (IS metric of n*) across combos.
- Guards: `n_splits` even and ≥ 4 else `ValueError`; `N_trials ≥ 2` else `ValueError`; clamp `ω ∈ [1/(N+1), N/(N+1)]`; average ranks on ties. Note compute: S=14 → C(14,7)=3432 combos (document the cost; S is tunable).
- **Test anchors (the calibration that matters):** seeded **pure-noise** matrix → **PBO ≈ 0.5**; one column with a genuine constant mean-edge → **PBO ≈ 0**; an IS↔OOS rank-inverted construction → **PBO ≈ 1**; odd `n_splits` → `ValueError`. Deterministic (combinations are ordered; only the test's matrix uses seeded RNG).

### 4. `haircut.py` — multiple-testing Sharpe haircut (Harvey & Liu 2014, classic core)

```python
@dataclass(frozen=True)
class HaircutResult:
    adjusted_pvalue: float; haircut_sharpe: float
    haircut_pct: float; method: str; fell_back: bool

def haircut_sharpe(
    sr: float, n_obs: int, n_tests: int,
    method: Literal["bonferroni", "holm", "bhy"] = "holm",
    pvalues_all: Sequence[float] | None = None,   # required for holm/bhy ordering
) -> HaircutResult: ...
```

- `t = sr·√n_obs`; `p = 2·(1 − Φ(|t|))` (two-sided). Adjust:
  - **Bonferroni:** `p_adj = min(1, p·n_tests)`.
  - **Holm / BHY:** step-down/FDR over `pvalues_all` (need the full set); if `pvalues_all is None` → **fall back to Bonferroni, set `fell_back=True`**.
- Back out haircut: `t_adj = Φ⁻¹(1 − p_adj/2)`; `haircut_sharpe = max(0, t_adj)/√n_obs`; `haircut_pct = 1 − haircut_sharpe/sr` (0 if sr≤0).
- Guards: `n_tests ≥ 1`; `n_tests == 1 → p_adj = p` (no haircut); `sr ≤ 0 → haircut_sharpe=0, p_adj=1`.
- **Test anchors:** `n_tests=1 → haircut_sharpe == sr`; Bonferroni `p_adj == min(1, p·n_tests)`; Holm adjusted-p ≤ Bonferroni adjusted-p; missing `pvalues_all` → `fell_back True`.
- *Scope note:* v1 = the 3 classic adjustments on p-values. The full Harvey-Liu empirical-t-distribution procedure is a later refinement.

### 5. `mintrl.py` — Minimum Track Record Length (LdP)

```python
def min_track_record_length(
    sr: float, skew: float = 0.0, kurtosis: float = 3.0,
    target_sr: float = 0.0, confidence: float = 0.95,
) -> float: ...
```

- `MinTRL = 1 + (1 − skew·sr + ((kurt−1)/4)·sr²)·(Φ⁻¹(confidence)/(sr − target_sr))²`.
- `sr ≤ target_sr → float("inf")`. Returns fractional obs (caller ceils).
- **Test anchors:** higher sr → lower MinTRL; higher confidence → higher MinTRL; `sr==target → inf`; **round-trip invariant:** at `n_obs = ceil(MinTRL)`, `PSR(sr, n_obs, …, target_sr) ≳ confidence` (cross-checks against `psr.py`).

### 6. `bootstrap.py` — block/stationary bootstrap CI (Politis-Romano)

```python
@dataclass(frozen=True)
class BootstrapCI:
    point: float; lo: float; hi: float; alpha: float; n_valid: int

def block_bootstrap_ci(
    returns: npt.NDArray[np.float64],
    stat_fn: Callable[[npt.NDArray[np.float64]], float],
    n_boot: int = 10_000, block: int | None = None,
    alpha: float = 0.05,
    method: Literal["stationary", "circular"] = "stationary",
    seed: int | None = None,
) -> BootstrapCI: ...
```

- Resample wrap-around blocks (stationary = geometric length mean `block`; circular = fixed `block`) until length T; `stat_fn` per resample; CI = percentiles `[alpha/2, 1−alpha/2]`. `point = stat_fn(returns)`.
- `block is None → max(1, round(len**(1/3)))`. Drop NaN resample stats from the pool (track `n_valid`).
- Guards: `len(returns) ≥ 2` else `ValueError`; `block` clamped to `< len`; `seed` → reproducible.
- **Test anchors:** iid-normal, `stat=mean` → CI brackets true mean ≈ (1−alpha) of repeats; CI width shrinks ~1/√T; **seeded determinism**; AR(1)-correlated series → stationary CI **wider** than naive iid bootstrap (the whole point).

## `__init__.py`

Eager re-export every public function + result dataclass (mirror `analytics/strategies/__init__.py`). One import site: `from analytics.research_guards import deflated_sharpe_ratio, cscv_pbo, ...`.

## Validation fixtures (the acceptance backbone)

Bake published/hand-computed anchors into tests as ground truth — the PSR ≈0.9881 case, the noise-matrix PBO≈0.5 / edge-matrix PBO≈0 / inverted PBO≈1 cases, and the MinTRL↔PSR round-trip. These are the "is the math right" gate; the *system-level* acceptance (guard re-flags the known OOS-decay cells) belongs to **P0a-3**, after wiring.

## Definition of done

- [ ] 6 modules + `__init__.py`, full type annotations (`npt.NDArray[np.float64]`, `Callable`, `Sequence`).
- [ ] 6 test files; property + worked-example anchors above; **no network, no DB** (pure math → fast).
- [ ] `make lint-py` clean (ruff format + lint).
- [ ] `make typecheck` clean (mypy strict).
- [ ] `make test` green; **`make test-regression` 3/3 goldens UNMOVED** (additive, touches nothing).
- [ ] One-line entry in `CLAUDE.md` Project-Structure for the new package (structure change; `/post-branch` will confirm). No behavioral docs yet (deferred to P0a-2 when it's wired/used).
- [ ] `/pr-summary` → title + summary + test plan.

## Task order (TDD)

1. `psr.py` + test (foundation — DSR/MinTRL depend on it).
2. `dsr.py` + test (uses psr).
3. `mintrl.py` + test (round-trips against psr).
4. `haircut.py` + test (standalone).
5. `bootstrap.py` + test (standalone).
6. `pbo.py` + test (heaviest; standalone).
7. `__init__.py` re-exports; full `make lint-py && make typecheck && make test`; CLAUDE.md line; `/pr-summary`.

## Explicitly NOT in this PR (next PRs)

- **P0a-2:** wire DSR/PBO into the sweep commit-gate (`/config-refresh`, `/wfo-sweep`, `/param-sweep-apply`), bootstrap-CI into the audit tools, DSR-annotation into recalibrate, CIs into P1 metrics.
- **P0a-3:** validation run — confirm the guard re-flags the known OOS-decay cells.
- **P0b:** funding accrual + slippage in `engine.py` + `_scan_forward` (behavioral; goldens move).
