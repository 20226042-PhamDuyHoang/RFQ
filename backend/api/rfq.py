import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.database import get_db
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
from backend.tasks.email_tasks import task_send_rfq_emails, task_poll_vendor_responses
from backend.celery_app import celery_app

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
def api_send_rfq_emails(request: Request, rfq_id: int, db: Session = Depends(get_db)):
    """Gui email RFQ toi tat ca vendors (async qua Celery, fallback sync)."""
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    try:
        task = task_send_rfq_emails.delay(rfq_id)
        return {"task_id": task.id, "status": "queued", "message": f"Sending emails for RFQ #{rfq_id}"}
    except Exception as broker_err:
        logger.warning("Celery broker unavailable (%s), falling back to sync", broker_err)
        result = send_rfq_to_vendors(db, rfq_id)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"task_id": None, "status": "completed", "message": "Sent synchronously", **result}


@router.post("/rfq/{rfq_id}/poll")
@limiter.limit("5/minute")
def api_poll_responses(request: Request, rfq_id: int, db: Session = Depends(get_db)):
    """Poll mailbox de lay email phan hoi tu vendors (async qua Celery, fallback sync)."""
    rfq = db.query(RFQ).filter(RFQ.id == rfq_id).first()
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    try:
        task = task_poll_vendor_responses.delay(rfq_id)
        return {"task_id": task.id, "status": "queued", "message": f"Polling responses for RFQ #{rfq_id}"}
    except Exception as broker_err:
        logger.warning("Celery broker unavailable (%s), falling back to sync", broker_err)
        result = poll_and_process_responses(db, rfq_id)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"task_id": None, "status": "completed", "message": "Polled synchronously", **result}


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


@router.get("/task/{task_id}")
def api_get_task_status(task_id: str):
    """Kiem tra trang thai cua Celery task."""
    result = celery_app.AsyncResult(task_id)
    response = {"task_id": task_id, "status": result.status}
    if result.ready():
        response["result"] = result.result
    return response
