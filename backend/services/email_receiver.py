import os
import email
import logging
import imaplib
from email.header import decode_header
from typing import List, Optional

from backend.config import settings

logger = logging.getLogger(__name__)

ATTACHMENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "attachments")
os.makedirs(ATTACHMENTS_DIR, exist_ok=True)


def decode_header_value(value: str) -> str:
    """Decode header field (co the bi encoded nhu UTF-8, ISO-8859...)."""
    if value is None:
        return ""
    decoded_parts = decode_header(value)
    result = ""
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result += part.decode(charset or "utf-8", errors="replace")
        else:
            result += part
    return result


def get_email_body(msg) -> str:
    """Lay noi dung text tu email (uu tien plain text, fallback sang html)."""
    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))

            # Bo qua attachment
            if "attachment" in disposition:
                continue

            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
            elif content_type == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")

    return body


def save_attachments(msg, rfq_id: int) -> List[dict]:
    """
    Luu cac file dinh kem (pdf, docx...) vao thu muc attachments.
    Tra ve danh sach dict {filename, filepath}.
    """
    saved = []
    rfq_dir = os.path.join(ATTACHMENTS_DIR, str(rfq_id))
    os.makedirs(rfq_dir, exist_ok=True)

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        if "attachment" not in disposition:
            continue

        filename = part.get_filename()
        if filename:
            filename = decode_header_value(filename)
            # Lam sach ten file
            safe_name = "".join(c for c in filename if c.isalnum() or c in ".-_ ")
            filepath = os.path.join(rfq_dir, safe_name)

            with open(filepath, "wb") as f:
                f.write(part.get_payload(decode=True))

            saved.append({"filename": safe_name, "filepath": filepath})
            logger.info("Saved attachment: %s", filepath)

    return saved


def extract_sender_email(from_header: str) -> str:
    """
    Trich email address tu header 'From'.
    Ho tro ca 2 dang:
      - "John Smith <john@example.com>"
      - "john@example.com"
    """
    if "<" in from_header and ">" in from_header:
        return from_header.split("<")[1].split(">")[0].strip().lower()
    return from_header.strip().lower()


# -------------------------------------------------------
# IMAP connection helper
# -------------------------------------------------------

def _connect_imap() -> imaplib.IMAP4:
    """Tao ket noi IMAP voi SSL hoac plain tuy config."""
    if settings.imap_use_ssl:
        conn = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
    else:
        conn = imaplib.IMAP4(settings.imap_host, settings.imap_port)
    conn.login(settings.imap_username, settings.imap_password)
    return conn


# -------------------------------------------------------
# Multi-strategy IMAP search
# -------------------------------------------------------

def _search_ids(mail: imaplib.IMAP4, criteria: str) -> set:
    """
    Thuc hien IMAP SEARCH va tra ve set message IDs (bytes).
    Tra ve set rong neu loi hoac khong co ket qua.
    """
    try:
        status, data = mail.search(None, criteria)
        if status == "OK" and data[0]:
            return set(data[0].split())
    except Exception as exc:
        logger.warning("[IMAP] Search failed | criteria='%s' | error=%s", criteria, exc)
    return set()


def _build_since_clause(since_date=None) -> str:
    """
    Tao menh de SINCE cho IMAP search.
    Chi lay email tu ngay RFQ duoc tao, tranh quet toan bo inbox.
    """
    if since_date is None:
        return ""
    # IMAP date format: 07-Mar-2026
    return since_date.strftime(" SINCE %d-%b-%Y")


def _collect_candidate_ids(
    mail: imaplib.IMAP4,
    subject_keyword: str,
    vendor_emails: Optional[List[str]],
    sent_message_ids: Optional[List[str]],
    since_date=None,
) -> set:
    """
    Thu thap ung vien message ID bang 3 chien luoc doc lap.
    Tat ca chien luoc deu gioi han: SINCE ngay tao RFQ
    de tranh quet toan bo inbox (gay timeout).

    KHONG dung UNSEEN — viec chong trung lap nho message_id dedup
    o tang rfq_service. Dieu nay dam bao email da doc (qua Gmail web
    hoac bi auto-SEEN boi fetch truoc) van duoc tim thay.

    Chien luoc 1 - Subject keyword
    Chien luoc 2 - Sender match (tu vendor da biet)
    Chien luoc 3 - Thread ID (In-Reply-To / References)

    Ket qua la UNION cua 3 tap; buoc loc sau se loai email khong lien quan.
    """
    candidates: set = set()
    since = _build_since_clause(since_date)

    # --- Chien luoc 1: Subject + SINCE ---
    by_subject = _search_ids(mail, f'SUBJECT "{subject_keyword}"{since}')
    candidates |= by_subject
    logger.debug(
        "[IMAP] Strategy 1 (subject='%s'): %d matches",
        subject_keyword, len(by_subject),
    )

    # --- Chien luoc 2: Sender + SINCE ---
    if vendor_emails:
        by_sender: set = set()
        for ve in vendor_emails:
            by_sender |= _search_ids(mail, f'FROM "{ve}"{since}')
        candidates |= by_sender
        logger.debug(
            "[IMAP] Strategy 2 (sender, %d vendors): %d matches",
            len(vendor_emails), len(by_sender),
        )

    # --- Chien luoc 3: Thread (In-Reply-To / References) ---
    if sent_message_ids:
        by_thread: set = set()
        for mid in sent_message_ids:
            clean_mid = mid.strip("<>")
            by_thread |= _search_ids(mail, f'HEADER In-Reply-To "{clean_mid}"{since}')
            by_thread |= _search_ids(mail, f'HEADER References "{clean_mid}"{since}')
        candidates |= by_thread
        logger.debug(
            "[IMAP] Strategy 3 (thread, %d sent IDs): %d matches",
            len(sent_message_ids), len(by_thread),
        )

    return candidates


# -------------------------------------------------------
# Public API
# -------------------------------------------------------

def poll_emails_for_rfq(
    rfq_id: int,
    subject_keyword: str,
    vendor_emails: Optional[List[str]] = None,
    sent_message_ids: Optional[List[str]] = None,
    since_date=None,
) -> List[dict]:
    """
    Poll mailbox IMAP va tra ve cac email phan hoi lien quan den RFQ.

    Su dung 3 chien luoc de khong bo sot reply:
    1. Subject chua keyword
    2. Email tu vendor da biet
    3. Email la reply theo chuan RFC (In-Reply-To / References)

    Khong dung UNSEEN (tranh mat email da doc qua Gmail hoac bi auto-SEEN
    boi IMAP fetch truoc do). Chong trung lap bang message_id dedup o rfq_service.
    Chi quet email SINCE ngay RFQ duoc tao de giam tai.
    Sau khi accept, danh dau email la SEEN.

    Args:
        rfq_id:            ID cua RFQ (dung de luu attachment).
        subject_keyword:   Chuoi can khop trong Subject.
        vendor_emails:     Danh sach email vendor da gui RFQ.
        sent_message_ids:  Message-ID da gui ra (de filter theo thread).
        since_date:        Chi quet email tu ngay nay tro di.

    Returns:
        Danh sach dict chua thong tin email da parse.
    """
    results = []

    try:
        mail = _connect_imap()
        mail.select(settings.imap_mailbox)

        candidate_ids = _collect_candidate_ids(
            mail, subject_keyword, vendor_emails, sent_message_ids,
            since_date=since_date,
        )

        if not candidate_ids:
            logger.info("[IMAP] No candidate emails for RFQ #%d", rfq_id)
            mail.logout()
            return results

        logger.info(
            "[IMAP] Processing %d candidates for RFQ #%d",
            len(candidate_ids), rfq_id,
        )

        vendor_email_set = {e.lower() for e in (vendor_emails or [])}
        our_email = settings.imap_username.lower()

        for msg_id in sorted(candidate_ids):
            fetch_status, msg_data = mail.fetch(msg_id, "(BODY.PEEK[])")
            if fetch_status != "OK":
                logger.warning("[IMAP] Failed to fetch msg_id=%s", msg_id)
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            from_header = decode_header_value(msg.get("From", ""))
            sender_email = extract_sender_email(from_header)
            subject = decode_header_value(msg.get("Subject", ""))
            message_id = msg.get("Message-ID", "").strip()
            in_reply_to = msg.get("In-Reply-To", "").strip()
            references = msg.get("References", "").strip()
            date_str = msg.get("Date", "")

            # Bo qua email chinh chuong tu gui di (outbound)
            if sender_email == our_email:
                continue

            # Neu co vendor whitelist: chi chap nhan email tu vendor da biet
            # (chong nhan thu rac hoac email khong lien quan)
            if vendor_email_set and sender_email not in vendor_email_set:
                logger.debug(
                    "[IMAP] Skipped unknown sender | from=%s", sender_email
                )
                continue

            body = get_email_body(msg)
            attachments = save_attachments(msg, rfq_id)

            sender_name = ""
            if "<" in from_header:
                sender_name = from_header.split("<")[0].strip().strip('"')

            results.append({
                "sender_email": sender_email,
                "sender_name": sender_name,
                "subject": subject,
                "body": body,
                "message_id": message_id,
                "in_reply_to": in_reply_to,
                "references": references,
                "date": date_str,
                "attachments": attachments,
            })

            # Danh dau da doc SAU KHI accept thanh cong
            # (tranh mat email neu bi skip hoac loi truoc do)
            try:
                mail.store(msg_id, "+FLAGS", "\\Seen")
            except Exception as exc:
                logger.warning("[IMAP] Failed to mark SEEN msg_id=%s: %s", msg_id, exc)

            logger.info(
                "[IMAP] Accepted | from=%s | subject=%s | attachments=%d",
                sender_email, subject, len(attachments),
            )

        mail.logout()
        logger.info(
            "[IMAP] Done | rfq_id=%d | accepted=%d / candidates=%d",
            rfq_id, len(results), len(candidate_ids),
        )

    except imaplib.IMAP4.error as exc:
        logger.error("[IMAP] Auth/connection error for RFQ #%d: %s", rfq_id, exc)
    except Exception as exc:
        logger.exception("[IMAP] Unexpected error for RFQ #%d: %s", rfq_id, exc)

    return results
