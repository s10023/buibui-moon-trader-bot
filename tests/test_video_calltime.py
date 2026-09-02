"""Tests for tools/video_calltime.py — the look-ahead guard. Pure, no I/O."""

from __future__ import annotations

import json

import pytest

from tools.video_calltime import (
    CallTime,
    is_backlog,
    main,
    resolve_call_ts,
)

PUB = "2026-07-28T14:00:00+00:00"


def test_falls_back_to_publish_when_no_stated_time() -> None:
    got = resolve_call_ts(PUB)
    assert got == CallTime(
        call_ts_utc=PUB,
        call_ts_source="publish",
        publish_ts_utc=PUB,
        stated_ts_utc=None,
        stated_ts_raw="",
    )


def test_prefers_stated_time_when_valid() -> None:
    got = resolve_call_ts(
        PUB, stated_ts_utc="2026-07-28T08:00:00+00:00", stated_ts_raw="it's 8am Monday"
    )
    assert got.call_ts_utc == "2026-07-28T08:00:00+00:00"
    assert got.call_ts_source == "stated"
    assert got.stated_ts_raw == "it's 8am Monday"
    assert got.publish_ts_utc == PUB


def test_rejects_stated_time_at_or_after_publish() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="2026-07-28T16:00:00+00:00")
    assert got.call_ts_source == "publish"
    assert got.call_ts_utc == PUB


def test_rejects_stated_time_leading_publish_by_more_than_max() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="2026-07-01T08:00:00+00:00")
    assert got.call_ts_source == "publish"


def test_date_only_resolves_to_conservative_end_of_day() -> None:
    got = resolve_call_ts(
        PUB, stated_ts_utc="2026-07-27T00:00:00+00:00", stated_date_only=True
    )
    assert got.call_ts_utc == "2026-07-27T23:59:59+00:00"
    assert got.call_ts_source == "stated"


def test_date_only_clamps_below_publish() -> None:
    got = resolve_call_ts(
        PUB, stated_ts_utc="2026-07-28T00:00:00+00:00", stated_date_only=True
    )
    assert got.call_ts_utc == PUB
    assert got.call_ts_source == "publish"


def test_unparseable_stated_time_falls_back() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="last Tuesday-ish")
    assert got.call_ts_source == "publish"


def test_unparseable_publish_time_raises() -> None:
    with pytest.raises(ValueError, match="publish_ts_utc"):
        resolve_call_ts("not a timestamp")


def test_is_backlog_true_beyond_threshold() -> None:
    assert is_backlog(PUB, "2026-07-30T14:00:00+00:00") is True


def test_is_backlog_false_within_threshold() -> None:
    assert is_backlog(PUB, "2026-07-28T20:00:00+00:00") is False


def test_cli_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--publish", PUB, "--stated", "2026-07-28T08:00:00+00:00"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["call_ts_source"] == "stated"
    assert payload["call_ts_utc"] == "2026-07-28T08:00:00+00:00"


def test_naive_stated_time_falls_back_to_publish() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="2026-07-28T08:00:00")
    assert got.call_ts_source == "publish"
    assert got.call_ts_utc == PUB


def test_bare_date_stated_time_falls_back_to_publish() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc="2026-07-27", stated_date_only=True)
    assert got.call_ts_source == "publish"


def test_naive_publish_time_raises() -> None:
    with pytest.raises(ValueError, match="publish_ts_utc"):
        resolve_call_ts("2026-07-28T14:00:00")


def test_stated_time_exactly_equal_to_publish_is_rejected() -> None:
    got = resolve_call_ts(PUB, stated_ts_utc=PUB)
    assert got.call_ts_source == "publish"


def test_is_backlog_raises_on_unparseable_publish() -> None:
    with pytest.raises(ValueError, match="publish_ts_utc"):
        is_backlog("not a timestamp", "2026-07-30T14:00:00+00:00")


def test_is_backlog_raises_on_naive_ingested() -> None:
    with pytest.raises(ValueError, match="ingested_ts_utc"):
        is_backlog(PUB, "2026-07-30T14:00:00")


def test_relay_without_a_stated_time_is_labelled_publish_relay() -> None:
    got = resolve_call_ts("2026-08-03T12:00:00+00:00", relay=True)
    assert got.call_ts_utc == "2026-08-03T12:00:00+00:00"
    assert got.call_ts_source == "publish_relay"


def test_non_relay_without_a_stated_time_is_still_plain_publish() -> None:
    assert resolve_call_ts("2026-08-03T12:00:00+00:00").call_ts_source == "publish"


def test_relay_with_a_valid_stated_time_is_still_stated() -> None:
    """`relay` labels the FALLBACK only. A stated time that survives every bound is
    the better answer regardless of who made the call."""
    got = resolve_call_ts(
        "2026-08-03T12:00:00+00:00",
        stated_ts_utc="2026-08-03T09:30:00+00:00",
        relay=True,
    )
    assert got.call_ts_source == "stated"
    assert got.call_ts_utc == "2026-08-03T09:30:00+00:00"


def test_relay_with_a_naive_stated_time_falls_back_to_publish_relay() -> None:
    """A naive timestamp is rejected, not assumed UTC — and the relay label must
    survive that rejection, or the row silently rejoins the first-hand population."""
    got = resolve_call_ts(
        "2026-08-03T12:00:00+00:00", stated_ts_utc="2026-08-03T09:30:00", relay=True
    )
    assert got.call_ts_source == "publish_relay"


def test_relay_with_an_after_publish_stated_time_falls_back_to_publish_relay() -> None:
    got = resolve_call_ts(
        "2026-08-03T12:00:00+00:00",
        stated_ts_utc="2026-08-03T14:00:00+00:00",
        relay=True,
    )
    assert got.call_ts_source == "publish_relay"


def test_relay_beyond_the_lead_bound_falls_back_to_publish_relay() -> None:
    got = resolve_call_ts(
        "2026-08-03T12:00:00+00:00",
        stated_ts_utc="2026-07-01T09:00:00+00:00",
        relay=True,
    )
    assert got.call_ts_source == "publish_relay"


def test_a_fallback_stores_the_stated_time_that_lost_a_bound() -> None:
    """ST70(b): the fallback must say WHY it fell back.

    A publish fallback with a non-empty `stated_ts_raw` has two causes -- pass 1
    emitted `None` for an uninferable timezone (contract-correct), or it emitted
    a timestamp that then failed a bound here (a real look-ahead rejection).
    Storing the input is what separates them; 40 of 108 live fallback rows sit
    in exactly this ambiguity.
    """
    got = resolve_call_ts(
        PUB,
        stated_ts_utc="2026-07-28T16:00:00+00:00",
        stated_ts_raw="it's 4pm Monday",
    )
    assert got.call_ts_source == "publish"
    assert got.stated_ts_utc == "2026-07-28T16:00:00+00:00"


def test_an_uninferable_timezone_is_stored_as_none_not_as_empty() -> None:
    """The other half of the discriminator, and it must not collapse to "".

    `stated_ts_raw` uses "" for absent because it is a quote; `stated_ts_utc`
    uses None because "" would read as a stated time that resolved to nothing.
    """
    got = resolve_call_ts(PUB, stated_ts_raw="拍摄26年7月17日")
    assert got.call_ts_source == "publish"
    assert got.stated_ts_utc is None
    assert got.stated_ts_raw == "拍摄26年7月17日"


def test_a_stated_row_stores_the_input_verbatim_not_the_resolved_value() -> None:
    """A date-only row resolves to end-of-day, so the two fields differ.

    Storing the resolved value instead would make the field a restatement of
    `call_ts_utc` and settle nothing.
    """
    got = resolve_call_ts(
        PUB, stated_ts_utc="2026-07-27T00:00:00+00:00", stated_date_only=True
    )
    assert got.call_ts_source == "stated"
    assert got.stated_ts_utc == "2026-07-27T00:00:00+00:00"
    assert got.call_ts_utc == "2026-07-27T23:59:59+00:00"


def test_cli_emits_the_stated_input(capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI is what the skill copies into the ledger row, so it carries it."""
    main(["--publish", PUB, "--stated", "2026-07-28T08:00:00+00:00"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["stated_ts_utc"] == "2026-07-28T08:00:00+00:00"

    main(["--publish", PUB, "--stated-raw", "拍摄26年7月17日"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["stated_ts_utc"] is None
