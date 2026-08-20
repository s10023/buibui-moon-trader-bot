"""Telegram OUT half of /card: the message body, and the CLI's opt-in wiring."""

from __future__ import annotations

import argparse
import json
from typing import Any

from card.card import FinalCard, parse_trade_card
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
        "steelman": ["htf zz1", "underweighted zz2", "catalyst zz3", "other zz4"],
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
    """The medium, not the terminal, decides the layout.

    Telegram gives `<pre>` no soft wrapping, so a prose paragraph inside one
    forces horizontal scrolling in a small monospace font. The card carries two
    content types with opposite needs — aligned numbers that require `<pre>`,
    and reasoning prose that is unreadable inside it — so the body splits them.
    """

    def test_phone_card_omits_the_steelman(self) -> None:
        """card-v5 deliberately keeps the steelman OFF the phone.

        ST30(c) was the operator finding the four numbers they act on buried
        under prose; four more argument bullets against a 4096-char guard
        re-opens exactly that. The steelman lives in the terminal render and
        `ai-cards.jsonl`. This is a decision, so it is pinned as a test
        rather than left for the next edit to quietly reverse.
        """
        body = card_telegram_body(_final("TRADE"))
        assert "steelman" not in body.lower()
        for angle in ("zz1", "zz2", "zz3", "zz4"):
            assert angle not in body

    def test_headline_carries_verdict_symbol_and_direction(self) -> None:
        body = card_telegram_body(_final("TRADE"))
        headline = body.split("\n", 1)[0]
        assert headline.startswith("<b>") and headline.endswith("</b>")
        assert "TRADE" in headline
        assert "BTCUSDT" in headline
        # the badge is imported from the signal alerts, not restated here, so
        # the two operator-facing renderers cannot drift apart
        assert "LONG 🟢" in headline

    def test_short_carries_the_red_badge(self) -> None:
        body = card_telegram_body(
            _final("TRADE", direction="short", sl=102.0, tp1=97.0, tp2=95.0, tp3=92.0)
        )
        assert "SHORT 🔴" in body.split("\n", 1)[0]

    def test_numbers_are_inside_the_pre_block(self) -> None:
        body = card_telegram_body(_final("TRADE"))
        pre = body[body.index("<pre>") : body.index("</pre>")]
        assert "ENTRY" in pre and "STOP" in pre
        for token in ("100.0", "98.0", "103.0", "12.5"):
            assert token in pre, token

    def test_reasoning_prose_is_outside_every_pre_block(self) -> None:
        # The whole point of the layout: prose must be free to soft-wrap.
        final = _final("TRADE")
        body = card_telegram_body(final)
        pre_blocks = []
        rest = body
        while "<pre>" in rest:
            head = rest.index("<pre>")
            tail = rest.index("</pre>")
            pre_blocks.append(rest[head:tail])
            rest = rest[tail + len("</pre>") :]
        for bullet in final.card.reasoning:
            assert bullet in body
            for block in pre_blocks:
                assert bullet not in block

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

    def test_quotes_are_left_alone(self) -> None:
        # Telegram decodes only &lt; &gt; &amp;. An escaped apostrophe would
        # render literally as &#x27; on the phone, and quotes need escaping in
        # attributes, not in text content.
        body = card_telegram_body(
            _final("TRADE", reasoning=["regime is 'range'", "b 2", "c 3", "d 4", "e 5"])
        )
        assert "'range'" in body
        assert "&#x27;" not in body

    def test_vetoed_card_carries_its_veto_reason(self) -> None:
        # A veto is how the daily breaker trip and the sub-lot capital wall
        # become visible on the phone, so VETOED must push like any other.
        body = card_telegram_body(_final("VETOED"))
        assert "VETOED" in body
        assert "SL must be below entry for a long" in body

    def test_no_trade_card_carries_the_gate_reason_and_no_price_block(self) -> None:
        body = card_telegram_body(_final("NO_TRADE"))
        assert "regime conflict" in body
        assert "ENTRY" not in body

    def test_an_over_long_card_is_trimmed_under_the_telegram_limit(self) -> None:
        # Telegram rejects a body over 4096 chars outright, so an unusually
        # verbose card must lose reasoning rather than the whole message.
        body = card_telegram_body(
            _final("TRADE", reasoning=["x" * 1200 for _ in range(5)])
        )
        assert len(body) <= 4096
        assert "trimmed" in body

    def test_valid_until_is_shortened_for_reading(self) -> None:
        body = card_telegram_body(_final("TRADE"))
        assert "11 Jul 12:00 UTC" in body
        assert "2026-07-11T12:00:00Z" not in body


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
