import logging
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.database import get_db, SessionLocal
from backend.models import RFQ, VendorResponse
from backend.schemas import (
    RFQCreate, RFQResponse, RFQDetail, VendorOut, VendorResponseOut,
    EmailSendRequest, PollEmailRequest, ComparisonTable,
)
from backend.services.rfq_service import (
    create_rfq,
    send_rfq_to_vendors,
    poll_and_process_responses,
    get_comparison_table,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["RFQ"])
limiter = Limiter(key_func=get_remote_address)


# -------------------------------------------------------
# CRUD RFQ
# -------------------------------------------------------

@router.post("/rfq", response_model=RFQResponse, status_code=201)
@limiter.limit("10/minute")
def api_create_rfq(request: Request, rfq_in: RFQCreate, db: Session = Depends(get_db)):
    """Tao RFQ moi voi danh sach vendors."""
    rfq = create_rfq(db, rfq_in)
    return rfq


@router.get("/rfq", response_model=List[RFQResponse])
def api_list_rfqs(db: Session = Depends(get_db)):
    """Lay danh sach tat ca RFQ."""
    rfqs = db.query(RFQ).order_by(RFQ.created_at.desc()).all()
    return rfqs


@router.get("/rfq/{rfq_id}", response_model=RFQDetail)
def api_get_rfq(rfq_id: int, db: Session = Depends(get_db)):
    """Lay chi tiet RFQ, bao gom vendors va responses."""
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    return rfq


# -------------------------------------------------------
# Email operations
# -------------------------------------------------------

@router.post("/rfq/{rfq_id}/send")
@limiter.limit("5/minute")
def api_send_rfq_emails(request: Request, background_tasks: BackgroundTasks, rfq_id: int, db: Session = Depends(get_db)):
    """Gui email RFQ toi tat ca vendors (chay ngam qua BackgroundTasks)."""
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")

    def _run():
        bg_db = SessionLocal()
        try:
            result = send_rfq_to_vendors(bg_db, rfq_id)
            logger.info("Background send done: %s", result)
        except Exception as exc:
            logger.error("Background send failed for RFQ #%d: %s", rfq_id, exc)
        finally:
            bg_db.close()

    background_tasks.add_task(_run)
    return {"task_id": None, "status": "queued", "message": f"Sending emails for RFQ #{rfq_id} in background"}


@router.post("/rfq/{rfq_id}/poll")
@limiter.limit("5/minute")
def api_poll_responses(request: Request, background_tasks: BackgroundTasks, rfq_id: int, db: Session = Depends(get_db)):
    """Poll mailbox de lay email phan hoi tu vendors (chay ngam qua BackgroundTasks)."""
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")

    def _run():
        bg_db = SessionLocal()
        try:
            result = poll_and_process_responses(bg_db, rfq_id)
            logger.info("Background poll done: %s", result)
        except Exception as exc:
            logger.error("Background poll failed for RFQ #%d: %s", rfq_id, exc)
        finally:
            bg_db.close()

    background_tasks.add_task(_run)
    return {"task_id": None, "status": "queued", "message": f"Polling responses for RFQ #{rfq_id} in background"}


# -------------------------------------------------------
# Comparison / Dashboard
# -------------------------------------------------------

@router.get("/rfq/{rfq_id}/comparison", response_model=ComparisonTable)
def api_get_comparison(rfq_id: int, db: Session = Depends(get_db)):
    """Lay bang so sanh cac vendor responses cho RFQ."""
    table = get_comparison_table(db, rfq_id)
    if not table:
        raise HTTPException(status_code=404, detail="RFQ not found or no responses")
    return table


# -------------------------------------------------------
# Vendor responses (chi tiet)
# -------------------------------------------------------

@router.get("/rfq/{rfq_id}/responses", response_model=List[VendorResponseOut])
def api_get_responses(rfq_id: int, db: Session = Depends(get_db)):
    """Lay danh sach phan hoi vendor cho RFQ."""
    responses = db.query(VendorResponse).filter(
        VendorResponse.rfq_id == rfq_id
    ).order_by(VendorResponse.created_at.desc()).all()
    return responses


@router.get("/debug/smtp")
def api_debug_smtp():
    """Test SMTP connection va tra ve ket qua (chi dung de debug)."""
    import smtplib
    from backend.config import settings
    try:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
        server.ehlo()
        server.starttls()
        server.login(settings.smtp_username, settings.smtp_password)
        server.quit()
        return {
            "status": "ok",
            "smtp_host": settings.smtp_host,
            "smtp_port": settings.smtp_port,
            "smtp_username": settings.smtp_username,
            "smtp_from_email": settings.smtp_from_email,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc), "smtp_username": settings.smtp_username}
