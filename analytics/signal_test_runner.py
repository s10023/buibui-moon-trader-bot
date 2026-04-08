"""Signal test runner — fires detectors against historical data for alert testing.

Unlike the live daemon, this:
  - Does NOT filter to the latest candle only.
  - Does NOT use the cooldown store.
  - Does NOT write to the DB.
  - Prints formatted alerts to stdout and optionally sends the most recent via Telegram.

Intended for testing alert formatting changes without waiting for a live signal.
"""

import logging
import time
from pathlib import Path

import duckdb
import pandas as pd

from analytics.data_store import DEFAULT_DB_PATH, get_funding_rates, get_ohlcv
from analytics.indicators_lib import STRATEGY_REGISTRY
from analytics.signal_lib import parse_timeframe_secs
from signals.alert_formatter import SignalEvent, format_confluence_alert
from signals.registry import SIGNAL_REGISTRY
from utils.telegram import send_telegram_message

logger = logging.getLogger(__name__)


def _build_event(
    row: pd.Series,
    symbol: str,
    timeframe: str,
    strategy: str,
    closed_df: pd.DataFrame,
    fallback_close: float,
) -> SignalEvent:
    signal_ts = int(row["open_time"])
    matches = closed_df.loc[closed_df["open_time"] == signal_ts, "close"]
    price = float(matches.iloc[0]) if not matches.empty else fallback_close
    spec = STRATEGY_REGISTRY.get(strategy)
    confidence = spec.get_confidence(timeframe) if spec else 3
    tp_raw = row.get("tp_price")
    return SignalEvent(
        symbol=symbol,
        timeframe=timeframe,
        strategy=strategy,
        direction=str(row["direction"]),
        reason=str(row.get("reason", "")),
        open_time=signal_ts,
        price=price,
        sl_price=float(row.get("sl_price") or 0.0),
        tp_price=float(tp_raw) if tp_raw is not None else 0.0,
        context=str(row.get("context", "")),
        confidence=confidence,
        conflict=False,
        low_volume=bool(row.get("low_volume", False)),
        volume_spike=bool(row.get("volume_spike", False)),
    )


def run_signal_test(
    symbols: list[str],
    timeframes: list[str],
    strategies: list[str],
    at_ms: int | None = None,
    lookback: int = 200,
    tp_r: float = 2.0,
    sl_pct: float = 0.02,
    min_sl_pct: float = 0.0,
    direction_filter: str | None = None,
    send_telegram: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Run detectors against historical candles and print formatted alerts.

    Iterates all symbol × timeframe × strategy combos. For each combo, finds
    the most recent signal in the loaded window and prints the formatted alert.
    With ``--telegram``, sends only the single most recent signal overall.

    Parameters
    ----------
    symbols:
        Trading pairs, e.g. ``["BTCUSDT"]``.
    timeframes:
        Candle timeframes, e.g. ``["1h", "4h"]``.
    strategies:
        Strategy names from SIGNAL_REGISTRY.
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
        When True, sends the most recent signal found via Telegram. Default False.
    db_path:
        Path to the DuckDB analytics database.
    """
    now_ms = int(time.time() * 1000)
    end_ms = at_ms if at_ms is not None else now_ms

    # Validate strategies up front.
    for strat in strategies:
        if strat not in SIGNAL_REGISTRY:
            raise ValueError(
                f"Unknown strategy '{strat}'. "
                f"Available: {sorted(SIGNAL_REGISTRY.keys())}"
            )

    # Collect (open_time, alert_text) for all signals found — used to pick the
    # most recent one for Telegram.
    all_found: list[tuple[int, str]] = []
    total_combos = 0
    found_combos = 0

    with duckdb.connect(str(db_path), read_only=True) as conn:
        for symbol in symbols:
            for timeframe in timeframes:
                tf_secs = parse_timeframe_secs(timeframe)
                start_ms = end_ms - lookback * tf_secs * 1000

                df = get_ohlcv(conn, symbol, timeframe, start_ms, end_ms)
                if df.empty:
                    print(
                        f"[{symbol}/{timeframe}] No OHLCV data — "
                        f"run 'buibui analytics backfill --symbols {symbol}' first."
                    )
                    continue

                if at_ms is not None:
                    df = df[df["open_time"] <= at_ms].copy()
                    if df.empty:
                        print(
                            f"[{symbol}/{timeframe}] No candles at or before the pinned timestamp."
                        )
                        continue

                closed_df = df.iloc[:-1].copy()
                if closed_df.empty:
                    print(f"[{symbol}/{timeframe}] Not enough closed candles.")
                    continue

                fallback_close = float(closed_df["close"].iloc[-1])

                for strategy in strategies:
                    plugin = SIGNAL_REGISTRY[strategy]
                    spec = STRATEGY_REGISTRY.get(strategy)
                    total_combos += 1

                    if spec and spec.requires_secondary:
                        print(
                            f"[{symbol}/{timeframe}/{strategy}] Skipped — "
                            "requires secondary symbol (SMT not supported in signal test)."
                        )
                        continue

                    try:
                        if spec and spec.requires_funding:
                            funding_df = get_funding_rates(
                                conn, symbol, start_ms, end_ms
                            )
                            if funding_df.empty:
                                print(
                                    f"[{symbol}/{timeframe}/{strategy}] Skipped — "
                                    "no funding data available."
                                )
                                continue
                            signals_df = plugin["detector"](closed_df, funding_df)
                        else:
                            signals_df = plugin["detector"](closed_df)
                    except Exception:
                        logger.exception(
                            "Detector %s raised for %s/%s", strategy, symbol, timeframe
                        )
                        continue

                    if signals_df.empty:
                        continue

                    if direction_filter:
                        signals_df = signals_df[
                            signals_df["direction"] == direction_filter
                        ]
                        if signals_df.empty:
                            continue

                    row = signals_df.iloc[-1]
                    event = _build_event(
                        row, symbol, timeframe, strategy, closed_df, fallback_close
                    )
                    alert_text = format_confluence_alert(
                        [event], tp_r=tp_r, sl_pct=sl_pct, min_sl_pct=min_sl_pct
                    )

                    print(f"\n{'─' * 60}")
                    print(alert_text)
                    all_found.append((event.open_time, alert_text))
                    found_combos += 1

    print(f"\n{'─' * 60}")
    print(f"Found {found_combos} signal(s) across {total_combos} combo(s) checked.")

    if not all_found:
        return

    if send_telegram:
        # Send only the most recent signal to avoid flooding the chat.
        _, most_recent_text = max(all_found, key=lambda x: x[0])
        send_telegram_message(most_recent_text)
        print("[Telegram] Most recent signal sent.")
