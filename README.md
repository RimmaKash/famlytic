# Famlytic

Famlytic is a family spending analysis web app. This first technical iteration
provides a Python 3.12 / FastAPI foundation and an extensible PDF statement parsing
architecture for Sber and T-Bank. It extracts PDF text with pdfplumber, detects the
bank, and normalizes transactions using Pydantic. AI, frontend, database,
authentication, dashboards, recommendations, and transfer matching are outside
this iteration.

## Setup and development

Use Python 3.12 from the existing checkout (no additional Git worktree is needed):

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Run tests from the repository root:

```sh
.venv/bin/python -m pytest -q
```

`GET /health` returns `{"status": "ok"}`. Upload one PDF as multipart field `file`
to `POST /api/parse`, for example:

```sh
curl -F 'file=@data/private/statement.pdf' http://127.0.0.1:8000/api/parse
```

The response contains `bank`, `transactions_count`, and `transactions`. Amounts
are positive `Decimal` values, serialized as JSON strings to preserve precision;
`direction` is `income` or `expense`. A leading `+` denotes income; negative or
unsigned amounts denote expenses. UUIDs are generated per parse, not stable
transaction identities. Transfer markers only flag candidates; pairs are not matched.

## Supported formats and limitations

The parsers currently support one transaction per extracted line, with either
pipe-separated columns or whitespace columns in these orders:

- Sber: date, bank category, amount, description.
- T-Bank: date, optional time, optional processing date, amount, description,
  optional pipe-separated last four card digits (optionally masked with `*`).

Dates use `DD.MM.YYYY`. Amounts use two decimal places with comma or dot decimals
and optional space grouping; RUB is assumed. Merchant, owner, normalized category,
and confidence remain unset rather than inferred. Processing time is not retained.
PDF layouts vary: these rules implement the supplied anonymized text contracts,
not universal bank statement support. Multiline descriptions, reordered columns,
dual amount columns, other currencies, and scanned PDFs/OCR are unsupported.
Non-date-led headers are ignored; malformed date-led rows reject the document to
avoid silently returning incomplete transactions. Bank detection rejects unknown
or ambiguous signatures. No transaction rows, unreadable/encrypted PDFs, or
unsupported documents return HTTP 422; files over 10 MiB return 413. Extraction
runs outside the async event loop. The byte limit is not a PDF resource sandbox;
public deployment would need further resource controls.

Tests use anonymized text and synthetic PDFs only. No real statements are included.
**Never commit real financial PDFs, names, addresses, account/card numbers, or
other personal information to Git.** Keep local statements in `data/private/`;
that directory, all `*.pdf` files, `.env`, and virtual environments are ignored.
The API does not persist uploaded documents. Do not add document contents to logs.
