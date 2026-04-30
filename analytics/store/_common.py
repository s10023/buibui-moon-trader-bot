"""Shared internals for the analytics.store package.

`_upsert` body is **sealed** — the explicit register/unregister in try/finally
prevents a DuckDB heap-corruption bug that the implicit replacement scan
(FROM df) triggers. Do not reformat or refactor that block.
"""

from pathlib import Path

import duckdb
import pandas as pd

DEFAULT_DB_PATH: Path = Path("analytics.db")


def _upsert(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    table: str,
    columns: str,
) -> None:
    if df.empty:
        return
    # register/unregister in try/finally: DuckDB increments refcount on register and
    # decrements on unregister, giving safe bulk-scan performance without the stale
    # C-pointer heap corruption that the implicit replacement scan (FROM df) causes.
    conn.register("_upsert_df", df)
    try:
        conn.execute(f"INSERT OR REPLACE INTO {table} SELECT {columns} FROM _upsert_df")
    finally:
        conn.unregister("_upsert_df")
