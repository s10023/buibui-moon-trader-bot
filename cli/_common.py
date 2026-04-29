"""Shared CLI helpers used by multiple subcommand modules."""

from __future__ import annotations

import argparse
import datetime


def parse_since_to_ms(since: str) -> int:
    """Parse ISO date string 'YYYY-MM-DD' to Unix milliseconds."""
    d = datetime.datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
    return int(d.timestamp() * 1000)


def parse_smt_pairs(value: str) -> dict[str, str]:
    """Parse comma-separated PRIMARY:SECONDARY tokens into a dict.

    Example: 'BTCUSDT:ETHUSDT,ETHUSDT:BTCUSDT' → {'BTCUSDT': 'ETHUSDT', 'ETHUSDT': 'BTCUSDT'}
    """
    result: dict[str, str] = {}
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        parts = token.split(":", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise argparse.ArgumentTypeError(
                f"Invalid smt-pairs token '{token}' — expected PRIMARY:SECONDARY"
            )
        result[parts[0].strip()] = parts[1].strip()
    return result
