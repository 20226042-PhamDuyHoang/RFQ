import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime,
    ForeignKey, Enum, Boolean, Index,
)
from sqlalchemy.orm import relationship

from backend.database import Base


class RFQStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    PARTIALLY_RESPONDED = "partially_responded"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class VendorResponseStatus(str, enum.Enum):
    PENDING = "pending"
    RECEIVED = "received"
    EXTRACTED = "extracted"
    FAILED = "failed"


# -------------------------------------------------------
# RFQ - Yeu cau bao gia
# -------------------------------------------------------
class RFQ(Base):
    __tablename__ = "rfqs"

    id = Column(Integer, primary_key=True, index=True)
    product = Column(String(500), nullable=False)
    quantity = Column(Integer, nullable=False)
    origin = Column(String(300), nullable=False)
    destination = Column(String(300), nullable=False)
    required_delivery_date = Column(String(50), nullable=True)
    special_notes = Column(Text, nullable=True)
    status = Column(Enum(RFQStatus), default=RFQStatus.DRAFT, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vendors = relationship("Vendor", back_populates="rfq", cascade="all, delete-orphan")
    vendor_responses = relationship("VendorResponse", back_populates="rfq", cascade="all, delete-orphan")


# -------------------------------------------------------
# Vendor - Nha cung cap
# -------------------------------------------------------
class Vendor(Base):
    __tablename__ = "vendors"
    __table_args__ = (
        Index("ix_vendors_rfq_id", "rfq_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(300), nullable=False)
    email = Column(String(300), nullable=False)
    company = Column(String(300), nullable=True)
    rfq_id = Column(Integer, ForeignKey("rfqs.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    rfq = relationship("RFQ", back_populates="vendors")


# -------------------------------------------------------
# VendorResponse - Phan hoi tu vendor (email goc + ket qua trich xuat)
# -------------------------------------------------------
class VendorResponse(Base):
    __tablename__ = "vendor_responses"
    __table_args__ = (
        Index("ix_vr_rfq_id", "rfq_id"),
        Index("ix_vr_rfq_vendor", "rfq_id", "vendor_email"),
        Index("ix_vr_message_id", "email_message_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    rfq_id = Column(Integer, ForeignKey("rfqs.id"), nullable=False)
    vendor_email = Column(String(300), nullable=False)
    vendor_name = Column(String(300), nullable=True)

    # Noi dung email goc
    email_subject = Column(Text, nullable=True)
    email_body = Column(Text, nullable=True)
    email_received_at = Column(DateTime, nullable=True)
    email_message_id = Column(String(500), nullable=True)

    # Ket qua trich xuat (structured)
    unit_price = Column(Float, nullable=True)
    currency = Column(String(10), default="USD")
    unit_price_usd = Column(Float, nullable=True)
    lead_time_days = Column(Integer, nullable=True)
    payment_terms = Column(String(500), nullable=True)
    confidence_score = Column(Float, nullable=True)

    # Contract attachment
    has_attachment = Column(Boolean, default=False)
    attachment_filename = Column(String(500), nullable=True)
    attachment_path = Column(String(1000), nullable=True)
    incoterms = Column(String(200), nullable=True)
    penalty_clause = Column(Text, nullable=True)
    validity = Column(String(200), nullable=True)

    status = Column(Enum(VendorResponseStatus), default=VendorResponseStatus.PENDING)
    extraction_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    rfq = relationship("RFQ", back_populates="vendor_responses")


# -------------------------------------------------------
# EmailLog - Log email gui/nhan
# -------------------------------------------------------
class EmailLog(Base):
    __tablename__ = "email_logs"

    __table_args__ = (
        Index("ix_email_logs_rfq_id", "rfq_id"),
        Index("ix_email_logs_message_id", "message_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    rfq_id = Column(Integer, ForeignKey("rfqs.id"), nullable=True)
    direction = Column(String(10), nullable=False)  # "outbound" hoac "inbound"
    sender = Column(String(300), nullable=True)
    recipient = Column(String(300), nullable=True)
    subject = Column(Text, nullable=True)
    body_preview = Column(Text, nullable=True)
    message_id = Column(String(500), nullable=True)
    status = Column(String(50), default="sent")  # sent, failed, received
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
