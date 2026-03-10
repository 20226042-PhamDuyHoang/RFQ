import json
import logging
import re
from typing import Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from backend.config import settings
from backend.schemas import ExtractionResult, ContractExtraction
from backend.services.currency_converter import convert_to_usd
from backend.services.document_loader import find_relevant_sections

logger = logging.getLogger(__name__)

client = OpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
)


# -------------------------------------------------------
# Email generation - tao noi dung email RFQ ca nhan hoa
# -------------------------------------------------------

GENERATE_EMAIL_PROMPT = """You are an assistant for a B2B logistics company.
Write a professional RFQ (Request for Quotation) email to a vendor.

RFQ details:
- Product: {product}
- Quantity: {quantity}
- Route: {origin} to {destination}
- Required Delivery Date: {delivery_date}
- Special Notes: {special_notes}

Vendor name: {vendor_name}

Sender info (use in the sign-off):
- Name: {sender_name}
- Position: {sender_position}
- Company: {sender_company}
- Phone: {sender_phone}
- Email: {sender_email}

Requirements:
- Be concise, professional, and direct.
- Include all RFQ details clearly.
- Ask the vendor to reply with: unit price, currency, lead time, payment terms.
- Mention that they can also attach a contract/terms document if available.
- Do NOT use any emojis.
- Write in English.
- Return ONLY the email body (no subject line).
"""


def _generate_email_llm(
    prompt: str, vendor_name: str,
) -> dict:
    """
    Internal: goi LLM de tao email. Duoc boc @retry rieng
    de co the retry khi LLM loi ma khong anh huong fallback.
    """
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "You are a professional logistics email writer."},
            {"role": "user", "content": prompt},
        ],
        temperature=1,
        max_tokens=800,
    )

    text_body = response.choices[0].message.content.strip()
    html_body = text_body.replace("\n", "<br>\n")

    logger.info(
        "LLM generated email for vendor '%s' (%d chars)",
        vendor_name, len(text_body),
    )
    logger.debug("LLM email body for '%s':\n%s", vendor_name, text_body[:500])
    return {"html": html_body, "text": text_body}


def generate_rfq_email(
    product: str,
    quantity: int,
    origin: str,
    destination: str,
    delivery_date: str,
    special_notes: str,
    vendor_name: str,
) -> dict:
    """
    Dung LLM de tao noi dung email RFQ ca nhan hoa cho tung vendor.
    Retry toi da 3 lan neu LLM loi. Fallback sang template neu van that bai.
    Tra ve {"html": ..., "text": ...}
    """
    prompt = GENERATE_EMAIL_PROMPT.format(
        product=product,
        quantity=quantity,
        origin=origin,
        destination=destination,
        delivery_date=delivery_date or "ASAP",
        special_notes=special_notes or "None",
        vendor_name=vendor_name,
        sender_name=settings.sender_name,
        sender_position=settings.sender_position,
        sender_company=settings.sender_company,
        sender_phone=settings.sender_phone or "N/A",
        sender_email=settings.smtp_from_email,
    )

    # Retry LLM toi da 3 lan voi exponential backoff
    from tenacity import retry, stop_after_attempt, wait_exponential
    retrying_llm = retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )(_generate_email_llm)

    try:
        return retrying_llm(prompt, vendor_name)
    except Exception as exc:
        logger.error("LLM error generating email for '%s' after retries: %s", vendor_name, exc)
        logger.warning("Falling back to template email for vendor '%s'", vendor_name)
        fallback_text = (
            f"Dear {vendor_name},\n\n"
            f"We would like to request a quotation for the following:\n\n"
            f"Product: {product}\n"
            f"Quantity: {quantity}\n"
            f"Route: {origin} to {destination}\n"
            f"Delivery Date: {delivery_date or 'ASAP'}\n"
            f"Notes: {special_notes or 'N/A'}\n\n"
            f"Please reply with your unit price, currency, lead time, and payment terms.\n\n"
            f"Best regards,\nRFQ Automation System"
        )
        return {"html": fallback_text.replace("\n", "<br>\n"), "text": fallback_text}


# -------------------------------------------------------
# Email extraction - trich xuat du lieu tu email phan hoi
# -------------------------------------------------------

EXTRACTION_PROMPT = """You are a senior data extraction specialist for a logistics company.
Your job is to extract structured quotation data from vendor email responses.

Think step-by-step:

STEP 1 - Identify the raw price:
  Look for numbers near words like "price", "rate", "cost", "offer", "quotation", "$", "USD", "EUR", "JPY".
  Handle European formats: "2.350,00" means 2350.00 (dot = thousands, comma = decimal).
  If multiple prices exist, pick the PRIMARY offer (not alternative/discount options).

STEP 2 - Identify currency:
  Look for explicit codes (USD, EUR, JPY, VND...) or symbols ($, E, Y).
  If mixed currencies, use the one attached to the main price.
  Default to "USD" only if absolutely no currency is indicated.

STEP 3 - Convert lead time to DAYS (integer):
  "48 hours" = 2 days.
  "3 days" = 3 days.
  "2-3 weeks" = upper bound = 3 * 7 = 21 days.
  "three to four weeks" = 4 * 7 = 28 days.
  "about 2 and a half weeks" = ceil(2.5 * 7) = 18 days.
  Always use the UPPER BOUND for ranges.

STEP 4 - Extract payment terms:
  "Net 30", "50% deposit", "15 days after invoice" etc.
  Copy the original terms as closely as possible.

STEP 5 - Confidence score:
  0.90-1.00: all fields clearly labeled and unambiguous.
  0.70-0.89: most fields clear but some inference needed.
  0.50-0.69: significant assumptions required, messy format.
  below 0.50: very unreliable, mostly guessing.

Email body:
---
{email_body}
---

Sender email: {vendor_email}

Return ONLY a valid JSON object with these exact fields:
{{"vendor_email": "...", "unit_price": <float>, "currency": "...", "lead_time_days": <int>, "payment_terms": "...", "confidence_score": <float>}}

No markdown fences, no explanation, no extra text. ONLY the JSON object.
"""


def _get_llm_text(response) -> str:
    """
    Lay text tu LLM response. Neu content la None (xay ra khi reasoning_effort='high'
    khien model bo het output vao reasoning field), thi parse JSON tu reasoning.
    """
    msg = response.choices[0].message
    content = msg.content
    if content is not None:
        return content

    # Fallback: trich xuat JSON tu reasoning field
    extra = getattr(msg, 'model_extra', {}) or {}
    reasoning = extra.get('reasoning', '')
    if reasoning:
        logger.info("[LLM] content=None, extracting JSON from reasoning (%d chars)", len(reasoning))
        # Tim tat ca JSON objects trong reasoning
        matches = list(re.finditer(r'\{[^{}]*\}', reasoning, re.DOTALL))
        if matches:
            return matches[-1].group()
        # Brute-force: tim cap ngoac nhon cuoi cung
        brace_start = reasoning.rfind('{')
        brace_end = reasoning.rfind('}')
        if brace_start != -1 and brace_end > brace_start:
            return reasoning[brace_start:brace_end + 1]

    raise ValueError("LLM returned no content and no JSON found in reasoning field")


def _clean_llm_json(raw: str) -> str:
    """
    Lam sach output tu LLM: bo markdown fences, trailing text, tim JSON object.
    """
    text = raw.strip()

    # Bo markdown code block
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

    # Tim JSON object dau tien trong text (phong truong hop LLM them text thua)
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        return match.group(0)

    return text


def _validate_extraction(data: dict, vendor_email: str) -> ExtractionResult:
    """
    Validate va normalize du lieu trich xuat thanh ExtractionResult.
    Dam bao moi field co gia tri hop le.
    """
    # Ep kieu an toan
    unit_price = float(data.get("unit_price", 0))
    lead_time_days = int(data.get("lead_time_days", 0))
    confidence = float(data.get("confidence_score", 0.5))

    # Clamp confidence ve [0, 1]
    confidence = max(0.0, min(1.0, confidence))

    # Gia phai duong
    if unit_price <= 0:
        logger.warning("[EXTRACT] unit_price <= 0 (%.2f), setting confidence low", unit_price)
        confidence = min(confidence, 0.3)

    # Lead time phai duong
    if lead_time_days <= 0:
        logger.warning("[EXTRACT] lead_time_days <= 0 (%d), setting confidence low", lead_time_days)
        confidence = min(confidence, 0.3)

    currency = str(data.get("currency", "USD")).upper().strip()
    if currency not in ("USD", "EUR", "GBP", "JPY", "CNY", "KRW", "VND", "THB", "SGD", "AUD"):
        logger.warning("[EXTRACT] Unknown currency '%s', defaulting to USD", currency)
        currency = "USD"

    payment_terms = str(data.get("payment_terms", "N/A")).strip()
    if not payment_terms:
        payment_terms = "N/A"

    return ExtractionResult(
        vendor_email=data.get("vendor_email", vendor_email),
        unit_price=unit_price,
        currency=currency,
        lead_time_days=lead_time_days,
        payment_terms=payment_terms,
        confidence_score=round(confidence, 2),
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((json.JSONDecodeError, KeyError, ValueError, AttributeError)),
    before_sleep=lambda retry_state: logger.warning(
        "[EXTRACT] Retry %d/3 after error: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception(),
    ),
)
def extract_quotation_from_email(email_body: str, vendor_email: str) -> Optional[ExtractionResult]:
    """
    Dung LLM (reasoning=high) de trich xuat du lieu bao gia tu email vendor.

    Pipeline:
    1. Gui email body cho LLM voi reasoning_effort="high"
    2. Parse JSON output
    3. Validate + normalize (currency, lead_time -> days)
    4. Retry toi da 3 lan neu JSON khong hop le

    Tra ve ExtractionResult da normalized, hoac None neu that bai hoan toan.
    """
    prompt = EXTRACTION_PROMPT.format(
        email_body=email_body,
        vendor_email=vendor_email,
    )

    logger.info("[EXTRACT] Calling LLM with reasoning=high for vendor: %s", vendor_email)

    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a precise data extraction tool. "
                    "Return ONLY valid JSON, nothing else."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=1024,
        extra_body={"reasoning_effort": "high"},
    )

    raw = _get_llm_text(response).strip()
    logger.debug("[EXTRACT] Raw LLM output:\n%s", raw[:800])

    # Parse JSON
    cleaned = _clean_llm_json(raw)
    data = json.loads(cleaned)

    # Validate
    result = _validate_extraction(data, vendor_email)

    logger.info(
        "[EXTRACT] Success | vendor=%s | price=%.2f %s | lead=%d days | confidence=%.2f",
        result.vendor_email,
        result.unit_price,
        result.currency,
        result.lead_time_days,
        result.confidence_score,
    )

    return result


def extract_and_normalize(email_body: str, vendor_email: str) -> Optional[dict]:
    """
    Pipeline hoan chinh: Extract + Normalize (quy doi USD + days).
    Tra ve dict voi unit_price_usd da duoc quy doi.
    Dung cho rfq_service de luu vao DB.
    """
    result = extract_quotation_from_email(email_body, vendor_email)
    if not result:
        return None

    # Quy doi gia ve USD
    unit_price_usd = convert_to_usd(result.unit_price, result.currency)
    if unit_price_usd is None:
        logger.warning(
            "[NORMALIZE] Cannot convert %.2f %s to USD for %s",
            result.unit_price, result.currency, vendor_email,
        )

    return {
        "extraction": result,
        "unit_price_usd": unit_price_usd,
    }


# -------------------------------------------------------
# Contract extraction - trich xuat tu noi dung PDF
# -------------------------------------------------------

CONTRACT_EXTRACTION_PROMPT = """Extract key contract terms from the following document sections.

For each field:
- incoterms: Standard Incoterms 2020 code (EXW, FCA, FAS, FOB, CFR, CIF, CPT, CIP, DAP, DPU, DDP). Return null if not found.
- penalty_clause: Summarize the FULL penalty terms including rate, calculation basis, and maximum cap. Look for penalties, liquidated damages, SLA violations, delay fees, late delivery charges. Return null ONLY if no penalty is mentioned.
- validity: Offer validity period, expiry date, or acceptance deadline. Return null if not found.

Document sections:
---
{document_text}
---

Return ONLY a valid JSON object:
{{"incoterms": "...", "penalty_clause": "...", "validity": "..."}}

Use null for missing fields. No markdown fences, no explanation.
"""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((json.JSONDecodeError, KeyError, ValueError, AttributeError)),
    before_sleep=lambda retry_state: logger.warning(
        "[CONTRACT] Retry %d/3 after error: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception(),
    ),
)
def extract_contract_terms(document_text: str) -> Optional[ContractExtraction]:
    """
    Trich xuat dieu khoan hop dong tu noi dung document (PDF/text).
    Su dung reasoning_effort="high" de LLM phan tich ky.
    Retry toi da 3 lan neu JSON khong hop le.
    """
    if not document_text or len(document_text.strip()) < 20:
        logger.warning("[CONTRACT] Document text too short (%d chars), skipping", len(document_text or ""))
        return None

    # Doc ngan -> gui nguyen; doc dai -> keyword-guided chunking
    relevant_text = find_relevant_sections(document_text, max_output_chars=6000)
    prompt = CONTRACT_EXTRACTION_PROMPT.format(document_text=relevant_text)

    logger.info(
        "[CONTRACT] Calling LLM | original=%d chars | filtered=%d chars",
        len(document_text), len(relevant_text),
    )

    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a precise legal document analysis tool. "
                    "Return ONLY valid JSON, nothing else."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=800,
    )

    raw = response.choices[0].message.content.strip()
    logger.debug("[CONTRACT] Raw LLM output:\n%s", raw[:500])

    cleaned = _clean_llm_json(raw)
    data = json.loads(cleaned)

    result = ContractExtraction(**data)

    logger.info(
        "[CONTRACT] Extracted | incoterms=%s | penalty=%s | validity=%s",
        result.incoterms, result.penalty_clause, result.validity,
    )

    return result
