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


def _call(author: str, symbol: str, days_before: int) -> str:
    ts_ms = AS_OF - days_before * DAY_MS
    iso = f"{__import__('datetime').datetime.fromtimestamp(ts_ms / 1000, tz=__import__('datetime').UTC).strftime('%Y-%m-%dT%H:%M:%S')}Z"
    return json.dumps(
        {
            "source": "twitter",
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
                "sweep_reclaim": {"long": {"n": 6, "hit_rate": 0.6, "avg_atr_r": 1.1}}
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
