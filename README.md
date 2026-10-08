# Famlytic

Famlytic is a family spending analysis web app. The current MVP provides a
Python 3.12 / FastAPI foundation, PDF statement parsing for Sber and T-Bank,
a separate deterministic internal-transfer matching service (Iteration 1.2),
and an isolated transfer review API (Iteration 1.3).
It extracts PDF text with pdfplumber, detects the bank, and normalizes transactions
using Pydantic. AI, frontend, database, authentication, dashboards, categorization,
and recommendations are outside the current scope.

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
transaction identities. Parsing only flags transfer candidates; matching is a separate step.

## Internal transfers (Iteration 1.2)

Call `match_internal_transfers(transactions)` in
`app/services/transfer_matcher.py`, or send normalized transactions from both
banks to `POST /api/analyze-transfers`:

```json
{"transactions": []}
```

Replace the empty list with combined `transactions` arrays from `/api/parse`.
Each transaction needs a unique ID. No PDF upload is required for this endpoint.
Do not mix currencies in one batch: totals must have a single currency (case is
normalized). Invalid batches return HTTP 422. Empty batches are supported.

An eligible pair requires different banks, one expense and one income, exactly
equal Decimal amounts, operation dates at most two days apart, and at least one
`is_transfer_candidate` flag. Processing dates are not used. Descriptions only
increase confidence; they never override eligibility. Recognized signals include
`Tinkoff Card2Card`, `Тинькофф Банк`, `Пополнение. Карта другого банка`,
`Внутрибанковский перевод`, and `SBOL` together with an explicit transfer marker.
`SBOL` alone is not a transfer signal.

The score is 50 base points, plus 25/15/5 for a 0/1/2-day gap, plus 15 for two
candidate flags or 5 for one, plus 5 per description with a recognized signal.
Confidence is score / 100, a heuristic score rather than a calibrated probability.
An isolated eligible pair is `automatic` at confidence >= 0.85, otherwise `review`.

The service builds a graph of all eligible pairs. Every connected component with
multiple edges becomes an `ambiguous_groups` entry with `status: "review"` and
all `alternatives`. These alternatives are **not assigned matches**. Even unequal
scores do not silently resolve competing pairs. No transaction participates in
more than one entry in `matches`. Results are independent of input order; match
IDs are deterministic for supplied transaction IDs. Re-parsing PDFs generates
new transaction IDs, so cross-import deduplication is not provided.

The response contains `matches`, `unmatched_transfer_candidates` (IDs only),
`ambiguous_groups`, and `statistics`. Reasons summarize rules without echoing
statement descriptions. A candidate in an ambiguous group remains unmatched;
a candidate in a unique review proposal is paired and no longer in the unmatched
list, but remains in the financial totals until confirmed.

`matched_internal_transfer_amount` counts automatic pairs once. Both
`internal_transfer_outflow` and `internal_transfer_inflow` equal that amount.
`adjusted_expense_total = raw_expense_total - internal_transfer_outflow` and
`adjusted_income_total = raw_income_total - internal_transfer_inflow`.
Unique review proposals and ambiguous alternatives are not subtracted. The net
cashflow therefore stays unchanged. All money is returned as Decimal JSON strings.

The service assumes the supplied accounts belong to the same family; it cannot
verify account ownership. Coincidental equal-value transfers can still be false
positives. Fees, split/batched transfers, same-bank transfers, delays over two
days, and currency conversion are not matched in v1. Conservative grouping can
send resolvable unequal-score alternatives to review. The review workflow below confirms individual proposals; durable persistence is
not implemented. Never store private normalized transactions in Git.

## Transfer review (Iteration 1.3)

Create a review analysis using `POST /api/transfers/review` with the same
`{"transactions": [...]}` body as matching. HTTP 201 returns a unique
`analysis_id`, sanitized `candidates`, `ambiguous_groups`, unmatched candidate
transaction IDs, and a recalculated `summary`.

Use the returned analysis ID for every subsequent request:

```text
GET  /api/transfers/review?analysis_id=<analysis_id>
POST /api/transfers/<candidate_id>/confirm?analysis_id=<analysis_id>
POST /api/transfers/<candidate_id>/reject?analysis_id=<analysis_id>
```

Each possible pairing has its own candidate ID. Group `candidate_ids` reference
all alternatives in the top-level `candidates` list, including resolved and
unavailable options for audit. No ambiguous pairing is automatically selected.

Candidate status transitions:

- `pending` -> `confirmed_internal`: reserve both transactions and subtract the
  amount from both income and expenses.
- `pending` -> `rejected`: leave transactions in normal totals. Rejection applies
  to that pairing, not every alternative involving its transactions.
- `automatic`: already excluded, stays automatic, and cannot be rejected.

Confirmed pairs cannot be rejected; rejected pairs cannot be confirmed (HTTP 409).
Repeating the same completed action is idempotent. Unknown analysis/candidate IDs
return 404; missing analysis IDs and invalid transaction batches return 422.
Confirmation reserves transaction IDs atomically, including across concurrent
requests. Competing pending options become `available: false` with a generic
reason; attempts to act on them return 409. A disjoint remaining option stays
pending even if the group is no longer ambiguous. Nothing auto-confirms after a
rejection or a competing confirmation.

The summary separates `automatic_transfer_outflow/inflow` from
`confirmed_transfer_outflow/inflow`. Adjusted totals subtract both, exactly once.
Raw totals remain unchanged. `pending_transfer_candidates_count` counts available
pending **pair options**, including ambiguous alternatives, not unique transactions
or unmatched transactions with no partner. `ambiguous_groups_count` counts groups
where available pending options still compete for a transaction. Historical group
entries remain in the response with `is_ambiguous: false` after resolution.
`review_pairs_count` counts available pending pairs originally outside groups.
Unmatched flagged transaction IDs exclude reserved transactions and isolated
pending review pairs; unresolved group participants and rejected candidates remain
unmatched. No descriptions, owners, card/account numbers, or original transactions
are retained in the review store or returned by the review API.

Review sessions are process-local memory state, isolated by `analysis_id`; creating
another analysis never overwrites existing decisions. There are no disk writes or
database. Run one API worker: restarts/reload lose review state, and workers do not
share it. Analysis IDs are scoping identifiers, not user authentication. The API is
for the current local MVP; durable/multi-user review is outside this iteration.
Re-importing statements creates another analysis and does not carry decisions over.
The original `/api/analyze-transfers` endpoint remains stateless and unchanged.

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
Parsing flags heuristic transfer candidates; it does not match pairs or categorize spending.
Unreadable/encrypted or unknown documents return 422; files over 10 MiB return 413.
Extraction runs outside the async event loop. The byte limit is not a PDF resource
sandbox; public deployment would need further resource controls.

Tests use anonymized text and synthetic PDFs only. No real statements are included.
**Never commit real financial PDFs, names, addresses, account/card numbers, or
other personal information to Git.** Keep local statements in `data/private/`;
that directory, all `*.pdf` files, `.env`, and virtual environments are ignored.
The API does not persist uploaded documents. Do not add document contents to logs.
