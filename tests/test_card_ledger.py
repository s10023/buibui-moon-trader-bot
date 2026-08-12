"""Ledger: ai-cards round-trip, TRADE-only dual-write, scorer compatibility."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from card.card import FinalCard, parse_trade_card
from card.config import CardConfig
from card.ledger import append_ledgers, pundit_row


def _final(verdict: str = "TRADE") -> FinalCard:
    obj: dict[str, Any] = {
        "verdict": "TRADE",
        "direction": "long",
        "entry": 100.0,
        "sl": 98.0,
        "tp1": 103.0,
        "tp2": 105.0,
        "tp3": 108.0,
        "confluence_score": 6,
        "reasoning": ["ref_close 100 above POC 99", "b", "c", "d", "e"],
        "invalidation": "close below 97",
        "expected_hold": "12h",
        "valid_until_utc": "2026-07-11T12:00:00Z",
        "no_trade_reason": None,
    }
    return FinalCard(
        symbol="BTCUSDT",
        as_of_ms=1_760_000_000_000,
        verdict=verdict,
        card=parse_trade_card(json.dumps(obj)),
        size_units=12.5,
        notional_usd=1250.0,
        risk_usd=25.0,
        risk_frac=0.0025,
        capital_used=10_000.0,
        capital_source="config",
        rr_tp1=1.5,
        warnings=[],
        veto_reasons=[] if verdict != "VETOED" else ["x"],
        state_digest="d" * 64,
        prompt_version="card-v1",
        model="sonnet",
        generated_at_ms=1_760_000_100_000,
        cost_usd_notional=0.01,
    )


def _cfg(tmp_path: Path, horizon: str = "intraday") -> CardConfig:
    return CardConfig(
        cards_path=str(tmp_path / "ai-cards.jsonl"),
        pundit_calls_path=str(tmp_path / "pundit-calls.jsonl"),
        horizon=horizon,
    )


class TestLedger:
    def test_trade_dual_writes(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        paths = append_ledgers(_final("TRADE"), cfg)
        assert len(paths) == 2
        cards = Path(cfg.cards_path).read_text().strip().splitlines()
        assert len(cards) == 1
        row = json.loads(cards[0])
        assert row["symbol"] == "BTCUSDT"
        assert row["state_digest"] == "d" * 64
        assert row["card"]["entry"] == 100.0

    def test_no_trade_and_vetoed_write_cards_only(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        assert len(append_ledgers(_final("NO_TRADE"), cfg)) == 1
        assert len(append_ledgers(_final("VETOED"), cfg)) == 1
        assert not Path(cfg.pundit_calls_path).exists()

    def test_pundit_row_parses_through_scorer_loader(self, tmp_path: Path) -> None:
        from tools.pundit_score import load_ledger

        cfg = _cfg(tmp_path)
        append_ledgers(_final("TRADE"), cfg)
        calls, warnings = load_ledger(Path(cfg.pundit_calls_path))
        assert warnings == []
        assert len(calls) == 1
        call = calls[0]
        assert call.source == "ai-card"
        assert call.author == "buibui_card"
        assert call.symbol == "BTCUSDT"
        assert call.direction == "long"
        assert call.horizon == "intraday"  # confirmed WINDOWS_MS key
        assert call.url.startswith("ai-card://")
        assert call.entry == "100.0"
        assert call.stop == "98.0"
        assert call.target == "103.0"

    def test_swing_card_is_scored_on_the_swing_window(self, tmp_path: Path) -> None:
        """The whole point of ST12: a swing card must not be scored on 48h.

        Before the flag, `pundit_row` hardcoded "intraday", so a card that
        reasoned at swing pace was resolved against a 48-hour window and
        silently booked wrong. Assert the written key AND that the scorer
        resolves it to the 30-day window, so this cannot pass on a key the
        scorer does not honour.
        """
        from tools.pundit_score import WINDOWS_MS, load_ledger, window_ms

        cfg = _cfg(tmp_path, horizon="swing")
        append_ledgers(_final("TRADE"), cfg)
        calls, warnings = load_ledger(Path(cfg.pundit_calls_path))
        assert warnings == []
        assert calls[0].horizon == "swing"
        assert window_ms(calls[0].horizon) == WINDOWS_MS["swing"]
        assert window_ms(calls[0].horizon) != WINDOWS_MS["intraday"]

    def test_intraday_remains_the_default(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        append_ledgers(_final("TRADE"), cfg)
        row = json.loads(Path(cfg.pundit_calls_path).read_text().strip())
        assert row["horizon"] == "intraday"

    def test_url_unique_per_generation(self, tmp_path: Path) -> None:
        a = pundit_row(_final("TRADE"), "intraday")
        b = dict(a)
        assert a["url"] == "ai-card://1760000100000-BTCUSDT"
        assert a["raw_quote"] == "ref_close 100 above POC 99"
        assert b["call_ts_utc"].endswith("Z")

    def test_url_differs_across_generations(self) -> None:
        import dataclasses

        a = pundit_row(_final("TRADE"), "intraday")
        b = pundit_row(
            dataclasses.replace(_final("TRADE"), generated_at_ms=1_760_000_200_000),
            "intraday",
        )
        assert a["url"] != b["url"]  # generated_at_ms makes each call distinct
        assert b["url"] == "ai-card://1760000200000-BTCUSDT"
