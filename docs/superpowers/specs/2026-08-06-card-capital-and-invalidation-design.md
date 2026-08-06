# Design: `/card` capital resolution (+ the SL-vs-invalidation check, dropped)

**Date:** 2026-08-06
**Status:** Approved — implementation pending. **Scope reduced 2026-08-06** to
Components 1 and 2; Component 3 was dropped before implementation when a source check
falsified its premise (see that section — it is kept, not deleted).
**Supersedes:** the Task 2b bullets in `docs/plans/next-conversation-prompt.md` (gitignored)

## Problem

`SizingConfig.capital` (`portfolio/sizing.py:21`) defaults to `10_000.0` and nothing
overrides it on the `/card` path. It is used as a stand-in for "the account" by two
independent consumers, so one stale constant produces two wrong answers:

| Consumer | Site | Consequence |
| --- | --- | --- |
| Position sizing | `card/card.py:326` — `risk_usd = sizing.capital * r_adm` | printed risk fraction understates real risk |
| Daily circuit breaker | `card/state.py:272` — `daily_r = pnl / (sizing.capital * sizing.r_base)` | `daily_loss_limit_r` cannot fire at its intended severity |

Measured against the documented account readings — equity `2000.00` (today's
`daily_check.py` run) and `1201.33` (PR #572, the figure the account "never left") —
the constant is **5× to 8.3× too large**. A card printing `risk $25.00 (0.25% of
capital)` is really risking **1.25%–2.1%**, and three concurrent cards are
**3.7%–6.2%** at risk rather than 0.75%.

The circuit-breaker half is the more serious of the two and was previously unrecorded.
With `r_base = 0.0025`, the R unit is `capital × r_base` = **$25** at the constant but
**$3.00–$5.00** against real equity. A genuine −2R day therefore computes as roughly
−0.24R to −0.4R and passes the `daily_loss_limit_r = -2.0` gate. **The guard is not
mis-labelled; it is off.**

### Three corrections to the previously filed description

Recorded because each was filed as fact and is false — the standing rule is that a
filed artifact is a hypothesis until re-derived from the caller.

1. **A capital override already exists end-to-end.** `CardConfig.sizing_toml`
   (`card/config.py:71`) is read at `cli/card.py:147` via
   `SizingConfig.from_toml(...)`, which parses a **`[portfolio]`** table. The filed
   note said to "set `capital` in a `[card]` TOML"; there is no such key, and
   `CardConfig.from_toml` raises on unknown keys, so following that note verbatim
   fails loudly. Changing the number today needs **zero code**.
2. **The card already fetches real equity and ignores it.**
   `AccountState.equity_usd` is populated at `card/state.py:273` from
   `account_provider.equity_usd()` and is never consulted by sizing. The defect is not
   a missing input; it is an available input left unused.
3. **The prompt version is `card-v3`** (`card/prompt.py:10`), not `card-v4` as filed.

### The RR claim is wrong, and the rule must not be built on it

The filed description says the printed `RR(tp1) 2.65` was "flattered" by a stop sitting
beyond the invalidation level. Arithmetically it was not: risk-per-unit is
`|entry − sl|`, so moving the stop *further* from entry makes the denominator
**larger** and RR **smaller**. The worked example computes
`(64634 − 63847) / (63847 − 63550) = 787 / 297 = 2.65`, and a stop at the stated
invalidation would make risk-per-unit `0` and RR undefined, not smaller.

The real defect is unchanged in severity but different in kind: **the position is held
through the point at which the card itself declares the thesis dead.** The card's prose
read *"breaks below PDL 63847 and fails to reclaim it"* while the stop sat at `63550`,
so 297 points of confirmed break-of-structure were absorbed by design. The rule below
is written on that basis. Building it on the RR claim would produce a check that
measures the wrong quantity.

## Non-goals

- Changing how RR is computed. RR stays anchored to `sl`, which is the actual money at
  risk.
- Auto-trading, or any change to `r_base`, the overlay, or the XS executor.
- The `/card` vs signal-watch DuckDB lock contention (separate item).
- Any SL-vs-invalidation rule, and any change to `card/prompt.py` or `PROMPT_VERSION`
  (Component 3, dropped below). The prompt therefore stays at `card-v3`.

## Component 1 — shared capital resolution

A pure helper in `portfolio/sizing.py`, beside the config it resolves:

```python
def resolve_capital(cfg: SizingConfig, equity_usd: float | None) -> tuple[float, bool]:
    """Capital to size against, and whether it came from live equity."""
```

Live equity wins when it is a finite number greater than zero; otherwise the configured
`cfg.capital`. Returning the source as a `bool` rather than logging inside keeps the
helper pure and lets both call sites decide how to surface it.

Both consumers call this one helper with the same `equity_usd`, so they cannot
disagree:

- `card/state.py` — `daily_r` divides by `resolve_capital(...)[0] * r_base`
- `card/card.py` — `risk_usd` multiplies `resolve_capital(...)[0]`

`post_pass` reads equity from `state.account.equity_usd`, which is `None` whenever the
account is absent. That happens on a pinned `--as-of` run (the account is unpinnable and
deliberately omitted) and on a credentials/network failure. Both fall back to config
capital; the fallback is surfaced as a warning, never silent.

**Degenerate inputs must fall back, not propagate.** `equity_usd` of `0.0`, a negative,
`NaN`, or `inf` all take the config path. A zero would otherwise size every card to zero
and read as a lot-size veto; a `NaN` would poison `risk_usd`, `risk_frac`, and
`notional_usd` without raising.

## Component 2 — record the capital behind every card

`FinalCard` gains `capital_used: float | None` and `capital_source: str | None`
(`"live_equity"` | `"config"`), rendered and written to the ledger.

This is load-bearing rather than cosmetic. Once capital is live, `risk_frac` varies
run-to-run for reasons outside the card, so a ledger row that records the fraction but
not the capital behind it cannot be interpreted after the fact. This is the same lesson
as `last_run.capital_override` in the xsmom executor, where an unrecorded pin made a
poisoned high-water mark undiagnosable.

Both fields are `None` on a VETOED or NO_TRADE card, matching the existing convention
that `size_units` / `risk_usd` / `rr_tp1` are cleared when nothing is sized.

## Component 3 — SL-vs-invalidation check: CONSIDERED AND DROPPED

An `invalidation_level` field plus two veto rules (coherence, then stop-placement with a
`sl_invalidation_tol_frac = 0.25` allowance) was designed, approved, and then **dropped
before implementation** when its premise failed a source check. Recorded here rather
than deleted, so the question is not reopened from the same wrong starting point.

**The premise was that a stop sitting beyond the stated invalidation level is a
defect. It is not — it is what the rubric asks for.** `card/prompt.py:65-67` already
instructs: *"TRADE only when a limit entry at a structural level, **a structural SL
beyond it**, and TP1/TP2/TP3 at mapped liquidity give planned RR(tp1) >= 1."*

The worked example confirms it rather than contradicting it. Its prose reads *"breaks
below PDL 63847 **and fails to reclaim it**"* — a two-part condition, break **plus** no
reclaim. A stop 297 points below the level is exactly what implements the
"fails to reclaim" allowance; a stop at the level would be hit by any wick that
subsequently reclaims, which is the outcome the prose explicitly excludes.

Consequences had it shipped:

- **Rule 1 would have vetoed the motivating card itself.** Entry `63847` is the PDL and
  the invalidation level, so `invalidation_level < entry` fails and every such
  entry-at-the-level trade is rejected — a check firing on correct input.
- **Rule 2 would have penalised the reclaim buffer**, which is the mechanism that makes
  a structural entry survivable.
- The `0.25` tolerance had **no measurement behind it**. It was invented to make the
  rule expressible, which is the `bar`-units failure in a new location: a bare number
  that looks portable and silently means something different per symbol and per setup.

**What would be needed to revive it:** a definition of "the stop is unreasonably far past
the level" derived from real cards, not from one example. The cheap first move is to read
`entry` / `sl` / `invalidation` off the rows already in `docs/plans/ai-cards.jsonl` and
ask whether any stop is genuinely indefensible. Until that measurement exists there is no
rule to implement, and a veto without it would cost good cards.

This does not touch Components 1 and 2: the capital defect is measured, carries a named
safety consequence, and shares none of this reasoning.

## Testing

Unit, with no network — mocks passed directly, per the existing suite convention.

**`resolve_capital`:** live equity wins over config; `None` → config; and each
degenerate input (`0.0`, negative, `NaN`, `inf`) → config. Enumerate the input class
rather than spot-check it — the LOT_SIZE defect stood while every spot-check passed.

**Circuit breaker:** a `daily_pnl_usd` that breaches `daily_loss_limit_r` against live
equity but not against the `10_000.0` constant. This is the positive control: it fails
on today's code and passes after the change, so it proves the stimulus is live rather
than asserting an invariant that already held.

**`post_pass`:** `risk_usd` / `risk_frac` computed against live equity; `capital_used`
and `capital_source` recorded on a TRADE and `None` on a VETO; the config-fallback
warning present when the account is absent.

**Regression goldens:** `make test-regression` is expected **unmoved** — the card is not
in the backtest pipeline — and this is to be verified by running it, not assumed. Run it
via `make test-regression`, never a bare `pytest`, which reports the three ~97s golden
backtests as timeout failures under the global 30s cap.

## Definition of Done

`make lint-py`, `make typecheck`, `make test`, `make test-regression` — each run and
each result stated plainly.

## Operational note

Behaviour changes for anyone running `/card` against a funded account: printed risk will
rise to the true figure. That is the point of the change, but it means a card generated
after this lands is not comparable to one generated before it at the same `risk_frac`.
The `capital_source` field in the ledger is what makes the two distinguishable.
