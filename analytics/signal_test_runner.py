"""Signal test runner — fires a detector against historical data for alert testing.

Unlike the live daemon, this:
  - Does NOT filter to the latest candle only.
  - Does NOT use the cooldown store.
  - Does NOT write to the DB.
  - Prints the formatted alert to stdout and optionally sends it to Telegram.

Intended for testing alert formatting changes without waiting for a live signal.
"""

import logging
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from analytics.data_store import DEFAULT_DB_PATH, get_funding_rates, get_ohlcv
from analytics.indicators_lib import STRATEGY_REGISTRY
from analytics.signal_lib import parse_timeframe_secs
from signals.alert_formatter import SignalEvent, format_confluence_alert
from signals.registry import SIGNAL_REGISTRY
from utils.telegram import send_telegram_message

logger = logging.getLogger(__name__)

_MYT = ZoneInfo("Asia/Kuala_Lumpur")


def run_signal_test(
    symbol: str,
    timeframe: str,
    strategy: str,
    at_ms: int | None = None,
    lookback: int = 200,
    tp_r: float = 2.0,
    sl_pct: float = 0.02,
    min_sl_pct: float = 0.0,
    direction_filter: str | None = None,
    send_telegram: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Run a detector against historical candles and format the most recent signal.

    Parameters
    ----------
    symbol:
        Trading pair, e.g. ``BTCUSDT``.
    timeframe:
        Candle timeframe, e.g. ``1h``.
    strategy:
        Strategy name from SIGNAL_REGISTRY, e.g. ``bos``.
    at_ms:
        Pin to a specific candle (Unix ms, UTC). The df is trimmed to candles
        with ``open_time <= at_ms`` before running the detector. Defaults to now.
    lookback:
        Number of candles to load ending at ``at_ms`` (or now). Default 200.
    tp_r:
        TP risk:reward for alert formatting. Default 2.0.
    sl_pct:
        Fallback SL pct when no structural SL is available. Default 0.02.
    min_sl_pct:
        Minimum SL distance as fraction of price. Default 0.0 (disabled).
    direction_filter:
        When set to ``"long"`` or ``"short"``, only signals in that direction
        are considered. Default None (both directions).
    send_telegram:
        When True, sends the formatted alert via Telegram. Default False.
    db_path:
        Path to the DuckDB analytics database.

    Returns
    -------
    str | None
        The formatted alert text, or None if no signal was found.
    """
    plugin = SIGNAL_REGISTRY.get(strategy)
    if plugin is None:
        raise ValueError(
            f"Unknown strategy '{strategy}'. "
            f"Available: {sorted(SIGNAL_REGISTRY.keys())}"
        )

    spec = STRATEGY_REGISTRY.get(strategy)

    end_ms = at_ms if at_ms is not None else int(time.time() * 1000)
    tf_secs = parse_timeframe_secs(timeframe)
    start_ms = end_ms - lookback * tf_secs * 1000

    with duckdb.connect(str(db_path), read_only=True) as conn:
        df = get_ohlcv(conn, symbol, timeframe, start_ms, end_ms)
        funding_df = (
            get_funding_rates(conn, symbol, start_ms, end_ms)
            if spec and spec.requires_funding
            else None
        )

    if df.empty:
        raise ValueError(
            f"No OHLCV data for {symbol}/{timeframe} in the requested window. "
            f"Run 'buibui analytics backfill --symbols {symbol}' first."
        )

    # Trim to at_ms boundary (inclusive) so the pinned candle is the "latest".
    if at_ms is not None:
        df = df[df["open_time"] <= at_ms].copy()
        if df.empty:
            raise ValueError(
                f"No candles for {symbol}/{timeframe} at or before the requested timestamp."
            )

    # Drop the last (potentially open/forming) candle — mirrors scan_symbol behaviour.
    closed_df = df.iloc[:-1].copy()
    if closed_df.empty:
        raise ValueError("Not enough closed candles in the requested window.")

    pinned_close = float(closed_df["close"].iloc[-1])

    # Run the detector on the full closed window (no latest-candle filter).
    requires_funding = spec.requires_funding if spec else False
    requires_secondary = spec.requires_secondary if spec else False

    if requires_funding:
        if funding_df is None or funding_df.empty:
            raise ValueError(
                f"Strategy '{strategy}' requires funding rates but none are available "
                f"for {symbol}. Check your DB."
            )
        signals_df = plugin["detector"](closed_df, funding_df)
    elif requires_secondary:
        raise ValueError(
            f"Strategy '{strategy}' requires a secondary symbol (SMT). "
            "Pass the secondary OHLCV manually or use a non-SMT strategy."
        )
    else:
        signals_df = plugin["detector"](closed_df)

    if signals_df.empty:
        print(
            f"No signals found for {symbol}/{timeframe}/{strategy} in the loaded window."
        )
        return None

    # Apply optional direction filter.
    if direction_filter:
        signals_df = signals_df[signals_df["direction"] == direction_filter]
        if signals_df.empty:
            print(
                f"No {direction_filter} signals found for {symbol}/{timeframe}/{strategy}."
            )
            return None

    # Take the most recent signal.
    row = signals_df.iloc[-1]
    signal_ts = int(row["open_time"])
    signal_close = float(
        closed_df.loc[closed_df["open_time"] == signal_ts, "close"].iloc[0]
        if signal_ts in closed_df["open_time"].values
        else pinned_close
    )

    confidence = STRATEGY_REGISTRY[strategy].get_confidence(timeframe) if spec else 3

    event = SignalEvent(
        symbol=symbol,
        timeframe=timeframe,
        strategy=strategy,
        direction=str(row["direction"]),
        reason=str(row.get("reason", "")),
        open_time=signal_ts,
        price=signal_close,
        sl_price=float(row.get("sl_price", 0.0)),
        tp_price=float(row.get("tp_price", 0.0)),
        context=str(row.get("context", "")),
        confidence=confidence,
        conflict=False,
        low_volume=bool(row.get("low_volume", False)),
        volume_spike=bool(row.get("volume_spike", False)),
    )

    alert_text = format_confluence_alert(
        [event],
        tp_r=tp_r,
        sl_pct=sl_pct,
        min_sl_pct=min_sl_pct,
    )

    print(alert_text)

    if send_telegram:
        send_telegram_message(alert_text)
        print("\n[Telegram] Alert sent.")

    return alert_text
