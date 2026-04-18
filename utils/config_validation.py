from typing import Any

_MAX_LEVERAGE = 150
_MIN_LEVERAGE = 1
_MIN_SL_PCT = 0.1
_MAX_SL_PCT = 100


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_coins_config(config_dict: dict[str, Any]) -> bool:
    """Validate the coins.json config dict. Raises ValueError if invalid."""
    if not isinstance(config_dict, dict):
        raise ValueError("Config must be a dict of symbol: {leverage, sl_percent}")

    for symbol, params in config_dict.items():
        if not isinstance(symbol, str):
            raise ValueError(f"Symbol key '{symbol}' is not a string.")
        if not isinstance(params, dict):
            raise ValueError(f"Value for symbol '{symbol}' must be a dict.")
        if "leverage" not in params:
            raise ValueError(f"Symbol '{symbol}' missing 'leverage'.")
        if "sl_percent" not in params:
            raise ValueError(f"Symbol '{symbol}' missing 'sl_percent'.")

        lev = params["leverage"]
        if not _is_number(lev):
            raise ValueError(f"Symbol '{symbol}' leverage must be a number.")
        if not (_MIN_LEVERAGE <= lev <= _MAX_LEVERAGE):
            raise ValueError(
                f"Symbol '{symbol}' leverage {lev} out of range "
                f"({_MIN_LEVERAGE}-{_MAX_LEVERAGE})."
            )

        sl = params["sl_percent"]
        if not _is_number(sl):
            raise ValueError(f"Symbol '{symbol}' sl_percent must be a number.")
        if not (_MIN_SL_PCT <= sl <= _MAX_SL_PCT):
            raise ValueError(
                f"Symbol '{symbol}' sl_percent {sl} out of range "
                f"({_MIN_SL_PCT}-{_MAX_SL_PCT})."
            )

        if "smt_secondary" in params:
            sec = params["smt_secondary"]
            if not isinstance(sec, str) or not sec:
                raise ValueError(
                    f"Symbol '{symbol}' smt_secondary must be a non-empty string."
                )
    return True
