"""Universe selection tool (N3) — makes the committed criterion executable.

Criterion: top-N USDT-M perpetuals by 30-day median daily quote volume;
status TRADING; stablecoin bases excluded; listed >= min-age. Prints a
ready-to-paste [universe] TOML block — it NEVER writes config/universe.toml
itself; a universe refresh stays a deliberate, reviewed commit.

Read-only / keyless (public fapi endpoints). Run via:
    PYTHONPATH=. poetry run python tools/select_universe.py [--top-n 25] [--min-age-days 365]
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

_FAPI = "https://fapi.binance.com"
_DAY_MS = 86_400_000
_CANDIDATE_POOL = 60  # pre-rank by 24h volume, re-rank this many by 30d median

STABLE_BASES: set[str] = {
    "USDC",
    "FDUSD",
    "TUSD",
    "DAI",
    "BUSD",
    "EURI",
    "USDP",
    "AEUR",
    "USD1",
    "USDE",
    "BFUSD",
    "XUSD",
}


def eligible_perps(
    exchange_info: dict[str, Any], *, as_of_ms: int, min_age_days: int
) -> list[str]:
    """Symbols passing the static filters: USDT perp, TRADING, non-stable, aged."""
    out: list[str] = []
    for s in exchange_info.get("symbols", []):
        if s.get("quoteAsset") != "USDT" or s.get("contractType") != "PERPETUAL":
            continue
        if s.get("status") != "TRADING":
            continue
        if str(s.get("baseAsset", "")).upper() in STABLE_BASES:
            continue
        onboard = int(s.get("onboardDate", 0))
        if as_of_ms - onboard < min_age_days * _DAY_MS:
            continue
        out.append(str(s["symbol"]))
    return out


def rank_by_median_volume(
    daily_quote_volumes: dict[str, list[float]], top_n: int
) -> list[str]:
    """Rank symbols by median daily quote volume, descending; truncate to top_n."""
    ranked = sorted(
        daily_quote_volumes,
        key=lambda s: (
            statistics.median(daily_quote_volumes[s]) if daily_quote_volumes[s] else 0.0
        ),
        reverse=True,
    )
    return ranked[:top_n]


def format_universe_toml(
    symbols: list[str], *, selected_at: str, criterion: str
) -> str:
    """Render the [universe] block ready to paste into config/universe.toml."""
    lines = [
        "[universe]",
        f'selected_at = "{selected_at}"',
        f'criterion = "{criterion}"',
        "symbols = [",
        *[f'  "{s}",' for s in symbols],
        "]",
    ]
    return "\n".join(lines) + "\n"


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=25)
    parser.add_argument("--min-age-days", type=int, default=365)
    args = parser.parse_args()

    as_of_ms = int(time.time() * 1000)
    info = _get_json(f"{_FAPI}/fapi/v1/exchangeInfo")
    tickers = _get_json(f"{_FAPI}/fapi/v1/ticker/24hr")
    eligible = set(
        eligible_perps(info, as_of_ms=as_of_ms, min_age_days=args.min_age_days)
    )
    by_24h = sorted(
        (t for t in tickers if t["symbol"] in eligible),
        key=lambda t: float(t["quoteVolume"]),
        reverse=True,
    )[:_CANDIDATE_POOL]

    vols: dict[str, list[float]] = {}
    for t in by_24h:
        sym = str(t["symbol"])
        kl = _get_json(f"{_FAPI}/fapi/v1/klines?symbol={sym}&interval=1d&limit=31")
        vols[sym] = [float(k[7]) for k in kl[:-1]]  # k[7] = quote vol; drop partial day
        time.sleep(0.15)

    winners = rank_by_median_volume(vols, top_n=args.top_n)
    criterion = (
        f"top-{args.top_n} USDT-M perpetuals by 30d median daily quote volume; "
        f"status TRADING; stablecoin bases excluded; "
        f"listed >= {args.min_age_days}d (Binance fapi snapshot)"
    )
    print(
        format_universe_toml(
            winners,
            selected_at=datetime.now(UTC).date().isoformat(),
            criterion=criterion,
        )
    )


if __name__ == "__main__":
    main()
