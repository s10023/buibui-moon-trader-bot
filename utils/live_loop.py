"""Shared Rich live-render loop utility."""

import logging
import time
from collections.abc import Callable

from rich.console import Console, RenderableType
from rich.live import Live


def run_live_loop(
    render: Callable[[], RenderableType],
    interval: float = 1.0,
) -> None:
    """Run a Rich Live loop until Ctrl-C, calling `render()` every `interval` seconds."""
    console = Console()
    # Suppress INFO-level console logging during Live rendering.
    # Any log write to the terminal inside a Live context corrupts Rich's
    # cursor-position tracking, causing panels to stack instead of refresh.
    root_logger = logging.getLogger()
    saved_level = root_logger.level
    root_logger.setLevel(logging.WARNING)
    try:
        # auto_refresh=False disables the background refresh thread.
        # Without it, that thread races with live.update() from a slow render()
        # call and writes to the terminal at the same time, causing a duplicate
        # panel header on screen. We drive every repaint manually with refresh().
        with Live(console=console, auto_refresh=False) as live:
            while True:
                live.update(render())
                live.refresh()
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        root_logger.setLevel(saved_level)
        console.print("\nExiting gracefully. Goodbye!")
