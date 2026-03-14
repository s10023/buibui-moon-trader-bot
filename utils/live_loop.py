"""Shared Rich live-render loop utility."""

import time
from collections.abc import Callable

from rich.console import Console, RenderableType
from rich.live import Live


def run_live_loop(
    render: Callable[[], RenderableType],
    interval: float = 1.0,
    refresh_per_second: float = 4.0,
) -> None:
    """Run a Rich Live loop until Ctrl-C, calling `render()` every `interval` seconds."""
    console = Console()
    try:
        with Live(console=console, refresh_per_second=refresh_per_second) as live:
            while True:
                live.update(render())
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        console.print("\nExiting gracefully. Goodbye!")
