import logging
from typing import List, Optional
from datetime import datetime

from sqlalchemy.orm import Session

from backend.models import RFQ, Vendor, VendorResponse, EmailLog, RFQStatus, VendorResponseStatus
from backend.schemas import RFQCreate, ComparisonRow, ComparisonTable
from backend.services.email_sender import send_rfq_emails, build_rfq_subject
from backend.services.email_receiver import poll_emails_for_rfq
from backend.services.ai_extractor import (
    generate_rfq_email,
    extract_and_normalize,
    extract_contract_terms,
)
from backend.services.document_loader import load_document

logger = logging.getLogger(__name__)


# -------------------------------------------------------
# Tao RFQ moi
# -------------------------------------------------------

def create_rfq(db: Session, rfq_in: RFQCreate) -> RFQ:
    """Tao RFQ moi va luu danh sach vendor."""
    rfq = RFQ(
        product=rfq_in.product,
        quantity=rfq_in.quantity,
        origin=rfq_in.origin,
        destination=rfq_in.destination,
        required_delivery_date=rfq_in.required_delivery_date,
        special_notes=rfq_in.special_notes,
        status=RFQStatus.DRAFT,
    )
    db.add(rfq)
    db.flush()  # Lay id truoc khi them vendor

    for v in rfq_in.vendors:
        vendor = Vendor(
            name=v.name,
            email=v.email,
            company=v.company,
            rfq_id=rfq.id,
        )
        db.add(vendor)

    db.commit()
    db.refresh(rfq)
    logger.info("Created RFQ #%d: %s", rfq.id, rfq.product)
    return rfq


# -------------------------------------------------------
# Gui email RFQ toi vendors
# -------------------------------------------------------

def send_rfq_to_vendors(db: Session, rfq_id: int) -> dict:
    """
    Generate noi dung email bang LLM va gui toi tat ca vendor cua RFQ.
    """
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        return {"error": "RFQ not found"}

    vendors = db.query(Vendor).filter(Vendor.rfq_id == rfq_id).all()
    if not vendors:
        return {"error": "No vendors for this RFQ"}

    # Generate noi dung email cho tung vendor
    email_bodies = {}
    for vendor in vendors:
        logger.info("Calling LLM to generate email for vendor '%s' <%s>", vendor.name, vendor.email)
        body = generate_rfq_email(
            product=rfq.product,
            quantity=rfq.quantity,
            origin=rfq.origin,
            destination=rfq.destination,
            delivery_date=rfq.required_delivery_date,
            special_notes=rfq.special_notes,
            vendor_name=vendor.name,
        )
        email_bodies[vendor.email] = body

    # Gui email
    vendor_list = [{"email": v.email, "name": v.name} for v in vendors]
    rfq_data = {
        "product": rfq.product,
        "origin": rfq.origin,
        "destination": rfq.destination,
    }

    results = send_rfq_emails(rfq_data, vendor_list, email_bodies, rfq_id=rfq.id)

    # Log ket qua gui
    sent_count = 0
    for r in results:
        log = EmailLog(
            rfq_id=rfq.id,
            direction="outbound",
            sender=rfq_data.get("from_email", "system"),
            recipient=r["vendor_email"],
            subject=build_rfq_subject(rfq.product, rfq.origin, rfq.destination),
            body_preview=email_bodies.get(r["vendor_email"], {}).get("text", "")[:200],
            message_id=r.get("message_id", ""),
            status=r["status"],
            error_message=r.get("error"),
        )
        db.add(log)

        if r["status"] == "sent":
            sent_count += 1

    # Cap nhat trang thai RFQ
    rfq.status = RFQStatus.SENT
    rfq.updated_at = datetime.utcnow()
    db.commit()

    logger.info("Sent RFQ #%d to %d/%d vendors", rfq_id, sent_count, len(vendors))
    return {
        "rfq_id": rfq_id,
        "total_vendors": len(vendors),
        "sent": sent_count,
        "failed": len(vendors) - sent_count,
        "details": results,
    }


# -------------------------------------------------------
# Nhan va xu ly email phan hoi
# -------------------------------------------------------

def poll_and_process_responses(db: Session, rfq_id: int) -> dict:
    """
    Poll mailbox, tim email phan hoi, trich xuat du lieu, luu vao database.
    """
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        return {"error": "RFQ not found"}

    # Subject keyword theo format chuan: "RFQ - [Product]"
    subject_keyword = f"RFQ - {rfq.product}"

    # Lay danh sach vendor emails de filter theo sender
    vendor_emails = [v.email for v in db.query(Vendor).filter(Vendor.rfq_id == rfq_id).all()]

    # Lay message IDs da gui de filter theo thread (In-Reply-To / References)
    sent_logs = db.query(EmailLog).filter(
        EmailLog.rfq_id == rfq_id,
        EmailLog.direction == "outbound",
        EmailLog.status == "sent",
    ).all()
    sent_message_ids = [log.message_id for log in sent_logs if log.message_id]

    raw_emails = poll_emails_for_rfq(
        rfq_id,
        subject_keyword,
        vendor_emails=vendor_emails,
        sent_message_ids=sent_message_ids,
        since_date=rfq.created_at,
    )

    if not raw_emails:
        return {"rfq_id": rfq_id, "new_responses": 0, "message": "No new emails found"}

    new_count = 0
    for raw in raw_emails:
        sender = raw["sender_email"]

        # Kiem tra da xu ly email nay chua (theo message_id — global, khong chi per-RFQ)
        # Tranh truong hop cung 1 email bi gan cho nhieu RFQ khac nhau
        existing = db.query(VendorResponse).filter(
            VendorResponse.email_message_id == raw["message_id"],
        ).first()

        if existing:
            continue

        # Tao vendor response record
        vr = VendorResponse(
            rfq_id=rfq_id,
            vendor_email=sender,
            vendor_name=raw.get("sender_name", ""),
            email_subject=raw["subject"],
            email_body=raw["body"],
            email_received_at=datetime.utcnow(),
            email_message_id=raw["message_id"],
            status=VendorResponseStatus.RECEIVED,
        )

        # Xu ly attachment
        if raw.get("attachments"):
            att = raw["attachments"][0]  # Lay file dau tien
            vr.has_attachment = True
            vr.attachment_filename = att["filename"]
            vr.attachment_path = att["filepath"]

        db.add(vr)
        db.flush()

        # Trich xuat du lieu tu email bang AI pipeline (reasoning=high)
        try:
            normalized = extract_and_normalize(raw["body"], sender)
            if normalized:
                extraction = normalized["extraction"]
                vr.unit_price = extraction.unit_price
                vr.currency = extraction.currency
                vr.lead_time_days = extraction.lead_time_days
                vr.payment_terms = extraction.payment_terms
                vr.confidence_score = extraction.confidence_score
                vr.unit_price_usd = normalized["unit_price_usd"]

                vr.status = VendorResponseStatus.EXTRACTED
                logger.info(
                    "[RFQ #%d] Extracted | vendor=%s | %.2f %s (= %s USD) | %d days | conf=%.2f",
                    rfq_id, sender,
                    extraction.unit_price, extraction.currency,
                    vr.unit_price_usd, extraction.lead_time_days,
                    extraction.confidence_score,
                )

        except Exception as exc:
            logger.error("Extraction failed for email from %s: %s", sender, exc)
            vr.status = VendorResponseStatus.FAILED
            vr.extraction_error = str(exc)

        # Xu ly attachment (PDF, TXT...) qua General Document Loader
        if vr.has_attachment and vr.attachment_path:
            try:
                doc_text = load_document(vr.attachment_path)
                if doc_text:
                    logger.info(
                        "[RFQ #%d] Document loaded | file=%s | chars=%d",
                        rfq_id, vr.attachment_filename, len(doc_text),
                    )
                    contract = extract_contract_terms(doc_text)
                    if contract:
                        vr.incoterms = contract.incoterms
                        vr.penalty_clause = contract.penalty_clause
                        vr.validity = contract.validity
                        logger.info(
                            "[RFQ #%d] Contract extracted | incoterms=%s | penalty=%s | validity=%s",
                            rfq_id, contract.incoterms, contract.penalty_clause, contract.validity,
                        )
            except Exception as exc:
                logger.error(
                    "[RFQ #%d] Contract extraction failed for %s: %s",
                    rfq_id, vr.attachment_filename, exc,
                )

        # Log inbound email
        log = EmailLog(
            rfq_id=rfq_id,
            direction="inbound",
            sender=sender,
            recipient=None,
            subject=raw["subject"],
            body_preview=raw["body"][:200],
            message_id=raw["message_id"],
            status="received",
        )
        db.add(log)
        new_count += 1

    # Cap nhat trang thai RFQ
    total_responses = db.query(VendorResponse).filter(VendorResponse.rfq_id == rfq_id).count()
    total_vendors = db.query(Vendor).filter(Vendor.rfq_id == rfq_id).count()

    if total_responses > 0 and total_responses < total_vendors:
        rfq.status = RFQStatus.PARTIALLY_RESPONDED
    elif total_responses >= total_vendors:
        rfq.status = RFQStatus.COMPLETED

    rfq.updated_at = datetime.utcnow()
    db.commit()

    return {"rfq_id": rfq_id, "new_responses": new_count, "total_responses": total_responses}


# -------------------------------------------------------
# Tao bang so sanh vendor
# -------------------------------------------------------

def get_comparison_table(db: Session, rfq_id: int) -> Optional[ComparisonTable]:
    """Tao bang so sanh cac vendor response cho RFQ."""
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        return None

    responses = db.query(VendorResponse).filter(
        VendorResponse.rfq_id == rfq_id,
        VendorResponse.status == VendorResponseStatus.EXTRACTED,
    ).all()

    rows = []
    for r in responses:
        rows.append(ComparisonRow(
            vendor_name=r.vendor_name,
            vendor_email=r.vendor_email,
            unit_price_usd=r.unit_price_usd,
            lead_time_days=r.lead_time_days,
            payment_terms=r.payment_terms,
            confidence_score=r.confidence_score,
            incoterms=r.incoterms,
            penalty_clause=r.penalty_clause,
            validity=r.validity,
        ))

    return ComparisonTable(
        rfq_id=rfq_id,
        product=rfq.product,
        route=f"{rfq.origin} to {rfq.destination}",
        rows=rows,
    )
