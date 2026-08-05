import pytest

from tools.x_route import route_target


@pytest.mark.parametrize(
    "content_type, verdict, expected",
    [
        ("setup", "NOVEL", "docs/plans/pundit-calls.jsonl"),
        ("mechanic", "NOVEL", "docs/plans/mechanics-backlog.md"),
        ("claim", "NOVEL", "docs/plans/thesis-inbox.md"),
        ("claim", "ALREADY-TESTED", None),
        ("claim", "FROZEN-CATEGORY", None),
        ("claim", "NOT-FALSIFIABLE", None),
    ],
)
def test_route_target(content_type: str, verdict: str, expected: str | None) -> None:
    assert route_target(content_type, verdict) == expected


def test_route_target_unroutable() -> None:
    with pytest.raises(ValueError):
        route_target("claim", "BOGUS")


# ---------------------------------------------------------------------------
# Setup suppressors. Both drops used to live only in the skills' markdown routing
# table, so each depended on the orchestrator reading prose correctly at the end of
# a long batch. `rejected` is here because 2026-07-31 round 3 shipped one: a pundit
# walked through a short and then explicitly argued AGAINST taking it, which is
# `setup` + `retrospective: false`, so the table routed it to Stream C and
# pundit_score.py scored him on a trade he declined. Only the digest reader caught it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["retrospective", "rejected"])
def test_a_suppressed_setup_is_dropped(flag: str) -> None:
    assert route_target("setup", "NOVEL", **{flag: True}) is None


def test_an_ordinary_setup_still_routes_to_stream_c() -> None:
    assert route_target("setup", "NOVEL") == "docs/plans/pundit-calls.jsonl"


# The flags describe a trade call. A mechanic or a claim has no entry to decline,
# and both skills already pin them to false — honouring the flag there would let a
# mis-set field silently delete a routable item.
@pytest.mark.parametrize(
    "content_type, expected",
    [
        ("mechanic", "docs/plans/mechanics-backlog.md"),
        ("claim", "docs/plans/thesis-inbox.md"),
    ],
)
def test_suppressors_do_not_apply_outside_setup(
    content_type: str, expected: str
) -> None:
    assert (
        route_target(content_type, "NOVEL", retrospective=True, rejected=True)
        == expected
    )


def test_unattributable_drops_a_setup() -> None:
    assert route_target("setup", "", unattributable=True) is None


def test_unattributable_leaves_a_mechanic_alone() -> None:
    """Setup-only, like retrospective and rejected: a mechanic scores nobody, so one
    mis-set flag must not be able to delete a routable item."""
    assert route_target("mechanic", "", unattributable=True) == (
        "docs/plans/mechanics-backlog.md"
    )


def test_unattributable_leaves_a_novel_claim_alone() -> None:
    assert route_target("claim", "NOVEL", unattributable=True) == (
        "docs/plans/thesis-inbox.md"
    )


def test_unattributable_defaults_false_so_existing_callers_are_unchanged() -> None:
    assert route_target("setup", "") == "docs/plans/pundit-calls.jsonl"
