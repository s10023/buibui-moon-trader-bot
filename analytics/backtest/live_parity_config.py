"""T6 live-parity gate toggles for run_backtest().

Each flag defaults to False so passing a default-constructed `LiveParityConfig()`
(or `None`) keeps the engine's current behaviour. Set `enabled=True` to flip
every individual flag on at once; per-gate flags remain effective on top of the
master switch so callers can compose `--live-parity --without-cooldown`.

PR-1 lands the dataclass + plumbing only. Per-gate logic ports ship in PRs 2-5.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveParityConfig:
    """Toggle live-only gates inside run_backtest().

    `enabled` is the master switch. `is_on(gate)` returns True when the master
    switch OR the specific gate field is set. Cooldown bars per timeframe are
    optional and fall back to the engine's baked-in defaults when None.
    """

    enabled: bool = False
    regime: bool = False
    direction_filter: bool = False
    f8_htf_ema: bool = False
    adr_bias: bool = False
    conflict_resolver: bool = False
    cooldown: bool = False
    cooldown_bars_per_tf: dict[str, int] | None = None

    def is_on(self, gate: str) -> bool:
        """Return True iff the named gate field is set.

        Note: `enabled` is a *resolver-time* convenience — the CLI/TOML resolver
        expands it into per-gate True values *before* the engine sees the
        config, so an explicit `--without-<gate>` can still cleanly disable
        one gate while the master switch stays on (the acceptance contract).
        """
        return bool(getattr(self, gate))


_GATES: tuple[str, ...] = (
    "adr_bias",
    "conflict_resolver",
    "cooldown",
    "direction_filter",
    "enabled",
    "f8_htf_ema",
    "regime",
)


def live_parity_key(cfg: LiveParityConfig | None) -> str | None:
    """Return a canonical string for a parity config, or None when every gate is off.

    ST86: the parity gates change which signals the engine keeps, so two runs
    that differ only here are different books and must not share a ``run_id``.
    ``None`` for a default-constructed config is load-bearing — it keeps the
    hash of every pre-ST86 row byte-identical, so historical sweeps stay
    addressable and the live ``backtest_cache`` is not invalidated.

    Gate names are sorted and the cooldown map is rendered in sorted key order,
    so one config has exactly one spelling regardless of construction order.
    The string is stored verbatim in ``backtest_runs.live_parity``, which is
    what lets a stored row answer *which* gates it ran under rather than only
    that some were on.
    """
    if cfg is None:
        return None
    on = [g for g in _GATES if getattr(cfg, g)]
    if not on:
        return None
    key = ",".join(on)
    if cfg.cooldown_bars_per_tf:
        bars = ",".join(
            f"{tf}={cfg.cooldown_bars_per_tf[tf]}"
            for tf in sorted(cfg.cooldown_bars_per_tf)
        )
        key += f"|cd:{bars}"
    return key
