"""Tests for Pydantic schema validation."""
import pytest
from pydantic import ValidationError

from backend.schemas import (
    VendorInput,
    RFQCreate,
    ExtractionResult,
)


class TestVendorInput:
    def test_valid(self):
        v = VendorInput(name="Acme", email="acme@example.com", company="Acme Corp")
        assert v.email == "acme@example.com"

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            VendorInput(name="", email="a@b.com")

    def test_invalid_email_rejected(self):
        with pytest.raises(ValidationError):
            VendorInput(name="Acme", email="not-an-email")

    def test_company_optional(self):
        v = VendorInput(name="Acme", email="a@b.com")
        assert v.company is None


class TestRFQCreate:
    def test_valid_minimal(self):
        rfq = RFQCreate(
            product="Widget",
            quantity=1,
            origin="A",
            destination="B",
            vendors=[VendorInput(name="V", email="v@b.com")],
        )
        assert rfq.product == "Widget"

    def test_quantity_must_be_positive(self):
        with pytest.raises(ValidationError):
            RFQCreate(
                product="X", quantity=0, origin="A", destination="B",
                vendors=[VendorInput(name="V", email="v@b.com")],
            )

    def test_negative_quantity(self):
        with pytest.raises(ValidationError):
            RFQCreate(
                product="X", quantity=-5, origin="A", destination="B",
                vendors=[VendorInput(name="V", email="v@b.com")],
            )

    def test_empty_vendors_list(self):
        with pytest.raises(ValidationError):
            RFQCreate(
                product="X", quantity=1, origin="A", destination="B",
                vendors=[],
            )

    def test_product_max_length(self):
        with pytest.raises(ValidationError):
            RFQCreate(
                product="X" * 501, quantity=1, origin="A", destination="B",
                vendors=[VendorInput(name="V", email="v@b.com")],
            )


class TestExtractionResult:
    def test_valid(self):
        er = ExtractionResult(
            vendor_email="v@b.com",
            unit_price=100.0,
            currency="USD",
            lead_time_days=30,
            payment_terms="Net 30",
            confidence_score=0.85,
        )
        assert er.unit_price == 100.0

    def test_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            ExtractionResult(
                vendor_email="v@b.com",
                unit_price=100.0,
                lead_time_days=30,
                payment_terms="Net 30",
                confidence_score=1.5,
            )

    def test_default_currency(self):
        er = ExtractionResult(
            vendor_email="v@b.com",
            unit_price=50.0,
            lead_time_days=7,
            payment_terms="COD",
            confidence_score=0.7,
        )
        assert er.currency == "USD"
