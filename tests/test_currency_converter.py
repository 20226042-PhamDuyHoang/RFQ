"""Unit tests for currency conversion — API fallback, edge cases."""
import pytest
from unittest.mock import patch, MagicMock

from backend.services.currency_converter import convert_to_usd, _rate_cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear exchange rate cache between tests."""
    _rate_cache.clear()
    yield
    _rate_cache.clear()


class TestConvertToUsd:
    """Test convert_to_usd with fallback rates (no API calls)."""

    def test_usd_to_usd_noop(self):
        result = convert_to_usd(100.0, "USD")
        assert result == 100.0

    def test_eur_fallback(self):
        with patch("backend.services.currency_converter.httpx.get") as mock_get:
            mock_get.side_effect = Exception("API down")
            result = convert_to_usd(100.0, "EUR")
            assert result == round(100.0 * 1.08, 2)

    def test_jpy_fallback(self):
        with patch("backend.services.currency_converter.httpx.get") as mock_get:
            mock_get.side_effect = Exception("API down")
            result = convert_to_usd(75000.0, "JPY")
            assert result == round(75000.0 * 0.0067, 2)

    def test_unknown_currency_returns_none(self):
        with patch("backend.services.currency_converter.httpx.get") as mock_get:
            mock_get.side_effect = Exception("API down")
            result = convert_to_usd(100.0, "FAKE")
            assert result is None

    def test_zero_amount(self):
        result = convert_to_usd(0.0, "USD")
        assert result == 0.0

    def test_api_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "result": "success",
            "conversion_rate": 1.09,
        }

        with patch("backend.services.currency_converter.httpx.get", return_value=mock_resp):
            result = convert_to_usd(100.0, "EUR")
            assert result == round(100.0 * 1.09, 2)
