"""Scale-free taker-flow imbalance and the spot-perp divergence.

``imb = (2*taker_buy_volume - volume) / volume`` in [-1, +1]: +1 = every taker
was a buyer, -1 = every taker was a seller, 0 = balanced. Being a ratio is what
makes the rest simple — no cross-symbol volume normalisation, no cross-era
normalisation as volumes grow, and the 1000PEPE multiplier cancels.

Daily bars are information-complete here: the day's taker-buy field IS the exact
intraday sum, so ``sum_i(2*tbv_i - vol_i) == 2*tbv_day - vol_day``. See the spec
section 3 and ``test_daily_bars_are_information_complete_for_daily_cvd``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def taker_imbalance(volume: pd.Series, taker_buy_volume: pd.Series) -> pd.Series:
    """Signed taker-flow share in [-1, +1]. Zero-volume days are NaN."""
    vol = volume.astype(float)
    tbv = taker_buy_volume.astype(float)
    out = (2.0 * tbv - vol) / vol.where(vol != 0.0)
    return out.replace([np.inf, -np.inf], np.nan)


def divergence(spot: pd.DataFrame, perp: pd.DataFrame) -> pd.Series:
    """``imb_spot - imb_perp`` on the days both venues have a bar.

    Positive = spot takers were more aggressive buyers than perp takers, which
    the spec pre-registers as the LONG direction. The index is built with the
    same normalize -> dedup(keep last) -> sort idiom as ``load_daily_inputs``
    (``analytics/forecast/replay.py``), so the two Series are guaranteed
    UTC-midnight-aligned by construction rather than by an assumption that
    every venue's ``open_time`` happens to already be midnight-aligned.
    """
    cols = ["open_time", "volume", "taker_buy_volume"]
    merged = spot[cols].merge(perp[cols], on="open_time", suffixes=("_s", "_p"))
    if merged.empty:
        return pd.Series(dtype="float64", index=pd.DatetimeIndex([], tz="UTC"))
    imb_s = taker_imbalance(merged["volume_s"], merged["taker_buy_volume_s"])
    imb_p = taker_imbalance(merged["volume_p"], merged["taker_buy_volume_p"])
    idx = pd.to_datetime(merged["open_time"], unit="ms", utc=True).dt.normalize()
    out = pd.Series((imb_s - imb_p).to_numpy(), index=pd.DatetimeIndex(idx))
    return out[~out.index.duplicated(keep="last")].sort_index()
