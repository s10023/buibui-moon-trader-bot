# v2 Phase 2 Step 2 — Unified `SignalCandidate` model

> **Status: DEFERRED (2026-05-14).** Shelved in favour of execution-side work
> (T4 positions-write merge, T5 position sizing) and a possible backtest-live-parity
> pass (T6 — port F8 / regime / direction-filter gates into the backtest engine so
> backtest numbers reflect the live gate stack). Re-open this plan only if:
> (a) ≥2 weeks of `signal_alert_outcomes` data shows the soft gates need
> per-cell routing that the strategy × tf matrix can't express, OR
> (b) T6 lands and the routing axes (direction × regime × session × volume × htf_bias)
> remain measurably load-bearing afterwards.
> The Bayesian shrinkage math (§5) and the 6-axis decomposition (§3–§4) are still
> the right shape for that future refactor; the 4–6 week timeline is the part that
> made this not worth doing now. See MEMORY.md Current State for the 2026-05-14
> session that produced this verdict.

---

**Branch:** `feat/v2-phase2-step2-signal-candidate` (not started)
**Status:** PLAN — pending user sign-off before code lands.
**Supersedes:** Phase 2 step 2 of `docs/redesign/buibui-redesign.md` §10 (the 1–2 week
estimate there is wrong; see Timeline below).
**Predecessors:** Phase 2 step 1 shipped (regime gate, PR #350 / commit `da69537`).
T2 outcome tracking shipped (PR #368).

---

## 1. Goal

Collapse the 19 parallel `detect_*` functions and the four ad-hoc setup buckets
into one routing surface — a `SignalCandidate` record whose
`(direction, regime, session, volume, htf_bias)` axes carry the per-cell edge
already shown to exist by the 2026-05-13 `bos` routing audit. Make those axes
the load-bearing thing the live gate stack and the soft→hard flip protocol
both consult, instead of the current strategy-name × timeframe matrix that
flattens away direction and routes on a v1 type→regime mapping the live data
has now contradicted in two places (`fib_golden_zone` flipped 2026-05-11;
`bos` long-side suppressed 2026-05-13).

The model is not a redesign of detectors. Detectors stay. What changes is the
output shape and the router.

---

## 2. Why now (T2 was the unlock)

Until PR #368 the system had no live `outcome_r` ledger for fired signals.
Any router we shipped would have had to either trust full-sample backtest
(systematically flatters survivorship) or no-look replays of the existing
trade table (the `regime_gate_replay.py` / `direction_filter_replay.py`
pattern — useful as a counterfactual, useless as continuous monitoring).

PR #368 changed both ends:

- Live fires now persist `tp_price` + `rr_ratio` at the moment of the alert
  (P1, commit `a21681a`). Per-cycle backfill walks OHLCV forward and writes
  `outcome ∈ {win, loss, expired}` + `outcome_r` (P2, commit `71df56c`).
- `tools/live_outcomes_report.py` (commit `a2576fb`) gives a roll-up by
  `(strategy, tf, direction)` — the exact axes T3 needs.

That gives Bayesian shrinkage a concrete live prior to weight against, and
gives the soft→hard flip protocol a non-replay form of evidence. Without
either, T3 would have shipped as a refactor wearing routing colours. With
both, the router can be empirical.

---

## 3. Unified `SignalCandidate` schema

`SignalEvent` (in `analytics/signal/types.py`) already carries most of the
inputs. What's missing is the explicit axis surface — `direction`, `regime`,
and `session` exist as bias-chain side-effects but aren't first-class fields
on the candidate, and the router can't read them without re-deriving.

The new dataclass:

```python
@dataclass(frozen=True, slots=True)
class SignalCandidate:
    # identity
    symbol: str
    timeframe: str
    candle_ts_ms: int
    strategy: str             # legacy name kept for traceability
    setup_family: SetupFamily # 'liquidity' | 'continuation' | 'orderblock' |
                              # 'candlestick' | 'session' | 'flow'

    # axes (router reads these — first-class, not derived)
    direction: Literal['long', 'short']
    regime: Literal['trend', 'range', 'high_vol', 'unknown']
    session: Literal['asia', 'london', 'ny_am', 'ny_pm', 'overnight']
    volume_state: Literal['spike', 'normal', 'low']
    htf_bias: Literal['with', 'against', 'flat']  # F8 slope vs direction

    # geometry
    entry: float
    sl_price: float
    tp_price: float
    rr_ratio: float
    structural_levels: list[StructuralLevel]  # FVG/OB/EQH/EQL inside ±2×ATR

    # context (computed once at fire time)
    atr14: float
    expected_r_prior: ExpectedR | None        # see §5
```

`SetupFamily` is six-valued and bridges the v1 `STRATEGY_TYPE_GROUPS` taxonomy
to the four `setup_type`s in `docs/redesign/buibui-redesign.md` §4 without
forcing a rename of the detector inventory. The mapping is published in the
TOML, not hard-coded — so a future cell-by-cell rerouting (e.g. moving
`liquidity_sweep` between families) is a config edit, not a deploy.

What the router consults: `(setup_family, direction, regime, session, volume_state, htf_bias)`.
Six axes; ~6 × 2 × 3 × 4 × 3 × 3 ≈ 1300 cells in principle, but
the realistic populated surface is the same ~150 we already audit. Cell-thinness
is solved by Bayesian shrinkage (§5), not by collapsing axes.

---

## 4. Schema migration — enrich `backtest_trades` in place

`backtest_trades` already has 700K+ historical rows with `entry_time`,
`strategy`, `tf`, `symbol`, `direction`, `outcome_r`. It lacks the rest of
the candidate axes. Two options:

| Option | Approach | Risk | Verdict |
| --- | --- | --- | --- |
| **A. Forward-only** | New rows from PR-merge onwards carry the full schema; old rows stay short. Router uses an `axes_v2` flag to gate. | Audits and replays bifurcate: half the corpus has the axes, half doesn't. The 2026-05-13 routing audit on `bos` would be impossible to refresh. | **Reject.** |
| **B. Replay enrichment** | Single migration script joins `backtest_trades` to OHLCV at `entry_time`, computes `(regime, session, volume_state, htf_bias)` per row, writes to a new `backtest_trades_axes` table keyed by `trade_id`. Idempotent; replayable. | Migration takes ~hours for 700K rows; pure read-write loop, no router behaviour change until the new gate is wired. | **Adopt.** |

Migration script lives at `tools/enrich_backtest_trades_axes.py`. Pure
function over `(conn, since_ts)`; deterministic; safe to re-run. Wired to
`make db-update` as a pre-step after `regression-update` (so goldens stay
clean — the new table is additive, not a column add on the existing).

Detector order for the first cut: enrich for the strategies that have the
most evidence pressure already — `bos`, `liquidity_sweep`, `ema`,
`fib_golden_zone`, `order_block`. The remaining 14 detectors enrich in the
same migration but their cells stay below the live router's min-n threshold
until they accumulate.

---

## 5. Bayesian shrinkage protocol

The point of T2 was to make live `outcome_r` a first-class input. The point
of T3's router is to combine that input with backtest evidence in a way that
doesn't let a 4-row live "win streak" override a 5K-row backtest pessimism,
and doesn't let a 50K-row backtest "edge" override 200 live trades' worth of
contradiction.

For each router cell `c = (setup_family, direction, regime, session, volume_state, htf_bias)`:

```text
expected_r(c) = (n_bt * mu_bt + n_live * mu_live + k * mu_global) / (n_bt + n_live + k)
```

- `mu_bt` — full-sample backtest avg_r for cell `c` from `backtest_trades_axes`.
- `mu_live` — live avg_r for cell `c` from `signal_alert_outcomes` joined with
  the same axes (computed at backfill time, see §6).
- `mu_global` — strategy-level prior (the per-strategy avg_r across all cells).
- `k` — shrinkage strength. Default `k = 30`. This is the only knob the TOML
  exposes; everything else is data.

The router fires a cell iff `expected_r(c) >= min_expected_r` (initial
`min_expected_r = 0.05`, matching the F9 live edge bar).

Edge cases the formula handles by construction:

- New cell with no live + no backtest evidence → falls to `mu_global` → safe
  default behaviour (today's behaviour).
- Cell with thousands of backtest trades but no live yet → `mu_bt` dominates;
  router behaves like the current backtest gate.
- Cell with thousands of live trades → `mu_live` dominates; router self-corrects
  as the live edge decays or improves.

**Naive rolling mean is explicitly out.** A 14-day rolling avg_r on
`signal_alert_outcomes` would, for a 1d strategy with 4 fires per cell over
14 days, be statistical noise. Shrinkage with `k = 30` against the global
prior is the protection.

---

## 6. `signal_alert_outcomes` axis enrichment

P2 of T2 wrote `outcome_r` but didn't enrich the axes (`regime`, `session`,
`volume_state`, `htf_bias`). T3 adds an enrichment step inside
`backfill_outcomes` — the row already needs OHLCV to resolve the outcome,
and the OHLCV slice carries the same context the live gate computed. Wire
the cache so we read 4h once per cycle per symbol.

Schema change: four new columns on `signal_alert_outcomes` (nullable,
backfilled by the same worker on next cycle for old rows that survived P1).

This is the only DB schema change in T3. The model lives mostly in
in-memory dataclasses and a new view (`v_router_cells`) that joins
`backtest_trades_axes` + `signal_alert_outcomes` and pre-aggregates the
six-axis cell rollup.

---

## 7. Soft→hard flip protocol

The protocol is what made PRs #366 and #367 land in soft mode by default and
what unblocks the eventual hard flip without re-litigation. T3 formalises it.

**A cell `c` may be promoted from `soft` to `hard` iff:**

1. **Live coverage**: ≥ 30 resolved (non-NULL outcome) rows in
   `signal_alert_outcomes` for cell `c` over the trailing 60 days.
2. **Replay-validated**: the corresponding replay tool
   (`regime_gate_replay.py`, `direction_filter_replay.py`, or the new
   `router_gate_replay.py` from §8) shows `suppressed_avg_r ≤ 0` AND
   `kept_avg_r ≥ suppressed_avg_r + 0.05` for cell `c`.
3. **Live consistent**: the live `mu_live` for the would-be-kept slice of
   `c` is ≥ 0 (no live contradiction of the replay verdict).
4. **Backtest consistent**: `mu_bt(c)` agrees in sign with `mu_live(c)`.
   Disagreement (e.g. backtest +0.20R, live −0.15R) blocks promotion and
   triggers a re-audit.

T2c (PR #367, `bos` long-side suppress) is the canonical example. It hit (2),
hit (4), and the soft mode was justified specifically *because* (1) and (3)
were not yet measurable. T3's evidence pipe makes (1) and (3) routine.

Promotion is a single-line TOML edit (`mode = "soft"` → `mode = "hard"`).
No code path changes. Same shape as the existing two soft gates.

---

## 8. Replay tooling (extends, does not replace)

`router_gate_replay.py` joins `backtest_trades_axes` to the proposed router
config and reports the would-suppressed / would-kept split per cell.
Mirrors `tools/regime_gate_replay.py` shape exactly. Reusable for every
future router-config edit — same way the existing two replay tools are
reused per PR.

Existing replay tools stay valid for their respective scopes:

- `regime_gate_replay.py` — still the canonical tool for the §6 mapping itself.
- `direction_filter_replay.py` — still the canonical tool for the per-strategy
  direction-suppress flags.
- `tools/strategy_edge_audit.py` (Phase 0) — full-sample audit; T3 doesn't
  retire it because the WFO-vs-full-sample split is still useful pre-flip.

---

## 9. Timeline — 4 to 6 weeks, not 15 days

The redesign's "1–2 weeks" estimate dates from before T2 existed and treated
this step as "rewire the detectors to feed one struct". The actual surface:

| Sub-step | Estimate | Notes |
| --- | ---: | --- |
| 9a. `SignalCandidate` dataclass + emitter on every detector | 4 days | 19 detectors; each is a one-line change at the return site, but volume_state / htf_bias / session need a shared helper to avoid drift. |
| 9b. `backtest_trades_axes` enrichment migration | 3 days | Migration script + idempotency test + golden refresh. Slow but mechanical. |
| 9c. `signal_alert_outcomes` axis columns + backfill enrichment | 2 days | Schema add; reader updates. |
| 9d. Router cell rollup view (`v_router_cells`) + Bayesian formula | 3 days | Pure SQL + a Python wrapper; testable in isolation. |
| 9e. Live router gate (soft mode) + TOML surface | 4 days | New gate slot in the bias chain (Step 0.5, between F8 and combo). Soft mode default. |
| 9f. `router_gate_replay.py` | 1 day | Mirror of existing two replay tools. |
| 9g. Validation: replay vs live over ≥ 2 weeks | 2 weeks | Wall-clock, not work. |
| 9h. First hard flip of a single cell (the bos-long suppress is the candidate) | 1 day | One-line TOML; demonstrates the protocol end-to-end. |

Total active engineering: ~17 working days. Total elapsed including the
mandatory soft-mode wait: 4–6 weeks. Bake the wait in; don't pretend the
flip evidence can be conjured.

Sequencing note: 9a/9b/9c/9d are parallelisable. 9e blocks on all four.
9f blocks on 9b. 9g blocks on 9e + 9f. 9h blocks on 9g.

---

## 10. What this branch does NOT do

- **No detector deletions.** The redesign §3 "aggressive cuts" list was
  already contradicted by Phase 0 audit (0 KILL, 0 DEMOTE, 19 KEEP).
  Cell-level routing replaces the detector-level cut idea.
- **No `setup_type` collapse to four.** Six families is the bridging surface;
  the four `setup_type`s from §4 are derivable but not load-bearing.
- **No new strategies.** `session_sweep` and the rest of the §4 setup list
  are Phase 2 step 3 (separate branch).
- **No risk engine.** Phase 4. Position sizing, concurrency caps, daily
  circuit-breaker stay where they are.
- **No alert format change.** Phase 4 also; the §8 decision-sheet layout
  is independent.

---

## 11. Open question — is `bos` salvageable end-to-end?

The 2026-05-13 routing audit found `bos` has 131 net-positive cells out of
644 at n≥30, with the positive slice clustering on the short side under
range/high_vol regimes. PR #367 captured the long-side suppression as a
one-line TOML override. T3 captures the regime-mapping correction as a
TOML edit too.

The unanswered question: **after the routing fixes, is `bos`'s remaining
positive surface stable enough to keep without a detector rework?**

Two cases:

1. The router (with shrinkage + regime + direction + session axes) lifts
   `bos` aggregate from −0.0555R to a small positive. No detector change
   needed. The "20.3% net-positive cells" carry the load.
2. The router lifts the suppressed-cell avg_r but the kept-cell avg_r stays
   marginal. Then the BOS swing-detection geometry itself is the issue —
   the swing-pivot rules in `analytics/strategies/market_structure.py`
   miscategorise structure breaks in trend regimes — and a rework is needed
   on top of the router.

T3 produces the data to decide between (1) and (2). The decision itself is
out of scope.

---

## 12. Validation criteria (for the eventual branch)

- **Behaviour-neutral at merge.** Router ships in soft mode; alert volume
  and content unchanged.
- **Cycle wall-clock**: ≤ +30ms vs pre-branch. The new gate is one dict
  lookup per candidate; the OHLCV axis enrichment piggybacks on the
  outcome-backfill OHLCV read.
- **No regression in goldens.** Router is live-only (no DB writes from the
  gate itself; only axis enrichment, which is additive).
- **3/3 main configs** inherit the new `[bias.router]` block cleanly.
- **Replay parity.** `router_gate_replay.py` run on the merged config must
  reproduce the same kept/suppressed split as the live soft-mode logs over
  a 7-day window — within ±3%.
- **Migration idempotency.** Re-running `tools/enrich_backtest_trades_axes.py`
  produces zero diffs in `backtest_trades_axes`.

---

## 13. Sign-off checklist before branching

Hand this section to the user and wait for explicit answers before writing
code:

- [ ] Is the six-family bridge (vs collapsing to four `setup_type`s now)
      acceptable, or do you want the four-bucket collapse in the same branch?
- [ ] Is `k = 30` shrinkage strength acceptable as the default, or do you
      want to sweep it against historical data before fixing?
- [ ] Is `min_expected_r = 0.05` the right router threshold, or should it
      track F9's per-cell bar (variable)?
- [ ] Is the 4–6 week timeline acceptable, or do you want the migration
      (9b) split out as a precursor PR so step 9a–9d can land sooner?
- [ ] Open question §11 — do you want the audit-after-router data to make
      the bos rework decision, or do you want to pre-commit to a rework now?

---

## 14. References

- Predecessor: `docs/redesign/buibui-redesign-phase2-plan.md` (regime gate
  soft-mode plan; shipped as PR #350).
- Findings that justify T3 over alternatives:
  `docs/redesign/buibui-redesign-phase2-replay-findings.md`.
- Phase 0 audit (data behind §10's no-cut conclusion):
  `docs/redesign/buibui-redesign-phase0.md`.
- Routing audit that proved the axes carry edge:
  `~/.claude-personal/.../memory/project_bos_routing_audit.md`.
- Outcome ledger contract: `analytics/signal/outcome_backfill.py`.
- System mental model (read first):
  `docs/system-overview.md` §2 (gate chain) and §7 (tooling).
