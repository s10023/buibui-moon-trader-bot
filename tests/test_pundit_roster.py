"""Resolution is exact-alias only; every non-MAPPED outcome must drop."""

from typing import Any

from tools.pundit_roster import (
    AMBIGUOUS,
    MAPPED,
    UNKNOWN,
    UNMAPPED,
    build_index,
    resolve,
)

ROSTER: dict[str, Any] = {
    "schema_version": 1,
    "pundit": [
        {
            "handle": "Traderfengge",
            "aliases": ["峰哥", "风哥"],
            "confidence": "high",
        },
        {
            "handle": "wugudehaore",
            "aliases": ["波浪", "柳玉东"],
            "confidence": "operator",
        },
        {
            "handle": "junzhangbtc",
            "aliases": ["军长", "BTC君掌", "君掌"],
            "confidence": "operator",
        },
    ],
    "unmapped": [{"text": "分"}],
    "ambiguous": [
        {
            "text": "陈志峰",
            "variants": ["陈智峰", "陈志锋"],
            "members": ["traderchenge", "bitezhi", "Traderfengge"],
            "action": "never_auto_attribute",
        },
        {"text": "陈哥", "action": "never_auto_attribute"},
    ],
}


def test_exact_alias_resolves_to_the_handle() -> None:
    got = resolve(build_index(ROSTER), "峰哥")
    assert got.outcome == MAPPED
    assert got.handle == "Traderfengge"
    assert got.confidence == "high"
    assert got.routable is True


def test_the_handle_itself_resolves() -> None:
    assert resolve(build_index(ROSTER), "Traderfengge").handle == "Traderfengge"


def test_leading_at_sign_is_stripped_from_the_stored_handle() -> None:
    roster: dict[str, Any] = {
        "pundit": [{"handle": "@Traderfengge", "aliases": ["峰哥"]}]
    }
    assert resolve(build_index(roster), "峰哥").handle == "Traderfengge"


def test_unrelated_aliases_for_one_person_both_resolve() -> None:
    """波浪 and 柳玉东 share no characters. Only an explicit list can join them."""
    index = build_index(ROSTER)
    assert resolve(index, "波浪").handle == "wugudehaore"
    assert resolve(index, "柳玉东").handle == "wugudehaore"


def test_homophone_variants_both_resolve() -> None:
    index = build_index(ROSTER)
    assert resolve(index, "军长").handle == "junzhangbtc"
    assert resolve(index, "君掌").handle == "junzhangbtc"


def test_ambiguous_string_is_never_mapped() -> None:
    got = resolve(build_index(ROSTER), "陈志峰")
    assert got.outcome == AMBIGUOUS
    assert got.routable is False
    assert got.members == ("traderchenge", "bitezhi", "Traderfengge")


def test_ambiguous_variants_are_also_ambiguous() -> None:
    assert resolve(build_index(ROSTER), "陈智峰").outcome == AMBIGUOUS


def test_chen_ge_is_ambiguous_not_a_traderchenge_alias() -> None:
    """Pending operator confirmation it is deliberately never_auto_attribute."""
    assert resolve(build_index(ROSTER), "陈哥").outcome == AMBIGUOUS


def test_unmapped_and_unknown_stay_distinct() -> None:
    index = build_index(ROSTER)
    assert resolve(index, "分").outcome == UNMAPPED
    assert resolve(index, "某个新名字").outcome == UNKNOWN


def test_neither_unmapped_nor_unknown_is_routable() -> None:
    index = build_index(ROSTER)
    assert resolve(index, "分").routable is False
    assert resolve(index, "某个新名字").routable is False


def test_substring_of_an_alias_does_NOT_resolve() -> None:
    """THE DISCRIMINATION TEST — see Step 2b. 波浪理论 is Elliott Wave Theory, a
    phrase, not a person; it merely contains the alias 波浪. A substring matcher
    attributes every mention of wave theory to wugudehaore."""
    assert resolve(build_index(ROSTER), "波浪理论").outcome == UNKNOWN


def test_alias_containing_another_alias_resolves_to_its_own_entry() -> None:
    """BTC君掌 contains 君掌. Exact matching gets this right by construction; a
    prefix/suffix matcher can pick either, and here they agree — which is why the
    test above, not this one, is the discriminating case."""
    assert resolve(build_index(ROSTER), "BTC君掌").handle == "junzhangbtc"


def test_whitespace_is_stripped_but_case_is_NOT_folded() -> None:
    index = build_index(ROSTER)
    assert resolve(index, "  峰哥  ").handle == "Traderfengge"
    assert resolve(index, "traderfengge").outcome == UNKNOWN


def test_empty_name_is_unknown_not_a_crash() -> None:
    assert resolve(build_index(ROSTER), "   ").outcome == UNKNOWN


def test_ambiguous_wins_over_an_alias_collision() -> None:
    """Safety ordering: a never_auto_attribute string must not become routable
    because some other entry happens to alias it."""
    roster: dict[str, Any] = {
        "pundit": [{"handle": "somebody", "aliases": ["陈哥"]}],
        "ambiguous": [{"text": "陈哥", "action": "never_auto_attribute"}],
    }
    assert resolve(build_index(roster), "陈哥").outcome == AMBIGUOUS


def test_committed_example_roster_parses_and_indexes() -> None:
    """`check-toml` matches `\\.toml$`, so `.example` files are skipped by pre-commit
    and the hook prints "(no files to check)" — which reads exactly like a pass. This
    is the only thing validating that file."""
    import tomllib
    from pathlib import Path

    path = Path("config/pundit_roster.toml.example")
    roster = tomllib.loads(path.read_text(encoding="utf-8"))
    index = build_index(roster)
    assert isinstance(index.by_alias, dict)
    for entry in roster.get("pundit", []):
        assert entry.get("handle"), "every [[pundit]] needs a handle"
        assert entry.get("evidence"), "every [[pundit]] must carry its evidence"
