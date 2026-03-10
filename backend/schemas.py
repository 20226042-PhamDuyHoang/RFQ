from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, EmailStr, Field


# -------------------------------------------------------
# RFQ Schemas
# -------------------------------------------------------

class VendorInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    email: EmailStr
    company: Optional[str] = None


class RFQCreate(BaseModel):
    product: str = Field(..., min_length=1, max_length=500, examples=["40ft Container - Electronics"])
    quantity: int = Field(..., gt=0, examples=[3])
    origin: str = Field(..., min_length=1, max_length=300, examples=["Shenzhen"])
    destination: str = Field(..., min_length=1, max_length=300, examples=["Los Angeles"])
    required_delivery_date: Optional[str] = Field(None, examples=["2026-04-15"])
    special_notes: Optional[str] = Field(None, examples=["Temperature control required"])
    vendors: List[VendorInput] = Field(..., min_length=1)


class RFQResponse(BaseModel):
    id: int
    product: str
    quantity: int
    origin: str
    destination: str
    required_delivery_date: Optional[str]
    special_notes: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RFQDetail(RFQResponse):
    vendors: List["VendorOut"]
    vendor_responses: List["VendorResponseOut"]


# -------------------------------------------------------
# Vendor Schemas
# -------------------------------------------------------

class VendorOut(BaseModel):
    id: int
    name: str
    email: str
    company: Optional[str]

    model_config = ConfigDict(from_attributes=True)


# -------------------------------------------------------
# VendorResponse Schemas
# -------------------------------------------------------

class VendorResponseOut(BaseModel):
    id: int
    rfq_id: int
    vendor_email: str
    vendor_name: Optional[str]
    email_subject: Optional[str]
    email_body: Optional[str]
    email_received_at: Optional[datetime]

    unit_price: Optional[float]
    currency: Optional[str]
    unit_price_usd: Optional[float]
    lead_time_days: Optional[int]
    payment_terms: Optional[str]
    confidence_score: Optional[float]

    has_attachment: bool
    attachment_filename: Optional[str]
    incoterms: Optional[str]
    penalty_clause: Optional[str]
    validity: Optional[str]

    status: str
    extraction_error: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# -------------------------------------------------------
# Extraction result (tu LLM)
# -------------------------------------------------------

class ExtractionResult(BaseModel):
    vendor_email: str
    unit_price: float
    currency: str = "USD"
    lead_time_days: int
    payment_terms: str
    confidence_score: float = Field(ge=0.0, le=1.0)


class ContractExtraction(BaseModel):
    incoterms: Optional[str] = None
    penalty_clause: Optional[str] = None
    validity: Optional[str] = None


# -------------------------------------------------------
# Dashboard / Comparison
# -------------------------------------------------------

class ComparisonRow(BaseModel):
    vendor_name: Optional[str]
    vendor_email: str
    unit_price_usd: Optional[float]
    lead_time_days: Optional[int]
    payment_terms: Optional[str]
    confidence_score: Optional[float]
    incoterms: Optional[str]
    penalty_clause: Optional[str]
    validity: Optional[str]


class ComparisonTable(BaseModel):
    rfq_id: int
    product: str
    route: str
    rows: List[ComparisonRow]


# -------------------------------------------------------
# Email
# -------------------------------------------------------

class EmailSendRequest(BaseModel):
    rfq_id: int


class PollEmailRequest(BaseModel):
    rfq_id: int
