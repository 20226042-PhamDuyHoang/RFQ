"""Unit tests for AI extraction pipeline — validation, JSON cleaning, normalization."""
import json
import pytest

from backend.services.ai_extractor import _clean_llm_json, _validate_extraction
from backend.schemas import ExtractionResult


class TestCleanLLMJson:
    """Test _clean_llm_json: strip markdown, find JSON from messy LLM output."""

    def test_clean_json_plain(self):
        raw = '{"unit_price": 450, "currency": "USD"}'
        assert json.loads(_clean_llm_json(raw)) == {"unit_price": 450, "currency": "USD"}

    def test_clean_json_with_markdown_fences(self):
        raw = '```json\n{"unit_price": 450}\n```'
        result = _clean_llm_json(raw)
        assert json.loads(result) == {"unit_price": 450}

    def test_clean_json_with_leading_text(self):
        raw = 'Here is the result:\n{"unit_price": 520, "currency": "USD"}'
        result = _clean_llm_json(raw)
        assert json.loads(result)["unit_price"] == 520

    def test_clean_json_with_trailing_text(self):
        raw = '{"unit_price": 700}\nLet me know if you need more info.'
        result = _clean_llm_json(raw)
        assert json.loads(result)["unit_price"] == 700

    def test_clean_json_whitespace(self):
        raw = '  \n  {"unit_price": 100}  \n  '
        assert json.loads(_clean_llm_json(raw))["unit_price"] == 100


class TestValidateExtraction:
    """Test _validate_extraction: type coercion, validation, confidence clamping."""

    def _base_data(self, **overrides):
        data = {
            "vendor_email": "vendor@test.com",
            "unit_price": 450.0,
            "currency": "USD",
            "lead_time_days": 7,
            "payment_terms": "Net 30",
            "confidence_score": 0.95,
        }
        data.update(overrides)
        return data

    def test_valid_extraction(self):
        result = _validate_extraction(self._base_data(), "vendor@test.com")
        assert isinstance(result, ExtractionResult)
        assert result.unit_price == 450.0
        assert result.currency == "USD"
        assert result.lead_time_days == 7
        assert result.confidence_score == 0.95

    def test_negative_price_lowers_confidence(self):
        result = _validate_extraction(self._base_data(unit_price=-10), "v@t.com")
        assert result.confidence_score <= 0.3

    def test_zero_lead_time_lowers_confidence(self):
        result = _validate_extraction(self._base_data(lead_time_days=0), "v@t.com")
        assert result.confidence_score <= 0.3

    def test_unknown_currency_defaults_usd(self):
        result = _validate_extraction(self._base_data(currency="XYZ"), "v@t.com")
        assert result.currency == "USD"

    def test_supported_currencies(self):
        for cur in ("EUR", "GBP", "JPY", "CNY", "KRW", "VND"):
            result = _validate_extraction(self._base_data(currency=cur), "v@t.com")
            assert result.currency == cur

    def test_confidence_clamped_above_1(self):
        result = _validate_extraction(self._base_data(confidence_score=1.5), "v@t.com")
        assert result.confidence_score == 1.0

    def test_confidence_clamped_below_0(self):
        result = _validate_extraction(self._base_data(confidence_score=-0.5), "v@t.com")
        assert result.confidence_score == 0.0

    def test_missing_payment_terms_default(self):
        result = _validate_extraction(self._base_data(payment_terms=""), "v@t.com")
        assert result.payment_terms == "N/A"

    def test_string_price_coerced(self):
        result = _validate_extraction(self._base_data(unit_price="520"), "v@t.com")
        assert result.unit_price == 520.0

    def test_string_lead_time_coerced(self):
        result = _validate_extraction(self._base_data(lead_time_days="14"), "v@t.com")
        assert result.lead_time_days == 14
