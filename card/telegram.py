"""Telegram message body for one card (ST30's OUT half).

Pure formatting: the CLI owns the send, so nothing here touches the network.
"""

from __future__ import annotations

import html

from card.card import FinalCard
from card.render import render_card


def card_telegram_body(final: FinalCard) -> str:
    """A bold headline over the rendered card, HTML-escaped, inside `<pre>`.

    Same shape as `deploy/run-job.sh`'s push, for two reasons that both bite:
    `utils.telegram` sends `parse_mode=HTML`, so an unescaped `<...>` in a
    reasoning bullet is read as an unclosed tag and rejected 400; and the card
    body is aligned ASCII, whose columns only survive inside `<pre>`.

    The headline is taken from the renderer's own first line rather than rebuilt
    here, so the card has one source of wording.
    """
    headline, _, body = render_card(final).partition("\n")
    head = f"<b>{html.escape(headline)}</b>"
    return f"{head}\n<pre>{html.escape(body)}</pre>" if body else head
