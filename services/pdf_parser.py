from __future__ import annotations

from typing import Optional

import fitz 

try:
    import pytesseract
    from pdf2image import convert_from_path
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

try:
    import docx
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False


def extract_pdf_text(pdf_path: str, max_pages: Optional[int] = None) -> str:
    """
    Extract text from a PDF using PyMuPDF.

    Returns plain text (string). If extraction fails for any reason, the exception
    is propagated so the caller can decide fallback behavior.
    """
    doc = fitz.open(pdf_path)
    try:
        total_pages = doc.page_count
        if max_pages is not None:
            total_pages = min(total_pages, max_pages)

        chunks: list[str] = []
        for i in range(total_pages):
            page = doc.load_page(i)
            text = page.get_text("text") or ""
            text = text.strip()
            if text:
                chunks.append(text)

        text_extracted = "\n\n".join(chunks).strip()
    finally:
        doc.close()
        
    if len(text_extracted) < 100 and HAS_OCR:
        try:
            images = convert_from_path(pdf_path, last_page=max_pages)
            ocr_text = [pytesseract.image_to_string(img) for img in images]
            text_extracted = "\n\n".join(ocr_text).strip()
        except Exception as e:
            print("OCR extraction failed:", e)
            
    return text_extracted


def extract_docx_text(docx_path: str) -> str:
    """Extract text from a DOCX file using python-docx."""
    if not HAS_DOCX:
        raise RuntimeError("python-docx is not installed. Run `pip install python-docx`.")
    doc = docx.Document(docx_path)
    return "\n".join([para.text for para in doc.paragraphs])
