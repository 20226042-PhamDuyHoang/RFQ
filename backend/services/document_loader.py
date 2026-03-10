"""
General Document Loader
-----------------------
Xu ly mixed PDF (text-selectable + scan/image) tu dong theo tung trang.

Pipeline per page:
  1. pdfplumber extract text (nhanh, ~1ms/page)
  2. Neu text < SPARSE_THRESHOLD chars -> trang la scan/image
  3. Convert trang do sang image (pdf2image / poppler)
  4. Chay pytesseract OCR tren image
  5. Ghep tat ca trang lai

Keyword-guided chunk retrieval (cho doc dai):
  - Split document thanh page-level chunks
  - Score moi chunk theo keyword relevance
  - Tra ve top-K chunks phu hop nhat
  - 1 LLM call voi context da loc -> chinh xac + tiet kiem token

Supported formats: .pdf (primary), mo rong duoc cho .docx, .xlsx sau.
"""

import logging
import os
import re
from typing import Optional, List, Tuple

import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

# Nguong ky tu toi thieu de coi trang la "co text".
# Duoi nguong nay -> chuyen sang OCR.
SPARSE_THRESHOLD = 50

# Nguong chars: neu document ngan hon -> gui nguyen cho LLM.
# Neu dai hon -> dung keyword-guided chunking.
SHORT_DOC_LIMIT = 6000

# Keyword sets cho tung loai dieu khoan hop dong.
# Moi nhom chua cac tu/cum tu dac trung, case-insensitive.
CONTRACT_KEYWORDS = {
    "incoterms": [
        "incoterm", "incoterms", "exw", "fca", "fas", "fob", "cfr", "cif",
        "cpt", "cip", "dap", "dpu", "ddp", "ex works", "free on board",
        "cost insurance freight", "delivery terms", "shipping terms",
        "trade terms", "risk transfer", "point of delivery",
    ],
    "penalty": [
        "penalty", "penalties", "liquidated damages", "late delivery",
        "delay", "delayed", "compensation", "damages", "fee per day",
        "per week of delay", "not to exceed", "forfeiture", "deduction",
        "sla", "service level", "transit time", "calendar day",
        "business day", "freight value", "per day of delay",
        "accumulated penalty", "maximum penalty", "force majeure",
        "time is of the essence", "liable to pay", "calculation of penalty",
        "penalty for late", "penalty clause", "breach", "non-performance",
        "default", "indemnity", "indemnification",
    ],
    "validity": [
        "validity", "valid for", "valid until", "expires", "expiry",
        "expiration", "offer valid", "acceptance deadline", "open until",
        "quotation valid", "effective date", "term of agreement",
        "duration", "commencement date", "termination", "renewal",
        "agreement period", "contract period",
    ],
    "pricing": [
        "unit price", "total price", "freight charge", "price per",
        "cost breakdown", "surcharge", "tariff", "rate",
        "quotation", "billing",
    ],
}

# Regex cho section headers (ARTICLE, Roman numerals, SECTION/CLAUSE, numbered caps titles)
SECTION_HEADER_RE = re.compile(
    r'^('
    r'ARTICLE\s+\d+\s*[:.]?\s*.*'
    r'|[IVX]+\.\s+[A-Z].*'
    r'|SECTION\s+\d+\s*[:.]?\s*.*'
    r'|CLAUSE\s+\d+\s*[:.]?\s*.*'
    r'|\d+\.\s+[A-Z][A-Z\s&(),:/-]{5,}.*'
    r')$',
    re.MULTILINE,
)


# -------------------------------------------------------
# Internal: Text extraction strategies
# -------------------------------------------------------

def _extract_text_native(page) -> str:
    """
    Strategy 1: Trich xuat text bang pdfplumber (text-selectable PDF).
    Bao gom ca table extraction.
    Nhanh va chinh xac cho PDF co text layer.
    """
    parts = []

    # Text binh thuong
    text = page.extract_text()
    if text:
        parts.append(text.strip())

    # Table data (pdfplumber hieu table structure, pytesseract thi khong)
    for table in page.extract_tables():
        for row in table:
            if row:
                cells = [str(cell).strip() if cell else "" for cell in row]
                parts.append(" | ".join(cells))

    return "\n".join(parts)


def _extract_text_ocr(page_image: Image.Image) -> str:
    """
    Strategy 2: OCR bang pytesseract cho trang scan/image.
    Ap dung basic preprocessing de tang accuracy.
    """
    # Preprocessing: grayscale + sharpen -> tang do net cho OCR
    processed = page_image.convert("L")  # grayscale
    processed = processed.filter(ImageFilter.SHARPEN)

    text = pytesseract.image_to_string(processed, lang="eng")
    return text.strip()


def _is_text_sparse(text: str) -> bool:
    """
    Kiem tra trang co qua it text khong.
    Neu sparse -> trang la scan/image, can OCR.
    """
    if not text:
        return True
    # Bo khoang trang va dem ky tu thuc
    cleaned = text.replace(" ", "").replace("\n", "")
    return len(cleaned) < SPARSE_THRESHOLD


# -------------------------------------------------------
# Keyword-guided chunk retrieval
# -------------------------------------------------------

def _score_chunk(text: str, keywords: List[str]) -> float:
    """
    Score a chunk by keyword relevance.
    - Unique keyword matches (primary signal)
    - Bonus for structural markers like ARTICLE/Section headers
    """
    text_lower = text.lower()
    unique_hits = sum(1 for kw in keywords if kw in text_lower)

    # Bonus cho structural markers (Article headers chua dieu khoan quan trong)
    structure_bonus = len(re.findall(
        r'(?:article|section|clause)\s+\d', text_lower
    )) * 0.5

    return unique_hits + structure_bonus


def _split_into_chunks(
    full_text: str, chunk_size: int = 2000, overlap: int = 300
) -> List[Tuple[int, str]]:
    """
    Split text thanh cac chunk co overlap.
    Uu tien cat tai ranh gioi paragraph hoac section header.
    """
    if len(full_text) <= chunk_size:
        return [(0, full_text)]

    chunks = []
    start = 0
    idx = 0

    while start < len(full_text):
        end = start + chunk_size

        if end < len(full_text):
            # Uu tien cat tai section header (ARTICLE, Section...)
            header_match = None
            for m in re.finditer(r'\n(?=(?:ARTICLE|Section|Clause|CHAPTER)\s+\d)', full_text[start:end], re.IGNORECASE):
                header_match = m
            if header_match and (start + header_match.start()) > start + chunk_size // 3:
                end = start + header_match.start() + 1
            else:
                # Fallback: cat tai paragraph boundary
                split_pos = full_text.rfind("\n\n", start, end)
                if split_pos == -1 or split_pos <= start:
                    split_pos = full_text.rfind("\n", start, end)
                if split_pos > start:
                    end = split_pos + 1

        chunk_text = full_text[start:end].strip()
        if chunk_text:
            chunks.append((idx, chunk_text))
            idx += 1

        new_start = end - overlap if end < len(full_text) else end
        # Guarantee forward progress to avoid infinite loop
        if new_start <= start:
            new_start = start + max(1, chunk_size // 2)
        start = new_start

    return chunks


def split_by_sections(text: str) -> List[dict]:
    """
    Split document into sections based on ARTICLE/Section headers.
    Returns list of {"title": str, "body": str, "index": int}.
    Falls back to [{"title": "FULL_DOCUMENT", ...}] if no clear structure.
    """
    matches = list(SECTION_HEADER_RE.finditer(text))

    if len(matches) < 2:
        return [{"title": "FULL_DOCUMENT", "body": text, "index": 0}]

    sections = []
    idx = 0

    # Preamble (text before first header) - may contain Effective Date, parties
    if matches[0].start() > 100:
        preamble = text[:matches[0].start()].strip()
        sections.append({"title": "PREAMBLE", "body": preamble, "index": idx})
        idx += 1

    for i, match in enumerate(matches):
        title = match.group(0).strip()
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections.append({"title": title, "body": body, "index": idx})
        idx += 1

    return sections


def find_relevant_sections(
    full_text: str,
    max_output_chars: int = 6000,
    custom_keywords: Optional[List[str]] = None,
) -> str:
    """
    Article-based retrieval: split by section headers, then per-category
    keyword scoring to find the best section for each contract term category.
    Returns focused context for LLM extraction.
    """
    sections = split_by_sections(full_text)

    # Unstructured doc -> chunk-based fallback
    if len(sections) == 1 and sections[0]["title"] == "FULL_DOCUMENT":
        if len(full_text) <= SHORT_DOC_LIMIT:
            return full_text
        all_kw = []
        for kl in CONTRACT_KEYWORDS.values():
            all_kw.extend(kl)
        if custom_keywords:
            all_kw.extend(custom_keywords)
        return _chunk_based_retrieval(full_text, all_kw, max_output_chars)

    # Per-category scoring: find the best section for each keyword group
    selected_indices = set()
    for cat_name, cat_keywords in CONTRACT_KEYWORDS.items():
        best_score = 0
        best_idx = -1
        for section in sections:
            score = _score_chunk(section["body"], cat_keywords)
            if score > best_score:
                best_score = score
                best_idx = section["index"]
        if best_idx >= 0 and best_score > 0:
            selected_indices.add(best_idx)

    if custom_keywords:
        for section in sections:
            if _score_chunk(section["body"], custom_keywords) > 0:
                selected_indices.add(section["index"])

    if not selected_indices:
        logger.warning("[SECTION] No keyword matches, returning full text")
        return full_text[:max_output_chars]

    # Select and filter by budget
    selected = [s for s in sections if s["index"] in selected_indices]
    total = sum(len(s["body"]) for s in selected)

    if total > max_output_chars:
        all_kw = []
        for kl in CONTRACT_KEYWORDS.values():
            all_kw.extend(kl)
        scored_sel = [(s, _score_chunk(s["body"], all_kw)) for s in selected]
        scored_sel.sort(key=lambda x: x[1], reverse=True)
        selected = []
        total = 0
        for s, sc in scored_sel:
            if total + len(s["body"]) <= max_output_chars:
                selected.append(s)
                total += len(s["body"])
        selected.sort(key=lambda s: s["index"])

    result = "\n\n".join(s["body"] for s in selected)

    logger.info(
        "[SECTION] Selected %d/%d sections | %d chars (from %d) | %s",
        len(selected), len(sections), len(result), len(full_text),
        ", ".join(s["title"][:50] for s in selected),
    )

    return result


def _chunk_based_retrieval(
    full_text: str, all_keywords: List[str], max_output_chars: int,
) -> str:
    """Fallback: chunk-based retrieval for long unstructured documents."""
    chunks = _split_into_chunks(full_text)
    logger.info("[CHUNK] Split into %d chunks (%d chars)", len(chunks), len(full_text))

    scored = [(idx, text, _score_chunk(text, all_keywords)) for idx, text in chunks]
    scored.sort(key=lambda x: x[2], reverse=True)

    selected_indices = set()
    total_chars = 0
    for idx, chunk_text, score in scored:
        if score == 0:
            break
        if total_chars + len(chunk_text) > max_output_chars:
            break
        selected_indices.add(idx)
        total_chars += len(chunk_text)

    if selected_indices:
        high_threshold = scored[0][2] * 0.5 if scored else 0
        neighbors = set()
        for idx, _, score in scored:
            if score >= high_threshold and idx in selected_indices:
                for n in [idx - 1, idx + 1]:
                    if 0 <= n < len(chunks) and n not in selected_indices:
                        neighbors.add(n)
        for nidx in sorted(neighbors):
            if total_chars + len(chunks[nidx][1]) <= max_output_chars:
                selected_indices.add(nidx)
                total_chars += len(chunks[nidx][1])

    if not selected_indices:
        head = full_text[:max_output_chars // 2]
        tail = full_text[-(max_output_chars // 2):]
        return head + "\n...\n" + tail

    ordered = sorted(selected_indices)
    sections = []
    current = []
    prev = -2
    for idx in ordered:
        if idx == prev + 1:
            current.append(chunks[idx][1])
        else:
            if current:
                sections.append("\n".join(current))
            current = [chunks[idx][1]]
        prev = idx
    if current:
        sections.append("\n".join(current))

    return "\n\n[...]\n\n".join(sections)


# -------------------------------------------------------
# Public: Document loaders
# -------------------------------------------------------

def load_pdf(filepath: str) -> Optional[str]:
    """
    Smart PDF loader: tu dong detect va xu ly tung trang.

    Per-page logic:
    - Try pdfplumber truoc (nhanh, chinh xac cho text-selectable)
    - Neu text sparse -> fallback sang OCR (pytesseract)
    - Mixed PDF (vua text vua scan) duoc xu ly tung trang doc lap

    Args:
        filepath: Duong dan tuyet doi toi file PDF.

    Returns:
        Toan bo text da trich xuat, hoac None neu that bai.
    """
    if not os.path.isfile(filepath):
        logger.error("[DOC] File not found: %s", filepath)
        return None

    all_pages_text = []
    ocr_pages = []  # track trang nao can OCR de batch convert

    try:
        with pdfplumber.open(filepath) as pdf:
            total_pages = len(pdf.pages)
            logger.info(
                "[DOC] Opening PDF | file=%s | pages=%d",
                os.path.basename(filepath), total_pages,
            )

            # Pass 1: Thu pdfplumber cho moi trang
            for page_num, page in enumerate(pdf.pages, start=1):
                native_text = _extract_text_native(page)

                if _is_text_sparse(native_text):
                    # Trang nay can OCR -> danh dau, xu ly sau
                    ocr_pages.append(page_num)
                    all_pages_text.append(None)  # placeholder
                    logger.debug(
                        "[DOC] Page %d/%d: sparse text (%d chars) -> queued for OCR",
                        page_num, total_pages, len(native_text),
                    )
                else:
                    all_pages_text.append(native_text)
                    logger.debug(
                        "[DOC] Page %d/%d: native text OK (%d chars)",
                        page_num, total_pages, len(native_text),
                    )

        # Pass 2: OCR cho cac trang scan (batch convert de hieu qua hon)
        if ocr_pages:
            logger.info(
                "[DOC] Running OCR on %d/%d pages: %s",
                len(ocr_pages), total_pages, ocr_pages,
            )
            try:
                # Convert chi nhung trang can OCR (pdf2image 1-indexed)
                for pg in ocr_pages:
                    images = convert_from_path(
                        filepath,
                        first_page=pg,
                        last_page=pg,
                        dpi=300,  # 300 DPI cho OCR chuan
                    )
                    if images:
                        ocr_text = _extract_text_ocr(images[0])
                        all_pages_text[pg - 1] = ocr_text  # fill placeholder
                        logger.debug(
                            "[DOC] Page %d: OCR extracted %d chars",
                            pg, len(ocr_text),
                        )
            except Exception as ocr_exc:
                logger.error("[DOC] OCR failed: %s", ocr_exc)
                # Van tra ve nhung gi co tu native extraction
                for i, text in enumerate(all_pages_text):
                    if text is None:
                        all_pages_text[i] = ""

        # Ghep tat ca trang
        combined = "\n\n".join(t for t in all_pages_text if t)
        if not combined.strip():
            logger.warning("[DOC] No text extracted from: %s", filepath)
            return None

        native_count = total_pages - len(ocr_pages)
        logger.info(
            "[DOC] Done | file=%s | total_chars=%d | native_pages=%d | ocr_pages=%d",
            os.path.basename(filepath), len(combined),
            native_count, len(ocr_pages),
        )
        return combined

    except Exception as exc:
        logger.error("[DOC] Error processing PDF %s: %s", filepath, exc)
        return None


def load_document(filepath: str) -> Optional[str]:
    """
    General entry point: dispatch loader theo file extension.

    Hien tai ho tro: .pdf
    Mo rong sau: .docx, .xlsx, .txt, .csv...

    Args:
        filepath: Duong dan tuyet doi toi file.

    Returns:
        Text content da trich xuat, hoac None neu that bai hoac format khong ho tro.
    """
    if not filepath:
        return None

    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".pdf":
        return load_pdf(filepath)

    if ext == ".txt":
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as exc:
            logger.error("[DOC] Error reading text file %s: %s", filepath, exc)
            return None

    logger.warning("[DOC] Unsupported file format: %s", ext)
    return None
