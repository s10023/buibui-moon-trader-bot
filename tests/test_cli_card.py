"""CLI: parser registration, dry-run path, BinanceAccountProvider mapping."""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock

from cli.card import BinanceAccountProvider, add_card_subparser


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_card_subparser(subparsers)
    return parser.parse_args(argv)


class TestParser:
    def test_defaults(self) -> None:
        args = _parse(["card", "BTCUSDT"])
        assert args.symbol == "BTCUSDT"
        assert args.direction is None
        assert args.as_of is None
        assert args.dry_run is False
        assert args.no_ledger is False
        assert args.json is False
        assert callable(args.func)

    def test_flags(self) -> None:
        args = _parse(
            [
                "card",
                "ETHUSDT",
                "--direction",
                "short",
                "--as-of",
                "2026-07-11T00:00:00Z",
                "--dry-run",
                "--no-ledger",
                "--json",
            ]
        )
        assert args.direction == "short"
        assert args.dry_run is True


class TestBinanceAccountProvider:
    def _client(self) -> Any:
        client = MagicMock()
        client.futures_position_information.return_value = [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "-0.5",
                "entryPrice": "100.0",
                "markPrice": "99.0",
                "unRealizedProfit": "0.5",
            },
            {"symbol": "ETHUSDT", "positionAmt": "0"},
        ]
        client.futures_income_history.return_value = [
            {"incomeType": "REALIZED_PNL", "income": "-40.0"},
            {"incomeType": "COMMISSION", "income": "-1.5"},
            {"incomeType": "FUNDING_FEE", "income": "0.5"},
            {"incomeType": "TRANSFER", "income": "999.0"},  # excluded
        ]
        client.futures_account_balance.return_value = [
            {"asset": "BNB", "balance": "1.0"},
            {"asset": "USDT", "balance": "9000.0"},
        ]
        return client

    def test_positions_maps_nonzero_only(self) -> None:
        provider = BinanceAccountProvider(self._client())
        positions = provider.positions()
        assert len(positions) == 1
        assert positions[0].symbol == "BTCUSDT"
        assert positions[0].side == "short"
        assert positions[0].qty == 0.5

    def test_daily_pnl_filters_income_types(self) -> None:
        provider = BinanceAccountProvider(self._client())
        assert provider.daily_pnl_usd(0, 1) == -41.0

    def test_equity_reads_usdt(self) -> None:
        provider = BinanceAccountProvider(self._client())
        assert provider.equity_usd() == 9000.0

    def test_daily_pnl_paginates_past_1000_row_cap(self) -> None:
        # A high-churn day exceeds the 1000-row cap; a single unpaginated
        # fetch would undercount daily PnL and mis-fire the loss-limit gate.
        rows: list[dict[str, Any]] = [
            {
                "incomeType": "REALIZED_PNL",
                "income": "1.0",
                "time": i,
                "tranId": i,
            }
            for i in range(1, 2501)  # 2500 rows -> 3 pages
        ]

        class _PagingClient:
            def __init__(self) -> None:
                self.calls: list[tuple[int, int]] = []

            def futures_income_history(
                self, *, startTime: int, endTime: int, limit: int
            ) -> list[dict[str, Any]]:
                self.calls.append((startTime, endTime))
                window = [r for r in rows if startTime <= r["time"] <= endTime]
                return window[:limit]

        client = _PagingClient()
        provider = BinanceAccountProvider(client)
        # every row is +1.0 REALIZED_PNL -> the full 2500 must be summed once
        assert provider.daily_pnl_usd(0, 10_000) == 2500.0
        assert len(client.calls) >= 3  # actually paged, not one truncated fetch


class TestDryRun:
    def test_dry_run_prints_state_and_prompt_no_llm(
        self, capsys: Any, tmp_path: Any, monkeypatch: Any
    ) -> None:
        import duckdb

        from analytics.store.schema import init_schema
        from cli.card import run_card_cmd

        db = tmp_path / "t.db"
        conn = duckdb.connect(str(db))
        init_schema(conn)
        conn.close()
        args = argparse.Namespace(
            symbol="BTCUSDT",
            direction=None,
            as_of="2026-07-11T00:00:00Z",
            db=str(db),
            config=None,
            json=False,
            dry_run=True,
            no_ledger=False,
        )
        run_card_cmd(args)
        out = capsys.readouterr().out
        assert '"symbol": "BTCUSDT"' in out
        assert "MARKET STATE JSON" in out  # the prompt was printed


class TestCardErrorExit:
    def test_card_error_exits_nonzero_with_message(
        self, capsys: Any, tmp_path: Any, monkeypatch: Any
    ) -> None:
        import argparse

        import duckdb
        import pytest

        from analytics.store.schema import init_schema
        from card.errors import CardError
        from cli import card as card_mod

        db = tmp_path / "t.db"
        conn = duckdb.connect(str(db))
        init_schema(conn)
        conn.close()

        def _boom(*_a: Any, **_k: Any) -> Any:
            raise CardError("llm exploded")

        monkeypatch.setattr(card_mod, "generate_card", _boom)
        monkeypatch.setattr(card_mod, "_build_account_provider", lambda: None)

        args = argparse.Namespace(
            symbol="BTCUSDT",
            direction=None,
            as_of="2026-07-11T00:00:00Z",
            db=str(db),
            config=None,
            json=False,
            dry_run=False,
            no_ledger=True,
        )
        with pytest.raises(SystemExit) as exc_info:
            card_mod.run_card_cmd(args)
        assert exc_info.value.code == 1
        # a clean stderr message, not a raw traceback
        assert "llm exploded" in capsys.readouterr().err


class TestQtyStepWiring:
    """#559's lesson: a value plumbed everywhere except its consumer is not
    plumbed. The cap there was read by `hint` and discarded by the flow; this
    asserts the CLI actually hands qty_step to generate_card."""

    def test_cli_passes_fetched_qty_step_to_generate_card(
        self, tmp_path: Any, monkeypatch: Any
    ) -> None:
        import argparse

        import duckdb
        import pytest

        from analytics.store.schema import init_schema
        from card.errors import CardError
        from cli import card as card_mod

        db = tmp_path / "t.db"
        conn = duckdb.connect(str(db))
        init_schema(conn)
        conn.close()

        seen: dict[str, Any] = {}

        def _capture(*_a: Any, **kw: Any) -> Any:
            seen.update(kw)
            raise CardError("stop after capture")

        monkeypatch.setattr(card_mod, "generate_card", _capture)
        monkeypatch.setattr(card_mod, "_build_account_provider", lambda: None)
        monkeypatch.setattr(card_mod, "_fetch_qty_step", lambda _sym: 0.001)

        args = argparse.Namespace(
            symbol="BTCUSDT",
            direction=None,
            as_of="2026-07-11T00:00:00Z",
            db=str(db),
            config=None,
            json=False,
            dry_run=False,
            no_ledger=True,
        )
        with pytest.raises(SystemExit):
            card_mod.run_card_cmd(args)
        assert seen["qty_step"] == 0.001

    def test_fetch_qty_step_degrades_to_none_when_client_unavailable(
        self, monkeypatch: Any
    ) -> None:
        # Keys/network absent must degrade to None, never raise — the card
        # still renders, warned, rather than failing. No real network call:
        # create_client is replaced before _fetch_qty_step reaches it.
        import utils.binance_client as bc
        from cli.card import _fetch_qty_step

        def _no_client(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("no credentials")

        monkeypatch.setattr(bc, "create_client", _no_client)
        assert _fetch_qty_step("BTCUSDT") is None
