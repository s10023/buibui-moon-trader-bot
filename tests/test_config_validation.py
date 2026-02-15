import pytest
from utils.config_validation import validate_coins_config


class TestValidateCoinsConfig:
    """Tests for validate_coins_config()."""

    def test_valid_config(self, sample_coins_config):
        """Valid config returns True."""
        assert validate_coins_config(sample_coins_config) is True

    def test_empty_config(self):
        """Empty dict is valid (no symbols configured)."""
        assert validate_coins_config({}) is True

    def test_single_symbol(self):
        """Single valid symbol."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": 2.0}}
        assert validate_coins_config(config) is True

    def test_not_a_dict(self):
        """Non-dict input raises ValueError."""
        with pytest.raises(ValueError, match="Config must be a dict"):
            validate_coins_config("not a dict")

    def test_list_input(self):
        """List input raises ValueError."""
        with pytest.raises(ValueError, match="Config must be a dict"):
            validate_coins_config([{"leverage": 25}])

    def test_missing_leverage(self):
        """Missing leverage key raises ValueError."""
        config = {"BTCUSDT": {"sl_percent": 2.0}}
        with pytest.raises(ValueError, match="missing 'leverage'"):
            validate_coins_config(config)

    def test_missing_sl_percent(self):
        """Missing sl_percent key raises ValueError."""
        config = {"BTCUSDT": {"leverage": 25}}
        with pytest.raises(ValueError, match="missing 'sl_percent'"):
            validate_coins_config(config)

    def test_params_not_dict(self):
        """Non-dict params raises ValueError."""
        config = {"BTCUSDT": "invalid"}
        with pytest.raises(ValueError, match="must be a dict"):
            validate_coins_config(config)

    def test_leverage_not_numeric(self):
        """Non-numeric leverage raises ValueError."""
        config = {"BTCUSDT": {"leverage": "high", "sl_percent": 2.0}}
        with pytest.raises(ValueError, match="leverage must be a number"):
            validate_coins_config(config)

    def test_leverage_too_low(self):
        """Leverage below 1 raises ValueError."""
        config = {"BTCUSDT": {"leverage": 0, "sl_percent": 2.0}}
        with pytest.raises(ValueError, match="out of range"):
            validate_coins_config(config)

    def test_leverage_too_high(self):
        """Leverage above 150 raises ValueError."""
        config = {"BTCUSDT": {"leverage": 200, "sl_percent": 2.0}}
        with pytest.raises(ValueError, match="out of range"):
            validate_coins_config(config)

    def test_leverage_boundary_low(self):
        """Leverage at minimum boundary (1) is valid."""
        config = {"BTCUSDT": {"leverage": 1, "sl_percent": 2.0}}
        assert validate_coins_config(config) is True

    def test_leverage_boundary_high(self):
        """Leverage at maximum boundary (150) is valid."""
        config = {"BTCUSDT": {"leverage": 150, "sl_percent": 2.0}}
        assert validate_coins_config(config) is True

    def test_sl_percent_not_numeric(self):
        """Non-numeric sl_percent raises ValueError."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": "tight"}}
        with pytest.raises(ValueError, match="sl_percent must be a number"):
            validate_coins_config(config)

    def test_sl_percent_too_low(self):
        """sl_percent below 0.1 raises ValueError."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": 0.05}}
        with pytest.raises(ValueError, match="out of range"):
            validate_coins_config(config)

    def test_sl_percent_too_high(self):
        """sl_percent above 100 raises ValueError."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": 150}}
        with pytest.raises(ValueError, match="out of range"):
            validate_coins_config(config)

    def test_sl_percent_boundary_low(self):
        """sl_percent at minimum boundary (0.1) is valid."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": 0.1}}
        assert validate_coins_config(config) is True

    def test_sl_percent_boundary_high(self):
        """sl_percent at maximum boundary (100) is valid."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": 100}}
        assert validate_coins_config(config) is True

    def test_leverage_as_float(self):
        """Float leverage is valid."""
        config = {"BTCUSDT": {"leverage": 25.0, "sl_percent": 2.0}}
        assert validate_coins_config(config) is True

    def test_sl_percent_as_int(self):
        """Integer sl_percent is valid."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": 3}}
        assert validate_coins_config(config) is True

    def test_multiple_symbols_one_invalid(self):
        """If any symbol is invalid, raises ValueError."""
        config = {
            "BTCUSDT": {"leverage": 25, "sl_percent": 2.0},
            "ETHUSDT": {"leverage": 0, "sl_percent": 2.5},
        }
        with pytest.raises(ValueError, match="ETHUSDT.*out of range"):
            validate_coins_config(config)
