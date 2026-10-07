from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.models.transaction import Bank, Transaction
from app.models.transfer import TransferMatchingResult
from app.services.transfer_matcher import TransferMatchingError, match_internal_transfers
from app.parsers.base import ParseError
from app.parsers.detector import detect_bank
from app.parsers.sber import SberParser
from app.parsers.tbank import TBankParser
from app.services.pdf_extractor import PDFExtractionError, extract_pdf_text

app = FastAPI(title="Famlytic")
MAX_PDF_BYTES = 10 * 1024 * 1024


class ParseResponse(BaseModel):
    bank: Bank
    transactions_count: int
    transactions: list[Transaction]


def parse_document(content: bytes) -> ParseResponse:
    text = extract_pdf_text(content)
    bank = detect_bank(text)
    parser = SberParser() if bank == Bank.SBER else TBankParser()
    transactions = parser.parse(text)
    return ParseResponse(bank=bank, transactions_count=len(transactions), transactions=transactions)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/parse", response_model=ParseResponse)
async def parse_pdf(file: UploadFile = File(...)):
    try:
        content = await file.read(MAX_PDF_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds the 10 MiB limit")
    try:
        return await run_in_threadpool(parse_document, content)
    except (PDFExtractionError, ParseError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class AnalyzeTransfersRequest(BaseModel):
    transactions: list[Transaction]


# A synchronous route runs the matching work in FastAPI's thread pool.
@app.post('/api/analyze-transfers', response_model=TransferMatchingResult)
def analyze_transfers(request: AnalyzeTransfersRequest):
    try:
        return match_internal_transfers(request.transactions)
    except TransferMatchingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
