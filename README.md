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

The parsers support the original pipe-separated / single-line fixtures and these
multiline text layouts produced by pdfplumber:

- Sber: operation date/time, category, and account amount on the first line;
  processing date, authorization code, description, and optional masked card
  suffix on the second line. Additional description lines are joined.
- T-Bank: operation date, processing date, original amount/currency, card-currency
  amount, description, and card suffix (or a dash) on the first line; operation
  and processing times plus description continuation on the next line. Further
  description lines are joined. The second amount is used, with RUB required for
  the card-currency amount.

Repeated table headers and known legal footers are excluded. A transaction can
continue across page headers. Authorization codes and times are not retained;
only the last four card digits are stored in `card_last4`. Strong document titles
have priority over bank names appearing inside transaction descriptions.

Dates use `DD.MM.YYYY`. Amounts use two decimal places with comma or dot decimals
and optional space grouping. Merchant, owner, normalized category, and confidence
remain unset rather than inferred. The parsers reconcile income and expense sums
against supported statement summary labels when present (Sber: `Пополнение` /
`Списание`; T-Bank: `Пополнения` / `Расходы`). A mismatch rejects the document
instead of returning a silently incomplete result. Exact monetary values and
private document contents must not be written to diagnostic logs.

PDF layouts vary: support is limited to these layouts, not all statement versions.
Scanned PDFs/OCR, reordered columns, and non-RUB account/card amounts are unsupported.
Missing processing/time rows and malformed transaction blocks return HTTP 422.
Without recognized statement totals, reconciliation cannot prove completeness.
Transfers are heuristic candidates only; no matching or categorization is performed.
Unreadable/encrypted or unknown documents return 422; files over 10 MiB return 413.
Extraction runs outside the async event loop. The byte limit is not a PDF resource
sandbox; public deployment would need further resource controls.

Tests use anonymized text and synthetic PDFs only. No real statements are included.
**Never commit real financial PDFs, names, addresses, account/card numbers, or
other personal information to Git.** Keep local statements in `data/private/`;
that directory, all `*.pdf` files, `.env`, and virtual environments are ignored.
The API does not persist uploaded documents. Do not add document contents to logs.
