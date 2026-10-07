from io import BytesIO

import pdfplumber


class PDFExtractionError(ValueError):
    pass


def extract_pdf_text(content: bytes) -> str:
    if not content.startswith(b"%PDF-"):
        raise PDFExtractionError("A valid PDF file is required")
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:
        raise PDFExtractionError("Cannot read PDF; it may be damaged or encrypted") from exc
    if not text.strip():
        raise PDFExtractionError("PDF has no extractable text; scanned statements require OCR")
    return text
