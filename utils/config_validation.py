from typing import Dict, Any


def validate_coins_config(config_dict: Dict[str, Any]) -> bool:
    """
    Validate the coins.json config dict.
    Raises ValueError if invalid.
    """
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
        sl = params["sl_percent"]
        if not (isinstance(lev, int) or isinstance(lev, float)):
            raise ValueError(f"Symbol '{symbol}' leverage must be a number.")
        if not (1 <= lev <= 150):
            raise ValueError(f"Symbol '{symbol}' leverage {lev} out of range (1-150).")
        if not (isinstance(sl, int) or isinstance(sl, float)):
            raise ValueError(f"Symbol '{symbol}' sl_percent must be a number.")
        if not (0.1 <= sl <= 100):
            raise ValueError(
                f"Symbol '{symbol}' sl_percent {sl} out of range (0.1-100)."
            )
    return True
