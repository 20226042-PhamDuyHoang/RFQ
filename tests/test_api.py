"""Integration tests for the FastAPI API endpoints."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend.main import app


# In-memory SQLite for tests — use StaticPool to keep single connection
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    """Create all tables before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


client = TestClient(app)


# -------------------------------------------------------
# Health check
# -------------------------------------------------------

class TestHealthCheck:
    def test_health_returns_ok(self):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"


# -------------------------------------------------------
# RFQ CRUD
# -------------------------------------------------------

VALID_RFQ_PAYLOAD = {
    "product": "40ft Container - Electronics",
    "quantity": 3,
    "origin": "Shenzhen",
    "destination": "Los Angeles",
    "required_delivery_date": "2026-04-15",
    "special_notes": "Temperature control required",
    "vendors": [
        {"name": "Vendor A", "email": "vendorA@example.com", "company": "A Corp"},
        {"name": "Vendor B", "email": "vendorB@example.com"},
    ],
}


class TestCreateRFQ:
    def test_create_rfq_success(self):
        r = client.post("/api/rfq", json=VALID_RFQ_PAYLOAD)
        assert r.status_code == 201
        data = r.json()
        assert data["product"] == "40ft Container - Electronics"
        assert data["quantity"] == 3
        assert data["status"] == "draft"
        assert "id" in data

    def test_create_rfq_no_vendor(self):
        payload = {**VALID_RFQ_PAYLOAD, "vendors": []}
        r = client.post("/api/rfq", json=payload)
        assert r.status_code == 422  # Validation error

    def test_create_rfq_missing_product(self):
        payload = {k: v for k, v in VALID_RFQ_PAYLOAD.items() if k != "product"}
        r = client.post("/api/rfq", json=payload)
        assert r.status_code == 422

    def test_create_rfq_invalid_email(self):
        payload = {
            **VALID_RFQ_PAYLOAD,
            "vendors": [{"name": "Bad", "email": "not-an-email"}],
        }
        r = client.post("/api/rfq", json=payload)
        assert r.status_code == 422

    def test_create_rfq_zero_quantity(self):
        payload = {**VALID_RFQ_PAYLOAD, "quantity": 0}
        r = client.post("/api/rfq", json=payload)
        assert r.status_code == 422


class TestListRFQs:
    def test_list_empty(self):
        r = client.get("/api/rfq")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_after_create(self):
        client.post("/api/rfq", json=VALID_RFQ_PAYLOAD)
        r = client.get("/api/rfq")
        assert r.status_code == 200
        assert len(r.json()) == 1


class TestGetRFQDetail:
    def test_get_existing(self):
        create_r = client.post("/api/rfq", json=VALID_RFQ_PAYLOAD)
        rfq_id = create_r.json()["id"]
        r = client.get(f"/api/rfq/{rfq_id}")
        assert r.status_code == 200
        data = r.json()
        assert len(data["vendors"]) == 2
        assert data["vendor_responses"] == []

    def test_get_not_found(self):
        r = client.get("/api/rfq/999")
        assert r.status_code == 404


class TestGetResponses:
    def test_responses_empty(self):
        create_r = client.post("/api/rfq", json=VALID_RFQ_PAYLOAD)
        rfq_id = create_r.json()["id"]
        r = client.get(f"/api/rfq/{rfq_id}/responses")
        assert r.status_code == 200
        assert r.json() == []


class TestGetComparison:
    def test_comparison_no_responses(self):
        create_r = client.post("/api/rfq", json=VALID_RFQ_PAYLOAD)
        rfq_id = create_r.json()["id"]
        r = client.get(f"/api/rfq/{rfq_id}/comparison")
        # Either 404 or empty rows depending on implementation
        assert r.status_code in (200, 404)
