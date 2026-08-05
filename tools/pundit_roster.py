"""Curated name -> handle resolution for relayed pundit calls.

Pure resolution plus a thin CLI, mirroring tools/video_calltime.py. The roster file is
gitignored and operator-specific, so every pure function takes an already-parsed dict
and the IO lives only in `main`.

EXACT ALIAS MATCHING ONLY. Fuzzy, substring or similarity matching is forbidden: 波浪
and 柳玉东 are one person with no shared characters, 军长 and 君掌 are homophones, and
陈志峰 is three different traders run together. No heuristic gets all three right, and
a wrong mapping attributes real calls to the wrong trader with no downstream check able
to see it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analytics.pundit_authors import normalize_author

MAPPED = "mapped"
AMBIGUOUS = "ambiguous"
UNMAPPED = "unmapped"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Resolution:
    """The outcome of resolving one extracted name."""

    outcome: str
    text: str
    handle: str = ""
    confidence: str = ""
    members: tuple[str, ...] = ()

    @property
    def routable(self) -> bool:
        """Only a MAPPED name may be written into the ledger's `author` field."""
        return self.outcome == MAPPED


@dataclass(frozen=True)
class RosterIndex:
    by_alias: dict[str, tuple[str, str]]
    ambiguous: dict[str, tuple[str, ...]]
    unmapped: frozenset[str]


def build_index(roster: dict[str, Any]) -> RosterIndex:
    """Flatten the parsed roster into exact-match lookup tables."""
    by_alias: dict[str, tuple[str, str]] = {}
    for entry in roster.get("pundit", []):
        handle = normalize_author(str(entry.get("handle", "")))
        if not handle:
            continue
        confidence = str(entry.get("confidence", ""))
        names = [handle, *(str(a) for a in entry.get("aliases", []))]
        for name in names:
            key = name.strip()
            if key:
                by_alias[key] = (handle, confidence)

    ambiguous: dict[str, tuple[str, ...]] = {}
    for entry in roster.get("ambiguous", []):
        members = tuple(str(m) for m in entry.get("members", ()))
        texts = [
            str(entry.get("text", "")),
            *(str(v) for v in entry.get("variants", [])),
        ]
        for text in texts:
            key = text.strip()
            if key:
                ambiguous[key] = members

    unmapped = frozenset(
        key
        for entry in roster.get("unmapped", [])
        if (key := str(entry.get("text", "")).strip())
    )
    return RosterIndex(by_alias=by_alias, ambiguous=ambiguous, unmapped=unmapped)


def resolve(index: RosterIndex, name: str) -> Resolution:
    """Resolve one extracted name. Anything but MAPPED must drop the setup."""
    key = name.strip()
    if not key:
        return Resolution(outcome=UNKNOWN, text=key)
    # Ambiguous is checked FIRST on purpose: a never_auto_attribute string must not
    # become routable because some other entry happens to alias the same text.
    if key in index.ambiguous:
        return Resolution(outcome=AMBIGUOUS, text=key, members=index.ambiguous[key])
    if key in index.by_alias:
        handle, confidence = index.by_alias[key]
        return Resolution(
            outcome=MAPPED, text=key, handle=handle, confidence=confidence
        )
    if key in index.unmapped:
        return Resolution(outcome=UNMAPPED, text=key)
    return Resolution(outcome=UNKNOWN, text=key)


def load_roster(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(
            f"roster not found: {path} (copy {path}.example and fill it in)"
        )
    return tomllib.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve a relayed name to a handle.")
    parser.add_argument("names", nargs="+", help="extracted originating_author strings")
    parser.add_argument(
        "--roster",
        type=Path,
        default=Path("config/pundit_roster.toml"),
        help="path to the roster TOML",
    )
    args = parser.parse_args(argv)
    index = build_index(load_roster(args.roster))
    out = [
        {
            "text": r.text,
            "outcome": r.outcome,
            "handle": r.handle,
            "confidence": r.confidence,
            "members": list(r.members),
            "routable": r.routable,
        }
        for r in (resolve(index, n) for n in args.names)
    ]
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
