---
name: signal-watch
description: >
  Signal daemon workflow, TOML config reference, and end-to-end signal flow
  (sync → detect → dedup → backtest gate → Telegram → persist).
  Invoke when the user says "/signal-watch", configures, debugs, starts, or
  modifies the live signal scanner — even for minor TOML changes or strategy
  additions.
allowed-tools: "*"
---

# Signal Watch Daemon

24/7 signal detection daemon — scans symbols × strategies × TFs on each new candle close, deduplicates, and sends Telegram alerts.

## What it does

1. **Sync candles**: fetches latest OHLCV from Binance Futures, stores in `analytics.db`
2. **Detect signals**: runs all configured strategies via `indicators_lib` detectors
3. **Dedup**: two-layer cooldown in `signals/cooldown_store.py`:
   - Candle watermark per `(symbol, tf, strategy)` — never re-alerts same candle
   - Cooldown timer per `(symbol, strategy, direction)` — default 1h between alerts
4. **Backtest filter**: runs mini-backtest per signal; suppresses if avg_r below threshold (hard mode)
5. **Telegram**: sends formatted alert via `utils/telegram.py`
6. **Persist**: saves passing signals to `signals` table in `analytics.db`

## Signal flow

```
candle close
  → data_sync.py (incremental OHLCV fetch)
  → signal_lib.scan_symbol() (run detectors per strategy)
  → cooldown_store.py (candle watermark + timer dedup)
  → signal_lib._compute_backtest() (backtest filter)
  → alert_formatter.format_signal_alert() (Telegram message)
  → telegram.py (send)
  → data_store.upsert_signals() (persist to DB)
```

## Starting the daemon

```bash
# With config file (recommended)
buibui signal watch --config config/signal_watch.toml

# With Telegram alerts (or set telegram = true in TOML)
buibui signal watch --config config/signal_watch.toml --telegram

# Make alias
make buibui-signal-watch CONFIG=config/signal_watch.toml

# Override specific params via CLI (CLI takes precedence over TOML)
buibui signal watch --config config/signal_watch.toml --timeframes 1h 4h --strategies bos engulfing
```

Note: the subcommand is `signal watch` (two words), not `signal-watch`.

## TOML config structure

Full example at `config/signal_watch.toml`. Key fields:

```toml
# Symbols (omit = all from config/coins.json)
# symbols = ["BTCUSDT", "ETHUSDT"]

timeframes = ["15m", "1h", "4h", "1d"]
telegram = true
min_sl_pct = 0.005

# Day filter: "off" | "weekdays" | "mon_fri" | "tue_thu" | "weekend" | "no_monfi"
day_filter = "tue_thu"

# EMA-50 trend gate for smt_divergence
smt_trend_filter = 1

# Active strategies list
strategies = ['bos', 'engulfing', 'pin_bar', ...]

# Global TP ratio for alert SL/TP display
# tp_r = 2.0

# ATR-based SL (overrides sl_pct when set)
# atr_sl_multiplier = 2.0

# Cooldown between same (symbol, strategy, direction) alerts
# cooldown_seconds = 3600

# Per-strategy TF restrictions
[strategy_timeframes]
trend_day = ["4h", "1d"]
fib_golden_zone = ["4h", "1d"]

# Per-strategy tp_r / sl_pct / volume overrides
[strategy_params.engulfing]
tp_r = 3.0
# volume_suppress = true     # per-strategy override (None = inherit global)

# SMT pairs resolved automatically from coins.json smt_secondary — no need for [smt_pairs]

# Backtest filter config (hard mode = suppress signal if directional avg_r below min_avg_r)
[backtest]
mode = "hard"
days = 200
min_trades = 12
min_trades_15m = 20
min_trades_1h = 12
min_trades_4h = 5
min_trades_1d = 2
min_avg_r = 0.0          # suppress signals with directional avg_r below this threshold
# volume_suppress = false  # global fallback; per-strategy override takes precedence

# F8 HTF EMA gate — suppress counter-trend signals when signal opposes HTF EMA slope
[bias.htf_ema]
enabled = true
mode = "soft"            # "soft" = log only (no suppression); "hard" = live suppression
default_tf = "1d"
default_period = 50
default_slope_lookback = 5
deadband_pct = 0.0
# suppress_directions scopes which signal directions F8 may suppress:
#   ["long","short"] = symmetric (default when key is omitted — back-compat)
#   ["long"]         = counter-trend longs only (production global default)
#   []               = full exempt (signal always passes)
suppress_directions = ["long"]

# Per-strategy overrides (precedence: per-strategy → global → built-in symmetric)
[bias.htf_ema.per_strategy.cvd_divergence]
suppress_directions = []   # flow family: fully exempt

[bias.htf_ema.per_strategy.smt_divergence]
suppress_directions = []   # flow family: fully exempt

[bias.htf_ema.per_strategy.fib_golden_zone]
suppress_directions = ["long", "short"]   # fib family: keep symmetric gate

[bias.htf_ema.per_strategy.ote_entry]
suppress_directions = ["long", "short"]   # fib family: keep symmetric gate
```

Hard-mode flip is deliberately OOS-gated — run `tools/htf_ema_gate_replay.py --oos-frac 0.3`
to confirm OOS edge before changing `mode = "soft"` to `mode = "hard"`.

**Note**: `filter_threshold` was renamed to `min_avg_r`. Update any old TOML that still has the old key.

## Stopping the daemon

The daemon runs in a poll loop — Ctrl+C to stop. State (candle watermarks + cooldown timers) is persisted to `signal_state.json` and survives restarts.

## Testing signal detection without the daemon

Run a single scan cycle manually:

```bash
# Backtest a strategy (validates detection logic)
buibui backtest --symbol BTCUSDT --strategy engulfing --interval 1h

# Force a scan cycle (via Python — no CLI yet)
python -c "
import duckdb
from analytics.signal_lib import scan_symbol
from analytics.data_store import DEFAULT_DB_PATH
conn = duckdb.connect(str(DEFAULT_DB_PATH))
from analytics.signal_config import SignalWatchConfig
from analytics.data_store import get_ohlcv
import time
ohlcv = get_ohlcv(conn, 'BTCUSDT', '1h', 0, int(time.time() * 1000))
result = scan_symbol(conn, 'BTCUSDT', '1h', ['engulfing'], ohlcv)
print(result)
"
```

## Viewing fired signals

```bash
# Via web UI — Signal Feed tab
# Via web API
curl http://localhost:8000/api/signals

# Via DuckDB
duckdb analytics.db "SELECT * FROM signals ORDER BY ts DESC LIMIT 20"
```

## Config files

| File | Description |
|------|-------------|
| `config/signal_watch.toml` | Tue–Thu (`day_filter = "tue_thu"`); curated strategy list |
| `config/signal_watch_weekdays.toml` | Mon + Fri only (`day_filter = "mon_fri"`) |
| `config/signal_watch_all.toml` | Sat + Sun only (`day_filter = "weekend"`) |

The three configs partition the calendar without overlap. When `buibui signal watch` is run with no `--config`, today's **UTC weekday** auto-picks one of them. Explicit `--config X` still wins. UTC (not local time) so the picker agrees with each config's `day_filter`, which evaluates every candle's UTC `open_time`.

## Key implementation files

| File | Role |
|------|------|
| `analytics/signal_lib.py` | `scan_symbol()`, `run_scan_cycle()` — core detection loop |
| `analytics/signal_runner.py` | Thin wrapper: creates client, opens DB, poll loop |
| `analytics/signal_config.py` | `SignalWatchConfig`, `BacktestFilterConfig`, `load_signal_config()` |
| `signals/cooldown_store.py` | Two-layer dedup: candle watermark + cooldown timer (JSON-persisted) |
| `signals/alert_formatter.py` | `format_signal_alert()` → Telegram Markdown message |
| `signals/registry.py` | `SIGNAL_REGISTRY` — 20 actionable strategies |

## Task: configure or debug signal watch

When the user asks to set up, change, or debug the signal watch daemon:

1. Check which config file is in use: `config/signal_watch.toml` is the default
2. For strategy changes: edit `strategies` list in TOML
3. For TF changes: edit `timeframes` and/or `[strategy_timeframes]`
4. For TP changes: edit `[strategy_params.X].tp_r`
5. For backtest filter changes: edit `[backtest]` sub-table
6. Test changes: run a quick backtest first (`buibui backtest --strategy X --interval Y`)
7. Start daemon: `make buibui-signal-watch CONFIG=config/signal_watch.toml`
8. Monitor logs for signal detections and filter decisions
