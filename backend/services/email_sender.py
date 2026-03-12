import smtplib
import logging
import email.utils
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import requests as http_requests

from backend.config import settings

logger = logging.getLogger(__name__)


def build_rfq_subject(product: str, origin: str, destination: str) -> str:
    """
    Tao subject line chuan theo format: RFQ - [Product] - [Origin] to [Destination].
    Subject nay duoc dung de loc email phan hoi qua IMAP.
    """
    return f"RFQ - {product} - {origin} to {destination}"


def send_email(
    to_email: str,
    subject: str,
    body_html: str,
    body_text: Optional[str] = None,
    extra_headers: Optional[dict] = None,
) -> dict:
    """
    Gui email that.
    - Neu SENDGRID_API_KEY duoc set -> dung SendGrid HTTP API (khong bi Render block).
    - Fallback: SMTP truc tiep.
    Tra ve dict: {status, message_id, error?}
    """
    domain = settings.smtp_from_email.split("@")[-1] if "@" in settings.smtp_from_email else "rfq.system"
    message_id = email.utils.make_msgid(domain=domain)

    # --- SendGrid path ---
    if settings.sendgrid_api_key:
        return _send_via_sendgrid(to_email, subject, body_html, body_text, message_id)

    # --- SMTP path ---
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if extra_headers:
        for key, value in extra_headers.items():
            msg[key] = str(value)
    if body_text:
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        if settings.smtp_use_tls:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
            server.ehlo()
            server.starttls()
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        server.login(settings.smtp_username, settings.smtp_password)
        server.sendmail(settings.smtp_from_email, to_email, msg.as_string())
        server.quit()
        logger.info("[SMTP] Sent | to=%s | subject=%s", to_email, subject)
        return {"status": "sent", "message_id": message_id}
    except smtplib.SMTPAuthenticationError as exc:
        logger.error("SMTP auth failed: %s", exc)
        return {"status": "failed", "error": f"Authentication error: {exc}"}
    except Exception as exc:
        logger.error("Unexpected error sending email to %s: %s", to_email, exc)
        return {"status": "failed", "error": str(exc)}


def _send_via_sendgrid(to_email: str, subject: str, body_html: str, body_text: Optional[str], message_id: str) -> dict:
    """Gui email qua SendGrid HTTP API (khong can SMTP, khong bi Render block)."""
    content = [{"type": "text/html", "value": body_html or " "}]
    if body_text:
        content.insert(0, {"type": "text/plain", "value": body_text})
    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": settings.smtp_from_email, "name": settings.smtp_from_name},
        "subject": subject,
        "content": content,
    }
    try:
        resp = http_requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {settings.sendgrid_api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if resp.status_code == 202:
            logger.info("[SendGrid] Sent | to=%s | subject=%s", to_email, subject)
            return {"status": "sent", "message_id": message_id}
        else:
            logger.error("[SendGrid] Failed | status=%d | body=%s", resp.status_code, resp.text)
            return {"status": "failed", "error": f"SendGrid {resp.status_code}: {resp.text}"}
    except Exception as exc:
        logger.error("[SendGrid] Exception: %s", exc)
        return {"status": "failed", "error": str(exc)}


def send_rfq_emails(rfq_data: dict, vendors: list, email_bodies: dict, rfq_id: int = None) -> list:
    """
    Gui email RFQ toi danh sach vendors.
    - Moi vendor nhan email voi noi dung rieng (da duoc LLM personalize).
    - Dinh kem header X-RFQ-ID de IMAP co the loc chinh xac phan hoi.
    email_bodies: dict mapping vendor_email -> {"html": ..., "text": ...}
    Tra ve danh sach ket qua gui.
    """
    subject = build_rfq_subject(
        rfq_data["product"],
        rfq_data["origin"],
        rfq_data["destination"],
    )

    extra_headers = {"X-RFQ-ID": str(rfq_id)} if rfq_id else {}

    results = []
    for vendor in vendors:
        vendor_email = vendor["email"]
        body = email_bodies.get(vendor_email, {})

        result = send_email(
            to_email=vendor_email,
            subject=subject,
            body_html=body.get("html", ""),
            body_text=body.get("text", ""),
            extra_headers=extra_headers,
        )
        result["vendor_email"] = vendor_email
        result["vendor_name"] = vendor.get("name", "")
        results.append(result)

    return results
