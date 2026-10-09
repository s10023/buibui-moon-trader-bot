---
status: accepted
---

# Direction per book after the #864 map

Decided 2026-10-09 in #922, closing the #864 map ("define the trading pillars, then find an edge or
the direction to build"). The evidence is in `docs/research/2026-10-0[7-9]-*.md`. Each part
names the observable that would reverse it.

- **Both books: codify #915's sizing rule before any live size.** No live size is legitimate
  without it (landed as #980 in #987). Reversed only by a replacement rule carrying its own
  evidence, never by a one-off size.
- **Manual book: build an exit manager, measured on commission.** Commission, not exits, is the
  leak that measures cleanly (41% of the lifetime net loss, all taker; exits are contested,
  `2026-10-08-pillar-gaps.md`), so v1 (#981) rests the stop and one reduce-only maker TP1 partial
  on a detected fill. Reversed if, over the next 30 journaled episodes, the maker share of exit
  fills does not rise or fee R per trade does not fall against #916's 28 canonical-basis
  episodes. Trailing stops and re-entry are new constructions, each with its own
  pre-registration.
- **Bot book: round 2 on new data, run as plain Issues.** The cheap price-only levers are
  exhausted (`2026-10-07-tested-register.md`), so round 2 draws on data the repo did not hold:
  E850 / H18 on the #936 OI archive in slot 1 (#850), slots 2-3 in #983, and liquidations
  recorded forward (#984). Reversed when every round-2 slot fails the three-leg gate, which
  re-opens #913's "buy no data" ruling.
- **The 20-strategy signal book is FROZEN.** It keeps running as advisory alerts and as the live
  out-of-sample ledger; upkeep (such as #970 and #971) still lands, and any other change enters
  only as a pre-registered candidate. Reversed by a pre-registered candidate on the signal book
  that clears the gate.
- **XS stays dry-run** by the operator's call, with the drawdown-halt ruling still owed so the
  dry-run reads cleanly. Reversed by that ruling plus the live prerequisites (a dedicated
  sub-account among them).
