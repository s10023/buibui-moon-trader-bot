"""Telegram OUT half of /card: the message body, and the CLI's opt-in wiring."""

from __future__ import annotations

import argparse
import json
from typing import Any

from card.card import FinalCard, parse_trade_card
from card.render import render_card
from card.telegram import card_telegram_body


def _final(verdict: str, **card_overrides: Any) -> FinalCard:
    obj: dict[str, Any] = {
        "verdict": "TRADE" if verdict != "NO_TRADE" else "NO_TRADE",
        "direction": "long" if verdict != "NO_TRADE" else None,
        "entry": 100.0 if verdict != "NO_TRADE" else None,
        "sl": 98.0 if verdict != "NO_TRADE" else None,
        "tp1": 103.0 if verdict != "NO_TRADE" else None,
        "tp2": 105.0 if verdict != "NO_TRADE" else None,
        "tp3": 108.0 if verdict != "NO_TRADE" else None,
        "confluence_score": 6,
        "reasoning": ["a 1", "b 2", "c 3", "d 4", "e 5"],
        "invalidation": "close below 97",
        "expected_hold": "12h",
        "valid_until_utc": "2026-07-11T12:00:00Z",
        "no_trade_reason": None if verdict != "NO_TRADE" else "regime conflict",
    }
    obj.update(card_overrides)
    return FinalCard(
        symbol="BTCUSDT",
        as_of_ms=1_760_000_000_000,
        verdict=verdict,
        card=parse_trade_card(json.dumps(obj)),
        size_units=12.5 if verdict == "TRADE" else None,
        notional_usd=1250.0 if verdict == "TRADE" else None,
        risk_usd=25.0 if verdict == "TRADE" else None,
        risk_frac=0.0025 if verdict == "TRADE" else None,
        capital_used=10_000.0 if verdict == "TRADE" else None,
        capital_source="config" if verdict == "TRADE" else None,
        rr_tp1=1.5 if verdict != "NO_TRADE" else None,
        warnings=[],
        veto_reasons=["SL must be below entry for a long"]
        if verdict == "VETOED"
        else [],
        state_digest="d" * 64,
        prompt_version="card-v4",
        model="sonnet",
        generated_at_ms=1,
        cost_usd_notional=0.0123,
        horizon="intraday",
    )


class TestBody:
    def test_headline_is_bold_and_sits_outside_the_pre_block(self) -> None:
        body = card_telegram_body(_final("TRADE"))
        headline = body.split("\n", 1)[0]
        assert headline.startswith("<b>")
        assert headline.endswith("</b>")
        assert "BTCUSDT" in headline
        assert "TRADE" in headline
        assert "<pre>" not in headline

    def test_card_body_is_wrapped_in_pre(self) -> None:
        # The card renders as aligned ASCII. Without <pre>, Telegram collapses
        # the runs of spaces and the columns stop lining up on the phone.
        body = card_telegram_body(_final("TRADE"))
        assert "<pre>" in body
        assert body.endswith("</pre>")
        assert "entry 100.0" in body
        assert "confluence 6/9" in body

    def test_html_special_characters_are_escaped(self) -> None:
        # utils.telegram sends parse_mode=HTML, and an unescaped `<...>` is
        # read as an unclosed tag and REJECTED 400. That is the defect that
        # made the daily-check alert fail on exactly the tracebacks it exists
        # to report (2026-08-07), so escaping here is the whole point.
        body = card_telegram_body(
            _final(
                "TRADE",
                reasoning=["fails at <module> & retries", "b 2", "c 3", "d 4", "e 5"],
            )
        )
        assert "&lt;module&gt;" in body
        assert "&amp;" in body
        assert "<module>" not in body

    def test_vetoed_card_carries_its_veto_reason(self) -> None:
        # A veto is how the daily breaker trip and the sub-lot capital wall
        # become visible on the phone, so VETOED must push like any other.
        body = card_telegram_body(_final("VETOED"))
        assert "VETOED" in body
        assert "SL must be below entry for a long" in body

    def test_every_rendered_line_survives_into_the_message(self) -> None:
        final = _final("NO_TRADE")
        body = card_telegram_body(final)
        for line in render_card(final).splitlines():
            assert line in body


def _cli_args(db: str, **overrides: Any) -> argparse.Namespace:
    args = argparse.Namespace(
        symbol="BTCUSDT",
        direction=None,
        horizon=None,
        as_of="2026-07-11T00:00:00Z",
        db=db,
        config=None,
        json=False,
        dry_run=False,
        no_ledger=True,
        telegram=False,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _stub_cli(monkeypatch: Any, tmp_path: Any) -> tuple[Any, list[str], str]:
    """A run_card_cmd that reaches its end with no LLM call and no network."""
    import duckdb

    from analytics.store.schema import init_schema
    from cli import card as card_mod

    db = tmp_path / "t.db"
    conn = duckdb.connect(str(db))
    init_schema(conn)
    conn.close()

    final = _final("TRADE")
    sent: list[str] = []
    monkeypatch.setattr(card_mod, "generate_card", lambda *_a, **_k: final)
    monkeypatch.setattr(card_mod, "_build_account_provider", lambda: None)
    monkeypatch.setattr(card_mod, "_fetch_qty_step", lambda _sym: None)
    monkeypatch.setattr(
        card_mod, "send_telegram_message", lambda text: sent.append(text)
    )
    return card_mod, sent, str(db)


class TestCliWiring:
    def test_no_flag_sends_nothing(self, monkeypatch: Any, tmp_path: Any) -> None:
        card_mod, sent, db = _stub_cli(monkeypatch, tmp_path)
        card_mod.run_card_cmd(_cli_args(db))
        assert sent == []

    def test_flag_sends_the_rendered_card(
        self, monkeypatch: Any, tmp_path: Any
    ) -> None:
        card_mod, sent, db = _stub_cli(monkeypatch, tmp_path)
        card_mod.run_card_cmd(_cli_args(db, telegram=True))
        assert sent == [card_telegram_body(_final("TRADE"))]
