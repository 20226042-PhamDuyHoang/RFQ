import logging

from backend.celery_app import celery_app
from backend.database import SessionLocal
from backend.services.rfq_service import send_rfq_to_vendors, poll_and_process_responses

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.send_rfq_emails", bind=True, max_retries=2)
def task_send_rfq_emails(self, rfq_id: int):
    """
    Task async: gui email RFQ toi vendors.
    Chay trong background de khong block API response.
    """
    db = SessionLocal()
    try:
        result = send_rfq_to_vendors(db, rfq_id)
        logger.info("Task send_rfq_emails completed for RFQ #%d: %s", rfq_id, result)
        return result
    except Exception as exc:
        logger.error("Task send_rfq_emails failed for RFQ #%d: %s", rfq_id, exc)
        self.retry(exc=exc, countdown=30)
    finally:
        db.close()


@celery_app.task(name="tasks.poll_vendor_responses", bind=True, max_retries=2)
def task_poll_vendor_responses(self, rfq_id: int):
    """
    Task async: poll mailbox va xu ly email phan hoi.
    Co the duoc schedule dinh ky hoac trigger thu cong.
    """
    db = SessionLocal()
    try:
        result = poll_and_process_responses(db, rfq_id)
        logger.info("Task poll_vendor_responses completed for RFQ #%d: %s", rfq_id, result)
        return result
    except Exception as exc:
        logger.error("Task poll_vendor_responses failed for RFQ #%d: %s", rfq_id, exc)
        self.retry(exc=exc, countdown=30)
    finally:
        db.close()


@celery_app.task(name="tasks.poll_all_active_rfqs")
def task_poll_all_active_rfqs():
    """
    Task dinh ky: poll email cho tat ca RFQ dang cho phan hoi.
    Dung voi Celery Beat scheduler.
    """
    from backend.models import RFQ, RFQStatus

    db = SessionLocal()
    try:
        active_rfqs = db.query(RFQ).filter(
            RFQ.status.in_([RFQStatus.SENT, RFQStatus.PARTIALLY_RESPONDED])
        ).all()

        for rfq in active_rfqs:
            task_poll_vendor_responses.delay(rfq.id)

        logger.info("Scheduled polling for %d active RFQs", len(active_rfqs))
    finally:
        db.close()
