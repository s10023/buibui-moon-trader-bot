"""Paper-portfolio sizing + replay (P1 spec 2026-06-05).

Replays the de-biased `signal_alert_outcomes` ledger through a Carver
two-layer sizing model into an overlapping-position paper book, producing the
system's first risk-adjusted numbers (Sharpe / Sortino / max-DD / attribution)
under policy #0 (today's exits). Pure libs over a DuckDB conn; no live risk.
"""

from portfolio.sizing import SizingConfig

__all__ = ["SizingConfig"]
