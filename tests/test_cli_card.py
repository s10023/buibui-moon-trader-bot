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
