from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from app.main import app
from app.models.transaction import Transaction
from app.services.pdf_extractor import PDFExtractionError, extract_pdf_text

client = TestClient(app)


def make_pdf(text):
    # Synthetic ASCII-only PDF with valid offsets; no personal financial data.
    stream = f'BT /F1 12 Tf 40 740 Td ({text}) Tj ET'.encode()
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
    ]
    result = b'%PDF-1.4\n'
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(result)
    result += b'xref\n0 6\n0000000000 65535 f \n'
    result += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    result += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    return result


def test_health():
    assert client.get('/health').json() == {'status': 'ok'}


@pytest.mark.parametrize('header,row,bank', [
    ('SBER', '28.06.2022 | Other | +200,00 | TRANSFER', 'sber'),
    ('TBANK.RU', '28.06.2022 | -50.00 | SHOP', 'tbank'),
])
def test_parse_pdf(header, row, bank):
    # Move the transaction to the next extracted line.
    pdf = make_pdf(header + ') Tj 0 -20 Td (' + row)
    response = client.post('/api/parse', files={'file': ('synthetic.pdf', pdf, 'application/pdf')})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['bank'] == bank
    assert data['transactions_count'] == 1
    assert float(data['transactions'][0]['amount']) > 0


@pytest.mark.parametrize('content', [b'not a PDF', b'%PDF-1.4\nbroken', make_pdf(''), make_pdf('UNKNOWN'), make_pdf('SBER')])
def test_invalid_document(content):
    response = client.post('/api/parse', files={'file': ('synthetic.pdf', content, 'application/pdf')})
    assert response.status_code == 422


def test_missing_file():
    assert client.post('/api/parse').status_code == 422


def test_size_limit(monkeypatch):
    monkeypatch.setattr('app.main.MAX_PDF_BYTES', 10)
    response = client.post('/api/parse', files={'file': ('synthetic.pdf', b'x' * 11)})
    assert response.status_code == 413


def test_extraction():
    assert extract_pdf_text(make_pdf('SBER')) == 'SBER'


@pytest.mark.parametrize('amount', ['-1', '0', 'NaN'])
def test_model_rejects_nonpositive_amount(amount):
    with pytest.raises(ValidationError):
        Transaction(date='2022-06-28', bank='sber', raw_description='SHOP', amount=amount, direction='expense')
