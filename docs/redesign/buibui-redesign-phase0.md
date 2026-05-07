# Phase 0 — Strategy Edge Audit (data-driven, runs before any cuts)

**Premise**: §3 of `buibui-redesign.md` deletes 11 detectors on category, not data. Phase 0 produces the ranked evidence so cuts are justified — or shown unnecessary.

**Design rule**: a detector is only deletable if avg_r ≤ 0 across **all** of: standalone TFs, regimes, combo partners, sessions. Anything with positive edge in ≥1 slice gets *demoted* (higher score threshold / confluence-only), not deleted.

---

## 1. Output (the artifact this phase produces)

A single ranked CSV: `/tmp/strategy_edge_audit.csv`

| Column | Source |
| --- | --- |
| `strategy` | `backtest_runs.strategy` |
| `timeframe` | `backtest_runs.timeframe` |
| `regime` | derived (trend/range/high_vol via `analytics/regime.py` stub from §6) |
| `session` | derived (Asia/London/NY/off via candle hour UTC) |
| `cofired_with` | NULL for standalone, partner strategy for combo rows |
| `n_trades` | trade count in slice |
| `avg_r` | mean R |
| `win_rate` | wins / n |
| `expectancy_R` | avg_r × win_rate |
| `pct_of_fires` | fraction of total fires for this strategy |
| `verdict` | `KEEP` / `DEMOTE` / `KILL` per decision rule |

---

## 2. Decision rule (deterministic — no judgement calls)

```text
KILL  : avg_r ≤ 0 in ALL slices AND no combo with avg_r ≥ +0.10R uplift
DEMOTE: positive edge in ≤2 slices OR combo-only edge → confluence-feature, not alerter
KEEP  : positive edge in ≥3 slices OR top-decile expectancy_R standalone
```

Tie-breakers: prefer KEEP when n_trades < 30 in the strongest slice (insufficient data → don't kill on noise).

---

## 3. Implementation (1–2 days)

1. **New script `tools/strategy_edge_audit.py`** (~200 LOC). Reads `backtest_runs`, `backtest_trades`, `backtest_combos`, `backtest_cross_tf_combos` from DuckDB. No external deps beyond what's already used.
2. **Stub regime classifier** (`analytics/regime.py`, §6 of redesign) — needed for regime slicing. Pure function: `regime(symbol, tf, candle_t) → "trend"|"range"|"high_vol"`. ~50 LOC. Lands as a real module (not throwaway).
3. **Session bucket helper** in same script: hour UTC → Asia (00–08) / London (08–13) / NY (13–22) / off (22–24).
4. Run, sort by `verdict` then `expectancy_R`, write CSV.
5. Manual review pass — eyeball KILL list before any deletion PR.

**No DB migrations. No code under `analytics/strategies/` touched.** Phase 0 is pure analytics — zero behaviour risk, fully reversible.

---

## 4. Acceptance gate (what must be true before §3 cuts proceed)

- For every detector marked for deletion in redesign §3, the audit CSV must show `verdict = KILL`.
- Any detector currently slated for deletion that the audit marks `KEEP` or `DEMOTE` → redesign §3 amended in place before any branch is cut.
- Combo / cross-TF slices populated for every active detector (catches "negative standalone, positive in combo" — your stated concern).

---

## 5. What this does NOT do

- Does not run new backtests. Uses accumulated data only.
- Does not change configs, detectors, or alerter behaviour.
- Does not address §4 unified scoring or §6 regime gating — those stay in Phase 1+.
- Does not tune `tp_r` (that's `/wfo-sweep` territory).

---

## 6. Risks

- **Sample size**: Some (strategy × regime × session × combo_partner) cells will have <10 trades. Mitigation: tie-breaker rule above, plus a `confidence` column flagging slices with n<30.
- **Regime label drift**: classifier stub today vs. final classifier in Phase 2 may disagree at boundaries. Acceptable — Phase 0 is directional, not load-bearing for tp_r.
- **`backtest_runs.source` is NULL for old rows** (per memory). Audit must include all sources or document the bias.
