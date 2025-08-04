# utils/rich_display.py
from rich.table import Table
from rich.live import Live
from rich.console import Console
from typing import List

console = Console()

def build_table(headers: List[str], rows: List[List[str]]) -> Table:
    table = Table(title="📈 Buibui Moon Bot — Live Prices", expand=True)
    for header in headers:
        table.add_column(header, justify="right")
    for row in rows:
        table.add_row(*row)
    return table

def live_render_loop(headers: List[str], get_rows_fn, interval: float = 5.0):
    with Live(build_table(headers, get_rows_fn()), refresh_per_second=1, screen=True) as live:
        while True:
            live.update(build_table(headers, get_rows_fn()))
            time.sleep(interval)
