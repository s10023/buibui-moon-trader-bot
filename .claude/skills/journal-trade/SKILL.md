---
name: journal-trade
description: >
  Capture a trade the user took into the gitignored trade journal at
  `docs/plans/journal/` — structured YAML frontmatter (machine-parseable) plus a
  narrative body (Thesis / Plan / Execution / Outcome / Retrospective). By default
  it now FETCHES recent trades from the user's Binance Futures account via
  `tools/journal_fetch.py` (fetch → pick → pre-fill the mechanical facts), leaving
  only judgement to the human; a legacy paste-the-details path remains for
  non-Binance trades. Entries are ground-truth training data for F2 (AI trade-card),
  validation for T2 (outcome loop), and a source of T5 trade-management heuristics.
  Invoke when the user says "/journal-trade", "journal my trade", "log this trade",
  "add a trade to the journal", or pastes raw trade-execution details. Also use to
  fill in the outcome after a logged trade closes.
allowed-tools: Bash, Write, Read, Edit
---

# Journal Trade

Turn the user's trade into a structured journal entry. The journal is the ground-truth corpus
for the eventual trade-planning system — every entry is a closed-loop prediction (plan) + result
(outcome) + lesson (retrospective). See `[[project_trade_journal_procedure]]` in memory.

The user should only supply **judgement** — thesis, soft-stop invalidation, intent, tags,
retrospective. The **mechanical facts** (fills, $ PnL, fees, funding, exchange SL/TP, and the
detected-signal context) are fetched from the user's own account and pre-filled.

## When to use

- User says `/journal-trade`, "journal my trade", "log this trade", or pastes trade-execution details.
- A previously-logged trade closed and the user reports the exit → fill in the post-close fields.
- Several entries have accumulated and the user wants them mined for patterns.

## Where it lives

- Directory: `docs/plans/journal/` — **gitignored** (personal financial data, never committed).
  Run `mkdir -p docs/plans/journal` if absent.
- One markdown file per trade. Filename: `YYYY-MM-DD-HHMM-<symbol>-<direction>.md`
  (entry instant, **UTC**) — `suggested_filename` emits it. **The `HHMM` is load-bearing:**
  without it a second wave on the same symbol, direction and UTC day proposes the filename
  the first one already owns, and writing it OVERWRITES a gitignored, single-copy entry.
  Pre-2026-08-27 entries carry the date-only form and stay valid — the dedup key reads
  frontmatter, not the filename.
- `docs/plans/journal/TEMPLATE.md` holds the canonical template — read it if unsure of current fields.

## Default flow — API-assisted (fetch → pick → pre-fill)

Use this when the user invokes the skill **without** pasting trade details.

1. **Fetch.** Run the read-only reconstructor (it never places/cancels orders):

   ```bash
   PYTHONPATH=. poetry run python tools/journal_fetch.py --json [--days N]
   ```

   Defaults to a 7-day lookback over the coins.json traded set ∪ any open position; bump
   `--days` for older trades. Parse the JSON list of trade candidates (closed round-trips +
   open positions). Each candidate already carries `already_journaled` so the user doesn't
   double-log.

   **`already_journaled` keys on symbol + direction + entry INSTANT** (`entry_ts_utc`, falling
   back to the id's `-HHMM-` prefix, matched within 30 minutes of the candidate's open). A
   second wave on the same symbol, direction and UTC day is therefore its own trade and stays
   listed. ⚠ **Until 2026-08-27 it keyed on the entry DATE alone**, so the second wave read as
   journaled the moment the first was filed and then vanished — measured: filing the 25-Aug
   05:47 UTC BTC/ETH/SOL short hid the 03:17 UTC one, invisibly. **A journal entry that states
   no `entry_ts_utc` and no `-HHMM-` id can still only be matched on the day**, which is what
   keeps the 23 legacy entries recognised — so fill `entry_ts_utc` on every new entry.
   `--include-journaled` remains the way to re-read a trade already filed.

   **`--days` has a hard ceiling around 83 that the tool cannot tell you about.**
   `journal_fetch.py` reads fills from `/fapi/v1/userTrades`, which Binance retains for
   only ~83 days; past that the endpoint returns fewer rows **with no error and no
   warning**, so `--days 200` silently yields ~83 days of trades and looks like a quiet
   stretch of not trading. If a trade is older than that, fills are gone — the P&L is
   still recoverable from `/fapi/v1/income` (~210 days) or the `income/asyn` export (back
   to inception), but neither carries maker/taker or per-fill detail. Full retention map
   and its traps → `.claude/context/tools.md`, under `journal_fetch.py`.

2. **Present** a compact numbered list: `#`, symbol, direction, status, `avg_entry → avg_exit`,
   `$ PnL`, and an "already journaled" marker. (Running the tool without `--json` prints this
   table directly.)

3. **Pick.** The user names one or more (`journal #2 and #3`). Draft them all in one pass and
   cross-link (`[[2026-06-18-eth-short]]`); keep the per-symbol filename convention.

4. **Pre-fill** the frontmatter from the candidate JSON — copy `suggested_filename` stem → `id`:

   | Frontmatter | Source (candidate field) |
   | ----------- | ------------------------ |
   | `symbol` / `direction` | `symbol` / `direction` |
   | `entry_ts_utc` | first `entry` leg `ts_utc` (→ `YYYY-MM-DD HH:MM`); `entry_ts_myt` = UTC + 8h |
   | `entry_price` | `avg_entry` (list each `add` leg in **## Execution**) |
   | `exit_ts_utc` / `exit_price` | `closed_ts_utc` / `avg_exit` (blank while `status: open`) |
   | `sl_price` / `sl_type` | `initial_sl` (the stop in force AT ENTRY) → `sl_type: hard`; **soft/mental stop stays a blank**. `exchange_sl` is the LAST working stop — use it only for the trailed-R note, never as the R basis |
   | `tp_eventual` | `exchange_tp` when present (else blank) |

   Put `$ PnL`, fees, and funding (`realized_pnl_usd` / `fees_usd` / `funding_usd`) into the
   **## Outcome** section. Set `outcome` tentatively from the sign of `realized_pnl_usd`
   (flag it for the user to confirm). Leave `r_realized` **blank** — it is scored against the
   soft stop (below), which the API cannot know.

5. **Signal replay (auto).** For each `entry` and `add` leg, run the **raw-detector** replay
   (no cooldown / min_avg_r / combo / F8 live gates — matching how existing entries were built):

   ```bash
   PYTHONPATH=. poetry run python buibui.py signal test --symbol <SYM> --at "<leg ts UTC>"
   ```

   Draft `strategies_seen` (e.g. `wick_fill_short_15m, pin_bar_short_1h`) and a **## Signal
   replay** subsection noting detected-vs-missed + direction alignment (see
   `docs/plans/journal/2026-06-18-btc-short.md` for the format). Prose stays human-editable.
   For deeper why-did-it-fire questions, pivot to `/investigate-strategy`.

6. **Leave human blanks** — thesis, intent, soft-stop invalidation (`sl_basis`), `tp_planned`,
   `thesis_tags`, `htf_bias`, the **## Retrospective**, and `self_rating`. These are judgement.

## Legacy flow — manual paste

Retained for trades **not** on the Binance account (other venue / manual) and for trades the
fetch can't reconstruct:

1. **Parse** the user's raw details: symbol, direction, intent, entry + reason, SL (type/level/basis),
   TP (planned + eventual + reason), planned RR, hold expectation, outcome if known.
2. **Convert timestamps**: user gives **MYT (UTC+8)** — record both MYT *and* **UTC = MYT − 8h**.
   UTC is mandatory (the bot reasons in UTC, so entries cross-reference `backtest_trades` / signals).
3. **Tag** `thesis_tags` + `strategies_seen` (cross-check `analytics/strategies/` names).
4. **Write** the file from the template; post-close fields stay blank until the trade closes.

## R / SL scoring convention

Score `r_realized` against the **stop that was actually working in the market** — the **soft**
stop if one was set (the real invalidation), not the wide hard catastrophe stop. The soft stop
is a human input, so `r_realized` is computed once the user supplies the invalidation. When
ambiguous, note both in **## Outcome** (e.g. "+3R on soft stop / +0.65R on hard 2%") and
confirm with the user. Keep the convention consistent across all entries. (Default chosen
2026-05-25 — soft-stop basis.)

**Trailed stops are the majority case, and they need a rule of their own.** A hard stop is
rarely one number: the exchange auto-attaches one at entry (`source: auto`) and the operator
then moves it, usually from a phone (`source: phone`). Scoring against wherever the stop
finally sat flatters or wrecks the R at random — the two bases differed by **4.7x** on the
2026-08-04 basket.

**The rule: score against the stop in force AT ENTRY (`initial_sl`), and note the trailed R
beside it.** `journal_fetch.py` gives you both plus the full `sl_history`
(`ts` / `trigger_price` / `qty` / `status` / `source`, oldest first), and the table's `SL`
column renders `98.00→104.00` when a trail happened and a single number when it did not.

`sl_history` answers one thing fills alone cannot: **who** moved the stop (`source`).

⚠ **It does NOT reliably answer how the position ENDED, and `status` alone is not the test.**
This file said `EXPIRED`/`FINISHED` meant the stop fired and `CANCELED` meant a manual exit.
**Falsified 2026-08-27**: BTCUSDT 2026-08-20 16:55 UTC reads `EXPIRED` with an exit **2% away
from its stop** — the order merely expired when the position went flat, 37 seconds after entry.
Read that way it manufactures phantom stop-outs, which is how a −$1.82 scratch was first
mis-filed as one.

**The test is the exit PRICE, not the status: a stop fired iff `avg_exit` is within ~0.2% of the
working stop** (use `exchange_sl`, falling back to `initial_sl`). Confirm with `status` in
(`EXPIRED`, `FINISHED`) as a second condition, never as the first. ⚠ **The fill can land on
EITHER side of the trigger** — Binance triggers on MARK price and fills at LAST, so a genuine
stop-out can print *better* than its level (measured: −0.04% on one, +0.05% on another, both
real). An exact-or-worse test misses half of them.

⚠ **`sl_history` is built ONLY from `/fapi/v1/allAlgoOrders`.** A stop placed as a classic
conditional order lands in `/fapi/v1/allOrders`, which the reconstructor reads for OPEN
positions only — so it would be silently absent from a closed trade's history. Verified
2026-08-27 that this account routes every stop through the algo endpoint (its classic history
holds nothing but market fills), so the reconstruction is complete **for this account**; check
before trusting it on another, and query both endpoints directly whenever the operator says a
stop was moved and `sl_history` disagrees.

**Consecutive rows at the SAME price are re-arms, not trail steps.** Adding to a position
cancels the working stop and re-places it for the new size, so one stop level can appear three
or four times. Count *distinct prices* when describing the trail. Measured on the 2026-08-04
basket: 5 BTCUSDT rows, but only two levels (65,265.70 → 64,240.00), and the R gap between
them was **4.70x** (+0.336R at entry basis vs +1.578R trailed) — 5.15x on ETH, 3.21x on SOL.

## Filling in an outcome (trade closed)

Read the existing entry, then Edit: set `exit_ts_myt` / `exit_ts_utc` / `exit_price` /
`r_realized` / `outcome` (win|loss|breakeven|scratch) / `self_rating` (1-5, execution quality
independent of P/L), and complete the **## Outcome** and **## Retrospective** sections. If the
trade is on Binance, re-run `tools/journal_fetch.py --include-journaled` to pull the closed-out
$ PnL / fees / funding.

## Mining (when entries accumulate)

Offer to read all `docs/plans/journal/*.md` and report: win-rate by `thesis_tag` and setup template,
which setups the bot already detects (`strategies_seen`) vs misses, recurring trade-management heuristics
worth promoting to T5, and any detector gaps worth a `/investigate-strategy` probe. Feeds **F2 / T2 / T5**
in the master to-do (tracked as **J-LOG**).
