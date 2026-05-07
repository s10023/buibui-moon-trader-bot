# AI Trade Suggestion — Enhanced Prompt + Gap Analysis

Pairs with `buibui-redesign.md` (cross-ref in §3). This is the F2 to-do item ("AI trade suggestions") materialised as a target spec.

---

## 1. Enhanced prompt (drop-in replacement for original)

> **Role**: Act as an elite quantitative trader and technical analyst. You receive a structured market-state JSON for `[ASSET]` containing precomputed indicators, structural zones, derivatives positioning, regime label, and the user's open positions. You do NOT have live chart access — reason from the JSON only.
>
> **Inputs you will be given**:
>
> - OHLCV summaries: 5m, 15m, 1H, 4H, 1D (last 50 bars each + key statistics)
> - Indicator stack: EMA-20/50/200, RSI-14, MACD (12/26/9), ATR-14, ADR consumed%, volume z-score per TF
> - Structural zones: active FVGs, order blocks, EQH/EQL pools, BOS swing points, OTE zones, prior week/day H/L, session H/L
> - Regime label per TF: `trend_up / trend_down / range / high_vol`
> - Derivatives: OI delta% (multi-window), funding z-score, liquidation proxy/real
> - HTF EMA bias (F8 slope)
> - Open positions and daily realised R
> - Detector candidates that fired in the last 4 candles with their backtest avg_r and star rating
>
> **Tasks**:
>
> 1. **Multi-TF synthesis**: state the dominant trend on 1D and 4H, the corrective structure on 1H and 15m, and where 5m sits in that hierarchy. Flag any TF disagreement.
> 2. **Liquidity map**: list the 3 nearest liquidity pools above and below current price, ranked by significance (weekly > daily > 4h > LTF) and proximity. Mark any that have been recently swept.
> 3. **Confluence scan**: enumerate every overlap between (active zones × detector candidates × derivatives positioning × regime). Score each confluence 0–9 using the §4 redesign scoring rubric (context 0–3 + confirm 0–3 + derivatives 0–3).
> 4. **Decision**:
>    - If best confluence ≥ 6/9 AND expected_R ≥ 0.5 AND no hard-gate veto → emit **TRADE** with: direction, entry (limit ± 5 bps), structural SL, TP1 (1R) / TP2 (2R) / TP3 (tp_r × sl_dist), position size for risk %, expected hold time, invalidation criterion, valid-until timestamp.
>    - Else → emit **NO TRADE** with the specific gate that failed (e.g. "regime=high_vol + continuation only eligible setup → dropped per §6 matrix").
> 5. **Reasoning log**: 5–8 bullet points, each citing a concrete number from the input JSON. Forbidden: "looks like", "seems to be", "could potentially". Required: "RSI 14 = 71.2 on 4h (overbought, +1.8σ above 90d mean)".
>
> **Hard rules**:
>
> - Refuse to invent indicator values or zones not present in the input JSON.
> - Never recommend a trade that conflicts with an existing open position on the same symbol.
> - Never recommend an entry that violates the daily-loss circuit-breaker (`daily_R ≤ −2R`).
> - If sample size for the chosen setup is < 30 trades in backtest, downgrade conviction by 1 star and state so explicitly.

---

## 2. What this project has TODAY (vs. what the prompt needs)

| Capability | Project status | Where it lives |
| --- | --- | --- |
| OHLCV multi-TF | ✅ DuckDB, 1m/5m/15m/1h/4h/1d backfilled | `analytics/store/market_data.py` |
| EMA 20/50/200 | ✅ computed, exposed on Chart UI | `analytics/strategies/_shared.py` |
| Structural zones (FVG, OB, EQH/EQL, BOS, OTE) | ✅ extracted | `analytics/zones_lib.py` |
| Detector candidates + avg_r + stars | ✅ live + backtest | `signals/registry.py`, `confidence_ratings` table |
| HTF EMA bias (F8) | ✅ shipped PR #346 | `analytics/signal/gates.py` |
| OI data | ✅ syncs (never used) | `analytics/data_fetcher.py` |
| ADR consumed% | ✅ live gate | `analytics/signal/gates.py` |
| Open positions | ✅ live monitor | `monitor/position_lib.py` |
| Daily realised R | ✅ derivable from `signal_outcomes` | not aggregated to a single number |

## 3. GAPS (what's missing for the AI prompt to actually run)

| # | Gap | Severity | Covered by redesign? |
| --- | --- | --- | --- |
| G1 | **RSI-14, MACD(12/26/9)** — not computed anywhere in `analytics/` | High | ❌ Not in redesign |
| G2 | **Volume z-score per TF** — only have raw volume + a `_is_volume_spike` boolean | Med | ❌ Not in redesign (partial via §4 confirm_score) |
| G3 | **Regime label per TF** | High | ✅ §6 regime classifier (4h primary; needs extension to 5m/15m/1h/1d) |
| G4 | **Funding z-score** — fetcher exists, never wired into `data_sync.sync()` | High | ✅ §5.2 (1-day fix) |
| G5 | **Real liquidation heatmap** — only have proxy via wick+volume | Med | ✅ §5.3 (proxy first; CoinGlass G2c later) |
| G6 | **Multi-TF synthesis layer** — no module fuses 5m→1d into a single narrative; current code emits per-TF independently | High | ✅ §4 unified `SignalCandidate` + score, BUT doesn't produce cross-TF *narrative* |
| G7 | **Liquidity pool ranking with significance score** | Med | ⚠️ Partial — `eqh_eql` + `zones_lib` give pools, but no significance ranking helper |
| G8 | **Daily realised R aggregation + circuit-breaker state** | High | ✅ §7 risk engine |
| G9 | **`expected_R` per setup at entry time** (cached lookup, not a fresh backtest) | High | ✅ §4 — but caching path not yet wired into alert formatter |
| G10 | **"No trade" output with reason** — alerter is silent on rejections; reasons exist in logs only | Med | ✅ §8 alert format implies it; not explicit |
| G11 | **LLM/Claude API integration** — no API client, no prompt cache, no JSON-schema → markdown renderer | High | ❌ Not in redesign at all |
| G12 | **Structured market-state JSON serialiser** — no single function that snapshots the full state for LLM ingestion | High | ❌ Not in redesign |
| G13 | **Symbol-specific HTF level map cached daily** (weekly/prior-day H/L pre-computed) | Med | ⚠️ Computable on demand from `zones_lib`; no daily cache job |
| G14 | **Sample-size flag in confidence rating** (downgrade if n < 30) | Low | ✅ Implicit in `recalibrate_lib`; needs explicit field |

---

## 4. Cross-ref summary: redesign covers ~7/14 gaps

**Redesign solves**: G3, G4, G5, G6 (partial), G8, G9, G10
**Redesign does NOT solve** (separate workstream): G1 (RSI/MACD), G2 (vol z-score), G7 (pool ranking), G11 (Claude API wiring), G12 (state serialiser), G13 (HTF level cache job), G14 (sample-size flag)

Of the redesign-uncovered gaps, **G11 + G12 are the load-bearing ones** — without them the prompt has nothing to consume even if every other indicator exists.

---

## 5. Suggested sequencing (after redesign Phases 1–3 land)

1. **Indicator pack** (G1, G2, G14) — RSI/MACD/vol-z + sample-size flag. Pure additions to `analytics/strategies/_shared.py`. ~3 days.
2. **State serialiser** (G12) — single function `snapshot_market_state(symbol, t) → dict` consumed by both the LLM prompt and the alert formatter. Reuses everything Phase 1–3 builds. ~3 days.
3. **HTF level cache job** (G13) — daily cron that pre-computes weekly/prior-day H/L per symbol; UI + LLM both read from cache. ~2 days.
4. **Pool ranking** (G7) — `analytics/zones_lib.py` extension: `score_pool(zone) → 0..3` based on TF significance + age + sweep history. ~1 day.
5. **Claude API integration** (G11) — `ai/suggest.py` with prompt caching, JSON-schema output, retry/fallback. Pairs with `claude-api` skill. ~3–5 days.
6. **AI tab in UI** — surfaces suggestion + reasoning bullets + accept/reject buttons (which write to `signal_outcomes`). ~3 days.

**Total post-redesign**: ~3 weeks of focused work. **Pre-redesign**: blocked — most of the input fields the prompt requires don't exist yet (G3/G4/G5/G6/G8/G9 all gated on redesign Phases 2–3).

---

## 6. One non-obvious thing the redesign doesn't address

The prompt asks for **"step-by-step reasoning in plain English"**. The redesign's §8 alert structure is a *decision sheet* — facts, no narrative. An AI suggestion layer needs the *justification* to be machine-generated from the same JSON, which means either:

- **(a)** Claude does it (G11) — natural fit, but adds cost + latency to every signal.
- **(b)** Template-based reasoning generator (`ai/explain.py`) — deterministic, free, but rigid.

Recommend (a) for the AI tab (signal-on-demand, user-initiated) and (b) for Telegram alerts (every fire, must be cheap). Both consume the same state JSON.
