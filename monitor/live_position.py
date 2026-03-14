"""Live position monitor: polling + Rich terminal."""

import time
from typing import Any

from rich.panel import Panel
from rich.text import Text

from monitor.position_lib import display_table
from utils.live_loop import run_live_loop

REFRESH_INTERVAL = 5  # seconds


def _render(
    client: Any,
    coins_config: dict[str, Any],
    coin_order: list[str],
    wallet_target: float,
    sort_by: str,
    descending: bool,
    hide_empty: bool,
    compact: bool,
) -> Panel:
    """Fetch and format positions, wrapping in a Rich Panel."""
    try:
        output = display_table(
            client,
            coins_config,
            coin_order,
            wallet_target,
            sort_by=sort_by,
            descending=descending,
            telegram=False,
            hide_empty=hide_empty,
            compact=compact,
        )
    except Exception as e:
        output = f"\nError fetching positions: {e}"
    ts = time.strftime("%H:%M:%S")
    return Panel(
        Text.from_ansi(output),
        title=f"[bold]\U0001f4c8 Live Position Monitor \u2014 Buibui Moon Bot[/bold]  |  Last update: {ts}",
        border_style="blue",
    )


def run(
    client: Any,
    coins_config: dict[str, Any],
    coin_order: list[str],
    wallet_target: float,
    sort_by: str = "default",
    descending: bool = True,
    hide_empty: bool = False,
    compact: bool = False,
    interval: int = REFRESH_INTERVAL,
) -> None:
    """Run live position monitor, polling every `interval` seconds until Ctrl-C."""
    run_live_loop(
        lambda: _render(
            client,
            coins_config,
            coin_order,
            wallet_target,
            sort_by,
            descending,
            hide_empty,
            compact,
        ),
        interval=float(interval),
        refresh_per_second=1.0,
    )
