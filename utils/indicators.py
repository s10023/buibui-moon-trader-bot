from typing import Any, Optional, Tuple, List
import pandas as pd
import pandas_ta as ta
import logging


def fetch_klines_df(
    client: Any, symbol: str, interval: str, limit: int = 150
) -> Optional[pd.DataFrame]:
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        if not klines:
            return None
        df = pd.DataFrame(klines)
        df.columns = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_asset_volume",
            "number_of_trades",
            "taker_buy_base_asset_volume",
            "taker_buy_quote_asset_volume",
            "ignore",
        ]
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        return df
    except Exception as e:
        logging.error(f"Error fetching klines for {symbol} [{interval}]: {e}")
        return None


def calculate_indicators(
    df: Optional[pd.DataFrame],
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if df is None or df.empty:
        return None, None, None
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    if isinstance(macd, pd.DataFrame) and not macd.empty:
        df = pd.concat([df, macd], axis=1)
    rsi_isnull = bool(df["rsi"].isnull().all())
    latest_rsi = df["rsi"].iloc[-1] if not rsi_isnull else None
    latest_macd = df["MACD_12_26_9"].iloc[-1] if "MACD_12_26_9" in df else None
    latest_macdsignal = df["MACDs_12_26_9"].iloc[-1] if "MACDs_12_26_9" in df else None
    return latest_rsi, latest_macd, latest_macdsignal


def get_alerts(
    rsi: Optional[float],
    macd: Optional[float],
    macdsignal: Optional[float],
    tf_label: str,
) -> List[str]:
    alerts = []
    if rsi is not None:
        if rsi > 70:
            alerts.append(f"{tf_label} RSI Overbought")
        elif rsi < 30:
            alerts.append(f"{tf_label} RSI Oversold")
    if macd is not None and macdsignal is not None:
        if macd > macdsignal:
            alerts.append(f"{tf_label} MACD Bullish")
        elif macd < macdsignal:
            alerts.append(f"{tf_label} MACD Bearish")
    return alerts
