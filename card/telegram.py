"""Telegram message body for one card (ST30(a); layout ST30(c)).

Pure formatting: the CLI owns the send, so nothing here touches the network.

**The medium decides the layout, which is why this does not reuse `render_card`
verbatim.** The card carries two content types with opposite wrapping needs, and
one wrapper cannot serve both: the aligned numeric rows NEED `<pre>` (Telegram
collapses runs of spaces otherwise and the columns break), while the reasoning
prose is far WORSE inside one — `<pre>` does not soft-wrap, so a paragraph forces
horizontal scrolling in a small monospace font and buries the four numbers the
operator acts on. Measured on the first real send, 2026-08-18.

The wording still comes from the card's own fields rather than a second copy.
"""

from __future__ import annotations

import html
from datetime import datetime

from card.card import FinalCard
from card.render import _BANNERS
from signals.alert_formatter import DIRECTION_LABELS

# sendMessage rejects a longer body outright, so an unusually verbose card must
# lose reasoning rather than lose the whole message.
_LIMIT = 4096
_LABEL = 8


def _esc(text: str) -> str:
    # quote=False on purpose: Telegram decodes only &lt; &gt; &amp;, so an escaped
    # apostrophe renders LITERALLY as &#x27; on the phone. Quotes need escaping in
    # attributes; this is text content.
    return html.escape(text, quote=False)


def _short_utc(stamp: str) -> str:
    """`2026-08-22T12:34:00Z` -> `22 Aug 12:34 UTC`; unparseable stamps pass through.

    The card's own validator already rejects an unparseable `valid_until_utc`, so
    the fallback is for defence rather than an expected path.
    """
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp
    return f"{moment.day} {moment:%b %H:%M} UTC"


def _number_rows(final: FinalCard) -> list[str]:
    """The aligned block — prices, size, risk. Empty when there is no trade."""
    card = final.card
    rows: list[str] = []
    if card.verdict == "TRADE":
        # Values are printed as the card states them; rounding here would invent
        # precision the card never claimed.
        rr = f"   RR {final.rr_tp1:.2f}" if final.rr_tp1 is not None else ""
        if rr and final.rr_tp1_net is not None:
            rr = f"{rr} (net {final.rr_tp1_net:.2f})"
        rows.append(f"{'ENTRY':<{_LABEL}}{card.entry}")
        rows.append(f"{'STOP':<{_LABEL}}{card.sl}{rr}")
        for i, tp in enumerate((card.tp1, card.tp2, card.tp3), start=1):
            if tp is not None:
                rows.append(f"{f'TP{i}':<{_LABEL}}{tp}")
    if final.size_units is not None and final.notional_usd is not None:
        rows.append(
            f"{'SIZE':<{_LABEL}}{final.size_units} u  ${final.notional_usd:,.2f}"
        )
    if final.risk_usd is not None:
        regime = f"  {final.sizing_regime}" if final.sizing_regime else ""
        rows.append(
            f"{'RISK':<{_LABEL}}${final.risk_usd:,.2f}  "
            f"{(final.risk_frac or 0.0) * 100:.2f}%{regime}"
        )
    return rows


def _compose(final: FinalCard, bullets: int) -> str:
    card = final.card
    head = f"{_BANNERS[final.verdict]} · {final.symbol}"
    if card.direction:
        # Same badge the signal alerts use, imported rather than restated.
        head = f"{head} · {DIRECTION_LABELS.get(card.direction, card.direction)}"
    parts = [f"<b>{_esc(head)}</b>"]

    # Vetoes and the no-trade gate go first: they are the actionable line.
    parts.extend(f"⛔ {_esc(reason)}" for reason in final.veto_reasons)
    if final.verdict == "NO_TRADE" and card.no_trade_reason:
        parts.append(f"gate: {_esc(card.no_trade_reason)}")

    rows = _number_rows(final)
    if rows:
        parts.append(f"<pre>{_esc(chr(10).join(rows))}</pre>")

    meta = [f"Confluence {card.confluence_score}/9"]
    if card.expected_hold:
        meta.append(f"hold {card.expected_hold}")
    if card.valid_until_utc:
        meta.append(f"valid to {_short_utc(card.valid_until_utc)}")
    parts.append(_esc(" · ".join(meta)))
    parts.extend(f"⚠ {_esc(w)}" for w in final.warnings)

    kept = card.reasoning[:bullets]
    if kept:
        parts.append("<b>WHY</b>")
        parts.extend(f"• {_esc(b)}" for b in kept)
    dropped = len(card.reasoning) - len(kept)
    if dropped:
        parts.append(f"… {dropped} bullet(s) trimmed — full card in the ledger")

    if card.invalidation:
        parts.append(f"<b>INVALIDATION</b>\n{_esc(card.invalidation)}")

    cost = (
        f" · ${final.cost_usd_notional:.4f}"
        if final.cost_usd_notional is not None
        else ""
    )
    parts.append(
        f"<i>{_esc(final.model)} · {_esc(final.prompt_version)} · "
        f"{final.state_digest[:8]}{cost}</i>"
    )
    return "\n".join(parts)


def card_telegram_body(final: FinalCard, *, limit: int = _LIMIT) -> str:
    """Render one card for Telegram, dropping reasoning until it fits."""
    for bullets in range(len(final.card.reasoning), -1, -1):
        body = _compose(final, bullets)
        if len(body) <= limit:
            return body
    # Nothing left to drop and still over: a hard slice beats a rejected send,
    # and utils.telegram retries as plain text if the cut lands inside a tag.
    return _compose(final, 0)[:limit]
