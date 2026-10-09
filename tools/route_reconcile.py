"""Reconcile a round's DECLARED routes against what actually landed in the sinks.

`/ingest-x` records a route per item as it goes ("after each successful append, record
it"). That is per-item and unverified in aggregate, and the gap is not theoretical: on
the 2026-08-25 round **19 Stream C setups were declared routed and never written**.
Streams A and B landed, the A/B `mark` calls ran at 11:34:14-16, the notes were written
at 11:35:43, and Stream C's append simply never ran. Nobody noticed for two days.

`route_dedup seed` could not have repaired it either — seed reconstructs FROM the sink,
and the sink was empty. The notes were the only copy.

⚠ **A note's `route:` line is a DECLARATION, and a ledger mark is a SECOND declaration.**
Only the sink itself is evidence. That is why `unperformed` outranks a present mark here
rather than being softened by it — a marked-but-unwritten row is the one state no
downstream tool can detect or repair.

Scope, stated because a silent gap here would read as an all-clear: this reconciles what
a note DECLARES. A note with no `route:` key reports `undeclared`, never `ok` — today
that is every `/ingest-video` and `/ingest-feed` note, whose routing lives in prose.

Run it at the end of a round:

    PYTHONPATH=. python3 tools/route_reconcile.py docs/plans/x-notes/2026-08-26-*.md

Exit 1 on any finding, so a skill's halt is a MECHANISM rather than a prose rule.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A bare `python3 tools/route_reconcile.py` puts `tools/` on the path, not the repo
# root, so the `tools.route_dedup` import below would die. `test_bare_invocation_works`
# is the guarantee; this line is only how it is met.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.route_dedup import (  # noqa: E402
    KNOWN_SINKS,
    MECHANICS_SINK,
    PUNDIT_SINK,
    THESIS_SINK,
    RoutedItem,
    load_ledger,
    parse_source_id,
)

DROPPED = "dropped"

# Verdicts that mean the round is intact. Everything else is a finding, including the
# two "we could not tell" states — a SKIP is not a PASS.
CLEAN_VERDICTS = frozenset({"ok", DROPPED, "cited"})

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
_ROUTING_SECTION = re.compile(
    r"^#{1,6}\s*Routing decision\s*$(.*?)(?=^#{1,6}\s|\Z)",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)


@dataclass(frozen=True)
class Declaration:
    """What one note SAYS happened to its item."""

    note: str
    source_id: str
    route: str


@dataclass(frozen=True)
class Finding:
    note: str
    source_id: str
    route: str
    verdict: str
    detail: str


def _frontmatter(text: str) -> dict[str, str]:
    """Scalar top-level keys of a note's YAML frontmatter.

    Deliberately not a YAML parse: notes carry nested blocks (`text_source:`) whose
    values are irrelevant here, and a hand-edited note that trips a strict parser must
    still be reconcilable — refusing to read it would hide exactly the rounds most
    likely to have gone wrong.
    """
    match = _FRONTMATTER.search(text)
    if not match:
        return {}
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line[:1] in (" ", "\t", "#"):
            continue  # nested value, or a comment
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip().strip("\"'")
    return out


def _prose_route(text: str) -> str:
    """The route declared by a `## Routing decision` section, or `""`.

    The 2026-08-26 round wrote no frontmatter `route:` at all — 11 of its 18 notes — so a
    frontmatter-only reader would call a correctly-routed round undeclared.

    ⚠ **The leading VERDICT decides, never a path found in the line.** An operator
    override reads `NOT WRITTEN. route_target(setup, NOVEL) returns
    docs/plans/pundit-calls.jsonl, but the operator approved dropping it` — matching on
    the path would turn every such drop into a spurious `unperformed`.

    An unrecognised opener stays `""`. Silence is not a drop: a new prose form must reach
    a human rather than be guessed at.
    """
    match = _ROUTING_SECTION.search(text)
    if not match:
        return ""
    for line in match.group(1).splitlines():
        line = line.strip().lstrip("*_ ")
        if not line:
            continue
        if line.upper().startswith(("DROPPED", "NOT WRITTEN", "NOT ROUTED")):
            return DROPPED
        if line.upper().startswith("ROUTED"):
            arrow = re.search(r"->\s*(\S+)", line)
            return arrow.group(1).rstrip(".,;:)") if arrow else ""
        return ""  # first substantive line wins, whatever it says
    return ""


def parse_declaration(note: str, text: str) -> Declaration:
    """One note's declaration. Frontmatter `route:` wins; prose is the fallback."""
    front = _frontmatter(text)
    source_id = (
        front.get("status_id")
        or front.get("video_id")
        or parse_source_id(front.get("url", ""))
        or ""
    )
    route = front.get("route", "") or _prose_route(text)
    return Declaration(note=note, source_id=source_id, route=route)


def _stream_c_sources(text: str) -> set[str]:
    """The source id of every Stream C row, read from the row's OWN `url`.

    Exact, unlike the prose sinks: one JSONL line is one item and carries its own url,
    so a row merely QUOTING another post's id cannot be mistaken for that post landing.
    """
    out: set[str] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a hand-edited line must not abort a reconciliation
        if isinstance(row, dict):
            found = parse_source_id(str(row.get("url") or ""))
            if found:
                out.add(found)
    return out


def _landed_in(
    source_id: str, sink_texts: dict[str, str]
) -> tuple[list[str], list[str]]:
    """`(routed_sinks, cited_sinks)` for `source_id`.

    The two are separated because the sinks differ in what they can prove:

    - **Stream C is exact.** Each row carries its own `url`, so presence means routed.
    - **Streams A and B are textual.** They persist no per-entry source field (which is
      why `route_dedup.seed_items` refuses to seed them), so an id inside an entry may be
      the entry's origin OR a predecessor / successor / corroboration citation. Measured
      on the 08-26 round: 4 of 4 apparent hits were corroboration lines.

    So a prose hit is reported as `cited` — real information, not a defect.
    """
    if not source_id:
        return [], []
    routed: list[str] = []
    cited: list[str] = []
    for sink, text in sink_texts.items():
        if sink.endswith(".jsonl"):
            if source_id in _stream_c_sources(text):
                routed.append(sink)
        elif source_id in text:
            cited.append(sink)
    return routed, cited


def declared_sinks(route: str) -> list[str]:
    """The sinks a `route:` names — comma-separated, because one video legitimately
    yields several items under one id and may route them to different streams.

    ⚠ **Sinks, not counts.** Nothing here verifies HOW MANY items reached a sink, so a
    video routing three Stream C items where only one landed still reconciles clean. The
    per-note check catches a stream that was skipped entirely — which is the 2026-08-25
    shape — and not a partial write within one stream.
    """
    return [part.strip() for part in route.split(",") if part.strip()]


def malformed_marks(source_id: str, ledger: list[RoutedItem]) -> list[RoutedItem]:
    """Ledger rows that MEANT to mark `source_id` but cannot ever match.

    `route_dedup mark` validates neither argument (ST99), and the 2026-08-25 round wrote
    30 rows that were dedup-blind in two distinct shapes: 26 with a bare filename where
    the full sink path belongs, and 4 where the id and the sink were joined into one
    `--source-id`, leaving the sink empty. Both surface as "no mark", which is also what
    never marking at all looks like — and the repairs differ, so naming the shape is the
    whole point of reporting it.
    """
    if not source_id:
        return []
    return [
        i
        for i in ledger
        if i.sink not in KNOWN_SINKS
        and (i.source_id == source_id or source_id in i.source_id)
    ]


def reconcile(
    declarations: list[Declaration],
    *,
    sink_texts: dict[str, str],
    ledger: list[RoutedItem],
) -> list[Finding]:
    """One finding per declaration, in input order."""
    marked = {(i.source_id, i.sink) for i in ledger}
    findings: list[Finding] = []

    for d in declarations:
        routed, cited = _landed_in(d.source_id, sink_texts)
        landed = routed + cited

        if not d.route:
            verdict, detail = "undeclared", "no `route:` key — nothing to reconcile"
        elif d.route == DROPPED:
            if routed:
                verdict = "phantom"
                detail = f"declared dropped but is a live row in {', '.join(routed)}"
            elif cited:
                verdict = "cited"
                detail = (
                    f"declared dropped and cited in {', '.join(cited)} — most likely a "
                    "corroboration line on an existing entry, which is legitimate; "
                    "confirm it did not become an entry of its own"
                )
            else:
                verdict, detail = DROPPED, "declared dropped, absent from every sink"
        elif unknown := [s for s in declared_sinks(d.route) if s not in KNOWN_SINKS]:
            verdict = "unknown-sink"
            detail = f"{', '.join(unknown)} is not one of {', '.join(KNOWN_SINKS)}"
        elif missing := [s for s in declared_sinks(d.route) if s not in landed]:
            verdict = "unperformed"
            detail = f"declared {', '.join(missing)} but the row is not there"
            if any((d.source_id, s) in marked for s in missing):
                detail += " (the ledger IS marked — the mark is not evidence)"
        elif unmarked := [
            s for s in declared_sinks(d.route) if (d.source_id, s) not in marked
        ]:
            verdict = "unmarked"
            detail = (
                f"landed in {', '.join(unmarked)} but no ledger mark — "
                "dedup-blind from here"
            )
            for bad in malformed_marks(d.source_id, ledger):
                detail += (
                    f"; a malformed mark exists (source_id={bad.source_id!r} "
                    f"sink={bad.sink!r}) — repair that row rather than adding one"
                )
        else:
            verdict, detail = "ok", f"landed in {d.route}, ledger marked"

        findings.append(
            Finding(
                note=d.note,
                source_id=d.source_id,
                route=d.route,
                verdict=verdict,
                detail=detail,
            )
        )
    return findings


def summarise(findings: list[Finding]) -> str:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.verdict] = counts.get(f.verdict, 0) + 1
    parts = [f"{n} {verdict}" for verdict, n in sorted(counts.items())]
    return f"{len(findings)} note(s): " + (", ".join(parts) if parts else "nothing")


def render(findings: list[Finding]) -> str:
    bad = [f for f in findings if f.verdict not in CLEAN_VERDICTS]
    if not bad:
        return f"ROUND RECONCILED — {summarise(findings)}"

    lines = [
        f"ROUTE RECONCILIATION FAILED — {summarise(findings)}",
        "",
        "A note's `route:` is a DECLARATION; only the sink is evidence.",
        "",
    ]
    for verdict in dict.fromkeys(f.verdict for f in bad):
        lines.append(f"### {verdict}")
        for f in (x for x in bad if x.verdict == verdict):
            lines.append(f"  {f.note}  [{f.source_id or 'no source id'}]")
            lines.append(f"    {f.detail}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notes", nargs="+", help="the round's note files")
    parser.add_argument("--ledger", default="docs/plans/routed-ledger.json")
    parser.add_argument("--thesis-sink", default=THESIS_SINK)
    parser.add_argument("--mechanics-sink", default=MECHANICS_SINK)
    parser.add_argument("--pundit-sink", default=PUNDIT_SINK)
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    args = parser.parse_args(argv)

    declarations = [
        parse_declaration(Path(p).name, Path(p).read_text(encoding="utf-8"))
        for p in args.notes
    ]

    # Keyed by the CANONICAL sink name a note declares, while reading whatever path the
    # caller pointed at — so a tmp-dir test and a real round take the same code path.
    sink_texts = {
        canonical: Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""
        for canonical, path in (
            (THESIS_SINK, args.thesis_sink),
            (MECHANICS_SINK, args.mechanics_sink),
            (PUNDIT_SINK, args.pundit_sink),
        )
    }

    findings = reconcile(
        declarations, sink_texts=sink_texts, ledger=load_ledger(Path(args.ledger))
    )

    if args.json:
        payload: dict[str, Any] = {
            "summary": summarise(findings),
            "findings": [f.__dict__ for f in findings],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render(findings))

    return 1 if any(f.verdict not in CLEAN_VERDICTS for f in findings) else 0


if __name__ == "__main__":
    from utils.stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
