"""Pundit board: priors parsing, recent-call filtering, graceful degradation."""

import json
from pathlib import Path

from analytics.brief.config import BriefConfig
from analytics.brief.pundit import build_board

AS_OF = 1_704_067_200_000  # 2024-01-01 00:00 UTC
DAY_MS = 86_400_000


def _cfg(tmp_path: Path, **kwargs: object) -> BriefConfig:
    return BriefConfig(
        symbols=("BTCUSDT",),
        as_of_ms=AS_OF,
        ledger_path=tmp_path / "calls.jsonl",
        priors_path=tmp_path / "priors.json",
        **kwargs,  # type: ignore[arg-type]
    )


def _call(author: str, symbol: str, days_before: int, source: str = "twitter") -> str:
    ts_ms = AS_OF - days_before * DAY_MS
    iso = f"{__import__('datetime').datetime.fromtimestamp(ts_ms / 1000, tz=__import__('datetime').UTC).strftime('%Y-%m-%dT%H:%M:%S')}Z"
    return json.dumps(
        {
            "source": source,
            "author": author,
            "url": "https://x.com/x/status/1",
            "call_ts_utc": iso,
            "symbol": symbol,
            "direction": "long",
            "entry": "zone 100-101 on a reclaim of the level with confirmation and volume",
            "stop": "99",
            "target": "110",
            "horizon": "swing",
            "confidence": "high",
            "raw_quote": "quote",
        }
    )


def _priors() -> str:
    return json.dumps(
        {
            "generated_at": "2023-12-31T00:00:00Z",
            "as_of": "2023-12-31T00:00:00Z",
            "policy": {"windows": {}, "atr": "atr14", "min_n_marker": 5},
            "authors": {
                "alice": {"n": 6, "hit_rate": 0.5, "avg_r": 0.2, "avg_atr_r": 0.9},
                "bob": {"n": 2, "hit_rate": 1.0, "avg_r": 1.0, "avg_atr_r": 1.5},
            },
            "families": {
                "sweep_reclaim": {
                    "long": {
                        "n": 6,
                        "hit_rate": 0.6,
                        "avg_r": 0.5,
                        "avg_atr_r": 1.1,
                    }
                },
                # avg_r null (no resolved calls) — must parse to None, not crash.
                "breakout": {
                    "short": {
                        "n": 6,
                        "hit_rate": 0.5,
                        "avg_r": None,
                        "avg_atr_r": 0.8,
                    }
                },
            },
        }
    )


def test_board_happy_path(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.ledger_path.write_text(
        "\n".join(
            [
                _call("alice", "BTCUSDT", 1),
                _call("bob", "ETHUSDT", 3),
                _call("carol", "BTCUSDT", 30),  # outside 14d window
                "not json at all",
            ]
        )
    )
    cfg.priors_path.write_text(_priors())
    board = build_board(cfg)
    assert board.priors_status == "ok"
    assert board.priors_age_days == 1
    assert board.min_n_marker == 5
    assert board.ledger_total == 4 and board.ledger_skipped == 1
    assert [c.author for c in board.recent_calls] == ["alice", "bob"]
    assert board.recent_calls[0].on_panel is True  # BTCUSDT is a panel symbol
    assert board.recent_calls[1].on_panel is False
    assert board.recent_calls[0].prior is not None
    assert board.recent_calls[0].prior.flagged is False  # n=6 >= 5
    assert len(board.recent_calls[0].entry) <= 60
    assert board.authors[0].author == "alice"  # sorted by n desc
    bob = next(a for a in board.authors if a.author == "bob")
    assert bob.flagged is True  # n=2 < 5
    assert board.families[0].family == "sweep_reclaim"
    assert board.families[0].direction == "long"
    assert board.families[0].avg_r == 0.5  # float avg_r parsed
    breakout = next(f for f in board.families if f.family == "breakout")
    assert breakout.avg_r is None  # null avg_r parses to None, no crash


def test_board_excludes_ai_card_dual_writes(tmp_path: Path) -> None:
    # TRADE cards dual-write a {source:"ai-card", author:"buibui_card"} row to
    # the same ledger so the scorer can grade the AI. The board must NOT cite
    # the card as an external pundit (self-citation). The line still counts in
    # ledger_total (it is a real ledger line) but is excluded from the board —
    # not treated as malformed/skipped.
    cfg = _cfg(tmp_path)
    cfg.ledger_path.write_text(
        "\n".join(
            [
                _call("alice", "BTCUSDT", 1),
                _call("buibui_card", "BTCUSDT", 1, source="ai-card"),
            ]
        )
    )
    board = build_board(cfg)
    assert [c.author for c in board.recent_calls] == ["alice"]
    assert board.ledger_total == 2  # both lines counted
    assert board.ledger_skipped == 0  # ai-card row excluded, not malformed


def test_board_omits_ai_card_author_from_priors(tmp_path: Path) -> None:
    # The scorer scores buibui_card (the kill-test) and writes it into
    # priors.json, but the board must not surface the card's own track record
    # as if it were an external pundit — even when its n outranks humans.
    cfg = _cfg(tmp_path)
    cfg.priors_path.write_text(
        json.dumps(
            {
                "generated_at": "2023-12-31T00:00:00Z",
                "policy": {"min_n_marker": 5},
                "authors": {
                    "alice": {"n": 6, "hit_rate": 0.5, "avg_r": 0.2},
                    "buibui_card": {"n": 9, "hit_rate": 0.4, "avg_r": -0.1},
                },
            }
        )
    )
    cfg.ledger_path.write_text(_call("alice", "BTCUSDT", 1))
    board = build_board(cfg)
    names = [a.author for a in board.authors]
    assert "alice" in names
    assert "buibui_card" not in names


def test_board_absent_files(tmp_path: Path) -> None:
    board = build_board(_cfg(tmp_path))
    assert board.priors_status == "absent"
    assert board.ledger_status == "absent"
    assert board.recent_calls == [] and board.authors == []


def test_board_unreadable_priors(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.priors_path.write_text("{broken json")
    cfg.ledger_path.write_text(_call("alice", "BTCUSDT", 1))
    board = build_board(cfg)
    assert board.priors_status == "unreadable"
    assert len(board.recent_calls) == 1
    assert board.recent_calls[0].prior is None


def test_future_dated_call_excluded(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.ledger_path.write_text(_call("alice", "BTCUSDT", -2))  # 2 days in future
    board = build_board(cfg)
    assert board.recent_calls == []


def test_board_null_policy_falls_back_to_default_min_n(tmp_path: Path) -> None:
    # "policy": null — data.get("policy", {}) returns None (key present, not
    # absent), so a naive .get("min_n_marker", ...) chain raises AttributeError.
    # A non-dict policy is optional metadata: the file is otherwise well-formed,
    # so this should degrade to the default min_n rather than "unreadable".
    cfg = _cfg(tmp_path)
    cfg.priors_path.write_text(
        json.dumps({"authors": {"alice": {"n": 6}}, "policy": None})
    )
    cfg.ledger_path.write_text(_call("alice", "BTCUSDT", 1))
    board = build_board(cfg)
    assert board.priors_status == "ok"
    assert board.min_n_marker == 5  # default fallback, no crash
    assert len(board.recent_calls) == 1
    assert board.recent_calls[0].prior is not None
    assert board.recent_calls[0].prior.flagged is False  # n=6 >= default 5


def test_board_null_n_degrades_gracefully(tmp_path: Path) -> None:
    # "n": null in an author cell — int(None) raises TypeError.
    cfg = _cfg(tmp_path)
    cfg.priors_path.write_text(
        json.dumps(
            {
                "authors": {"alice": {"n": None, "hit_rate": 0.5}},
                "policy": {"min_n_marker": 5},
            }
        )
    )
    cfg.ledger_path.write_text(_call("alice", "BTCUSDT", 1))
    board = build_board(cfg)
    assert board.priors_status == "unreadable"
    assert len(board.recent_calls) == 1
    assert board.recent_calls[0].prior is None


def test_board_non_numeric_n_degrades_gracefully(tmp_path: Path) -> None:
    # "n": "abc" in a family cell — int("abc") raises ValueError.
    cfg = _cfg(tmp_path)
    cfg.priors_path.write_text(
        json.dumps(
            {
                "authors": {"alice": {"n": 6}},
                "policy": {"min_n_marker": 5},
                "families": {"sweep_reclaim": {"long": {"n": "abc"}}},
            }
        )
    )
    cfg.ledger_path.write_text(_call("alice", "BTCUSDT", 1))
    board = build_board(cfg)
    assert board.priors_status == "unreadable"
    assert len(board.recent_calls) == 1
    assert board.recent_calls[0].prior is None


def test_board_skips_direction_outside_the_enum(tmp_path: Path) -> None:
    """A fourth direction value must not reach the board.

    The board renders ``direction`` verbatim and the scorer books any
    non-``long`` value as a SHORT, so an unvalidated ``"range"`` is a wrong
    number on both surfaces. The falsy check here caught a *missing*
    direction but passed any truthy string straight through.
    """
    good = _call("alice", "BTCUSDT", 1)
    bad = json.dumps(json.loads(_call("bob", "BTCUSDT", 1)) | {"direction": "range"})
    cfg = _cfg(tmp_path)
    cfg.ledger_path.write_text(good + "\n" + bad + "\n")
    board = build_board(cfg)
    assert [c.author for c in board.recent_calls] == ["alice"]
    assert board.ledger_total == 2 and board.ledger_skipped == 1


def test_board_folds_direction_case_rather_than_dropping_it(tmp_path: Path) -> None:
    """Positive control for the guard above — a valid row still gets through.

    Without this, the skip assertion is satisfied by a board that drops
    everything, which is indistinguishable from a correct guard.
    """
    row = json.dumps(json.loads(_call("alice", "BTCUSDT", 1)) | {"direction": "SHORT"})
    cfg = _cfg(tmp_path)
    cfg.ledger_path.write_text(row + "\n")
    board = build_board(cfg)
    assert [c.direction for c in board.recent_calls] == ["short"]
    assert board.ledger_skipped == 0


def test_board_skips_horizon_outside_the_enum(tmp_path: Path) -> None:
    """A row the scorer refuses must not render as if it were tracked.

    The board only *prints* horizon, so no wrong number is possible on this
    surface — but the scorer skips an unrecognised one permanently, unlike a
    merely unresolved recent call, so showing it implies a track-record
    contribution that will never arrive.
    """
    good = _call("alice", "BTCUSDT", 1)
    bad = json.dumps(json.loads(_call("bob", "BTCUSDT", 1)) | {"horizon": "scalp"})
    cfg = _cfg(tmp_path)
    cfg.ledger_path.write_text(good + "\n" + bad + "\n")
    board = build_board(cfg)
    assert [c.author for c in board.recent_calls] == ["alice"]
    assert board.ledger_total == 2 and board.ledger_skipped == 1


def test_board_keeps_an_absent_horizon_and_still_renders_it_as_empty(
    tmp_path: Path,
) -> None:
    """Positive control, and a guard on the display semantics.

    Absence is legitimate, so the row survives. It must also still carry
    ``""`` rather than the canonical ``"unspecified"`` — ``render.py`` prints
    the field only when truthy, so storing the normalised value would grow a
    ``· unspecified`` suffix on every previously-silent row. That would be a
    visible board change smuggled in by a validation fix.
    """
    row = json.loads(_call("alice", "BTCUSDT", 1))
    row.pop("horizon", None)
    cfg = _cfg(tmp_path)
    cfg.ledger_path.write_text(json.dumps(row) + "\n")
    board = build_board(cfg)
    assert [c.author for c in board.recent_calls] == ["alice"]
    assert [c.horizon for c in board.recent_calls] == [""]
    assert board.ledger_skipped == 0
