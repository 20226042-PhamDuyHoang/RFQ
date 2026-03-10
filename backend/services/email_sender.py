import smtplib
import logging
import email.utils
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

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
    Gui email that qua SMTP.
    - Gan Message-ID chuan de ho tro threading khi IMAP poll.
    - extra_headers cho phep them header tuy chinh (vd: X-RFQ-ID).
    Tra ve dict: {status, message_id, error?}
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject

    # Tao Message-ID chuan RFC 2822 de IMAP co the loc theo In-Reply-To / References
    domain = settings.smtp_from_email.split("@")[-1] if "@" in settings.smtp_from_email else "rfq.system"
    message_id = email.utils.make_msgid(domain=domain)
    msg["Message-ID"] = message_id

    # Header tuy chinh (vd: X-RFQ-ID de filter ben IMAP)
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

        logger.info(
            "[SMTP] Sent | to=%s | subject=%s | message_id=%s",
            to_email, subject, message_id,
        )
        return {"status": "sent", "message_id": message_id}

    except smtplib.SMTPAuthenticationError as exc:
        logger.error("SMTP auth failed: %s", exc)
        return {"status": "failed", "error": f"Authentication error: {exc}"}

    except smtplib.SMTPException as exc:
        logger.error("SMTP error sending to %s: %s", to_email, exc)
        return {"status": "failed", "error": str(exc)}

    except Exception as exc:
        logger.error("Unexpected error sending email to %s: %s", to_email, exc)
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
