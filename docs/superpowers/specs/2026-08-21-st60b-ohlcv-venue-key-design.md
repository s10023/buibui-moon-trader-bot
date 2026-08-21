# `ohlcv` venue key — ST60(b) design

**Date:** 2026-08-21 · **Status:** approved, unbuilt · **Scope:** `analytics/store/`,
`analytics/data_sync.py`, `utils/binance_client.py`, `tools/export_live_db.py`, one migration
tool, 22 test insert sites · **Source:** SoT ST60(b) — the prevention half of the
OKX-overwrite defect whose *detection* half shipped in #676 and whose *NULL* half shipped
in #678

This touches the backtest surface, so `make test-regression` applies. §8 pre-registers the
claim that the goldens must **not** move, before anything is built.

## 1. Problem

Stated in full in `AGENTS.md` under "Code-level rules" and in SoT ST60 — not restated here.
The one line that shapes this design: `ohlcv`'s primary key is `(symbol, timeframe,
open_time)` with no venue component, and `upsert_ohlcv` REPLACES on conflict, so a
`DATA_SOURCE=okx` run against the real DB destroys Binance bars instead of landing beside
them. #678 stopped the destroyed field passing for a measurement; it did not stop the
destruction.

Live DB measured 2026-08-21: **2,003,340 rows**, 9 columns, all Binance.

## 2. Goal, and the explicit non-goal

**Goal — prevention.** Make the collision unreachable rather than merely visible: an OKX row
and a Binance row for the same `(symbol, timeframe, open_time)` both exist, and neither can
overwrite the other.

**Non-goal — comparison (operator call, 2026-08-21).** No venue-aware read API, no
disagreement-flagging surface, no consumer changes. The 08-21h ingest row's
"keep a source disagreement as a flagged pair" argument is *satisfied by retention alone*
here — both rows survive and are queryable ad-hoc. Building a comparison surface on a field
whose only consumers are a shelved sleeve and one 1h detector was priced and declined.

## 3. Schema

The physical table carries every venue; a **view named `ohlcv`** is the read path, so no
existing read site changes.

```sql
CREATE TABLE IF NOT EXISTS ohlcv_all (
    venue            TEXT   NOT NULL,
    symbol           TEXT   NOT NULL,
    timeframe        TEXT   NOT NULL,
    open_time        BIGINT NOT NULL,
    open             DOUBLE NOT NULL,
    high             DOUBLE NOT NULL,
    low              DOUBLE NOT NULL,
    close            DOUBLE NOT NULL,
    volume           DOUBLE NOT NULL,
    taker_buy_volume DOUBLE,
    PRIMARY KEY (venue, symbol, timeframe, open_time)
);

CREATE TABLE IF NOT EXISTS db_meta (
    key   TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (key)
);
-- seeded once: ('read_venue_order', 'binance')
```

`db_meta.read_venue_order` is a comma-separated preference list stored **in the database**,
never read from the process environment. `init_schema` GENERATES the view from it:

```sql
-- stored order 'binance' (the real analytics.db)
CREATE OR REPLACE VIEW ohlcv AS
SELECT symbol, timeframe, open_time, open, high, low, close, volume, taker_buy_volume
FROM ohlcv_all
WHERE venue = 'binance';

-- stored order 'okx,binance' (the CI slim DB — §7)
CREATE OR REPLACE VIEW ohlcv AS
SELECT symbol, timeframe, open_time, open, high, low, close, volume, taker_buy_volume
FROM ohlcv_all
WHERE venue IN ('okx', 'binance')
QUALIFY row_number() OVER (
    PARTITION BY symbol, timeframe, open_time
    ORDER BY CASE venue WHEN 'okx' THEN 0 WHEN 'binance' THEN 1 END
) = 1;
```

Two properties are load-bearing:

- **`WHERE venue IN (...)` is not decoration.** A venue absent from the stored order is
  invisible to every read, so an unrecognised venue can never shadow a known one.
- **The single-venue form is a plain filter, not a window.** The 2M-row production case pays
  no window-function cost; only a genuinely multi-venue DB does.

⚠ **Views appear in `information_schema.columns` but NOT in `duckdb_tables()`** (probed,
DuckDB 1.5.5). `schema.py`'s existing `taker_buy_volume` ALTER guard keys on
`information_schema` and would therefore start inspecting the *view*. It must be re-keyed
onto `ohlcv_all` via `duckdb_tables()`, which is also the discriminator §6's refusal guard
uses.

## 4. Reads — unchanged, and unchanged *correctly*

All ~50 non-test SQL sites and every `get_ohlcv` caller keep reading `ohlcv` and keep getting
exactly the rows they get today. Nothing becomes venue-aware.

Two deliberate exceptions inside `analytics/store/market_data.py`:

- **`FABRICATED_CVD_SQL` moves to `FROM ohlcv_all`.** Per `AGENTS.md` that check now guards
  history and regressions rather than the live adapter, so it must scan every venue. Reading
  it through the view would blind it to exactly the rows it exists to find.
- **`latest_open_time` stays on the view.** `sync` resumes from it, and resuming from the
  preferred venue's tail is what reproduces current behaviour in both environments.

A cross-venue read is `FROM ohlcv_all` with an explicit `venue` predicate. No helper is added
for it (§2 non-goal).

## 5. Writes — one function, one venue argument

`upsert_ohlcv(conn, df, *, venue: str)` targets `ohlcv_all` and stamps the column. It is the
**only** production writer. Of the 25 `INSERT INTO ohlcv` matches in the tree, **22 are
statements in `tests/`** and the remaining three are comments — one test docstring, two in
`tools/export_live_db.py`. There is no second production path to keep in step.

`_upsert`'s body is sealed (heap-corruption note in `analytics/store/_common.py`) and is not
touched; only the table name and the column list change at the call site.

Venue resolution lives beside the client resolution it mirrors: `utils/binance_client.py`
gains `resolve_venue() -> str` reading the same `DATA_SOURCE` env var (`binance` default).
`data_sync.backfill` / `sync` take `venue: str | None = None` and fall back to
`resolve_venue()`, so the tag is derived rather than separately configured, and tests that
inject a `MagicMock` client can still pass a venue explicitly. **Venue is never inferred from
the client object** — a mock has no venue, and inferring one would make the tag untestable.

## 6. Migration — explicit, guarded, never implicit

`analytics.db` is single-copy and gitignored, and the 15-minute timer owns it. A rebuild of a
2M-row table must not happen silently underneath whichever process connects first.

**`tools/migrate_ohlcv_venue.py`**, run once by the operator:

1. Refuses unless a local backup snapshot exists and is <24h old (reads the `MANIFEST.json`
   the backup script writes). `--force` overrides, and says so loudly.
2. Refuses if `ohlcv_all` already exists — idempotent, so a second run is a no-op, not a
   second rebuild.
3. In one transaction: create `ohlcv_all`; copy every existing row into it stamped
   `'binance'`; assert the row counts match; drop the old `ohlcv` table; create the view;
   seed `db_meta.read_venue_order = 'binance'`.
4. Prints before/after counts and a read-back of one slice through the new view.

**`init_schema` refuses rather than migrates.** On a DB where `ohlcv` exists as a *table* and
`ohlcv_all` does not, it raises with a message naming the tool. Fresh databases — every test,
every CI run — get `ohlcv_all` + `db_meta` + the view directly and need no migration at all,
which is why the change is invisible to the great majority of the suite.

## 7. `export_live_db.py` and the CI signal-watch path

`.github/workflows/signal-watch.yaml` seeds an ephemeral DB from the committed
`live_signal.duckdb`, then each cycle runs `sync()` under `DATA_SOURCE=okx` and reads a
200–600 bar `scan_window` back out of the same table. **That path deliberately wants a mixed
series** — Binance history underneath, OKX's fresh tail on top. A binance-pinned view would
make the synced candles invisible; an okx-pinned view would starve every detector on its
200-bar warmup.

So the exporter stamps the slim DB it produces with `read_venue_order = 'okx,binance'`, and
CI's reads then prefer the OKX tail and fall back to Binance history — **byte-identical to
today's behaviour, with nothing destroyed**. Changes needed:

- `LIVE_TABLES`: `ohlcv` → `ohlcv_all`; the symbol/floor-scoped copy gains a venue column and
  targets `ohlcv_all`.
- A `--read-venue-order` flag, defaulting to `okx,binance`, written into the output DB's
  `db_meta`. The exported DB exists to be extended by an OKX run; that default encodes it.

## 8. Tests — and one pre-registered claim

**Pre-registered, before anything is built: `make test-regression` goldens MUST NOT move.**
The view returns the same rows in the same order as today's table, so a moved golden is a
defect in this change, not a golden to regenerate. If one moves, stop and find out why — do
not run `make regression-update`.

New coverage:

- **Collision:** write a Binance bar and an OKX bar on the same `(symbol, timeframe,
  open_time)`; assert `ohlcv_all` holds both and the Binance values are untouched. This is
  the whole point of the item and is the test to read first.
- **View, single-venue order:** reads return Binance rows only.
- **View, `okx,binance` order:** OKX wins where present, Binance fills every gap — the CI
  shape, exercised without CI.
- **Unknown venue is invisible:** a row tagged `kraken` is absent from both view forms.
- **`init_schema` refusal:** an unmigrated DB raises, and the message names the tool.
- **Migration:** idempotent on a second run; row count preserved; a slice read through the
  new view is identical to the same slice read before migrating.

**Mutation cases are required, not optional** — per `AGENTS.md`, a green test proves nothing
about reach until a mutation proves it, and reach is not scope. At minimum: deleting the
view's venue predicate must fail the collision test, and re-keying the `init_schema` guard
back onto `information_schema` must fail the refusal test.

The 22 `INSERT INTO ohlcv` statements in `tests/` move to `ohlcv_all` with a venue literal.
Most are positional `VALUES (...)`, so they break under *any* approach that adds a column —
unavoidable churn rather than a cost of the view.

## 9. What this does NOT do

- **It does not stop a local `DATA_SOURCE=okx` run writing OKX rows.** It makes them inert —
  reads ignore them, Binance bars survive, and the visible symptom is bars failing to
  advance. That is a strictly better failure than today's silent destruction, but it is not
  an error message.
- **`spot_ohlcv` stays venue-blind.** Out of scope; no caller mixes venues into it today.
- **Perf is measured only synthetically.** On a 2M-row table with the real 600-bar slice
  shape: plain table 2.62 ms, single-venue view 4.03 ms, preference view 5.48 ms per read.
  The suite-level effect is **unmeasured** until `make test` runs against the built change —
  §8's regression leg and a `make test` wall-clock comparison settle it, and a material
  regression reverses decision D2 below.

## 10. Decision log

| Decision | Alternatives rejected | Why | What would reverse it |
| --- | --- | --- | --- |
| D1 · Venue enters a PK at all | Sibling `ohlcv_alt` table for non-Binance writes (zero migration, zero test churn) | `ohlcv` would stay venue-scoped-but-unlabelled, so the data model still lies and a third venue re-opens the question; diverges from the `venue_spot_daily` precedent the SoT names | A second venue never materialises AND the migration proves costlier than budgeted — then the sibling table buys the same prevention for less |
| D2 · A view named `ohlcv` over a physical `ohlcv_all` | Venue in `ohlcv`'s own PK with `AND venue = ?` at ~50 read sites | A forgotten filter returns two venues interleaved on one `open_time` — silently duplicated history, a *worse* failure than the overwrite it replaces. The view also answers "will the goldens move" by construction rather than by argument | `make test` wall clock regresses materially against main, or a read pattern emerges that the view cannot express |
| D3 · Preference order stored in `db_meta` | (a) Read `DATA_SOURCE` when building the view; (b) a constant "prefer okx" order | (a) makes the view definition shared mutable state — a stray okx process flips what the 15-min timer reads afterwards, a new silent failure for an old one. (b) needs no state but lets a local okx run shadow Binance in every read | A second process legitimately needs a different preference against the *same* DB file — then the preference must move to the connection, not the schema |
| D4 · Migration is an explicit tool that `init_schema` refuses without | Migrate inside `init_schema`, as the existing `taker_buy_volume` ALTER guard does | That guard adds a nullable column; this rebuilds a 2M-row table on a single-copy gitignored DB owned by a 15-minute timer. Automatic is right for the cheap case and wrong for this one | The table shrinks enough, or DuckDB gains an in-place primary-key alter, that the rebuild stops being a rebuild |
| D5 · Prevention only, no comparison surface | Venue-aware read API + disagreement flagging | Operator call 2026-08-21. Retention alone satisfies the "flagged pair" argument; the consumers are a shelved sleeve and one 1h detector | A second venue's data starts feeding a live decision — then disagreement needs to be surfaced, not merely retained |
| D6 · Architectural path, spec first | Bounded — a short in-chat design | Chosen before the CI mixed-read problem was found, and that problem would have been discovered mid-implementation under the bounded path. Recorded because the classification was the right call for the wrong reason | — |
