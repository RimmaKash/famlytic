import re

from app.models.transaction import Bank, Direction, Transaction
from app.parsers.base import (
    BankParser, DATE, MONEY, ParseError, parse_date, parse_money,
    transfer_candidate, validate_statement_totals,
)

CURRENCY = r"(?:₽|RUB|USD|EUR|\$|€)"


class TBankParser(BankParser):
    row = re.compile(
        rf"^({DATE})(?:[ T]+\d{{2}}:\d{{2}}(?::\d{{2}})?)?\s*(?:\|\s*)?"
        rf"(?:({DATE})\s*(?:\|\s*)?)?({MONEY})\s*(?:₽|RUB|руб\.?)?"
        rf"\s*(?:\|\s*)?([^|]+?)(?:\s*\|\s*(?:\*+\s*)?(\d{{4}}))?$", re.I,
    )
    operation = re.compile(
        rf"^({DATE}) ({DATE}) ({MONEY}) ({CURRENCY}) "
        rf"({MONEY}) ({CURRENCY}) (.+?) (\d{{4}}|—|-)$", re.I,
    )
    times = re.compile(r"^\d{2}:\d{2}(?::\d{2})? \d{2}:\d{2}(?::\d{2})?(?: (.*))?$")

    def parse(self, text: str) -> list[Transaction]:
        multiline = "Сумма операции" in text or bool(re.search(rf"^{DATE} {DATE} .*₽ .*₽", text, re.M))
        transactions = self._parse_multiline(text) if multiline else self._parse_simple(text)
        if not transactions:
            raise ParseError("No supported T-Bank transactions found")
        validate_statement_totals(text, transactions, {
            Direction.INCOME: "Пополнения", Direction.EXPENSE: "Расходы",
        })
        return transactions

    def _parse_simple(self, text: str) -> list[Transaction]:
        transactions = []
        for line in text.splitlines():
            line = line.strip()
            if not re.match(rf"^{DATE}(?:\s|\||$)", line):
                continue
            match = self.row.fullmatch(line)
            if not match:
                raise ParseError("Unsupported T-Bank transaction row")
            date, processed, money, description, card = match.groups()
            try:
                amount, direction = parse_money(money)
                transactions.append(Transaction(
                    date=parse_date(date), processed_date=parse_date(processed) if processed else None,
                    bank=Bank.TBANK, raw_description=description.strip(), amount=amount,
                    direction=direction, card_last4=card,
                    is_transfer_candidate=transfer_candidate(description),
                ))
            except ValueError:
                raise ParseError("Invalid T-Bank transaction row") from None
        return transactions

    def _parse_multiline(self, text: str) -> list[Transaction]:
        transactions = []
        pending = None
        needs_times = False
        active = "Дата и время" not in text

        def finish():
            if pending is None:
                return
            if needs_times:
                raise ParseError("Incomplete T-Bank transaction block")
            pending["is_transfer_candidate"] = transfer_candidate(pending["raw_description"])
            try:
                transactions.append(Transaction(bank=Bank.TBANK, **pending))
            except ValueError:
                raise ParseError("Invalid T-Bank transaction block") from None

        for original in text.splitlines():
            line = " ".join(original.split())
            if not line:
                continue
            if line.startswith("Дата и время"):
                active = True
                continue
            if line.startswith(("операции списания", "Справка о движении средств")):
                continue
            if re.match(r'^АО [«"]?(?:ТБанк|Тинькофф)', line) or line.startswith(("Пополнения:", "Расходы:")):
                active = False
                continue
            if not active:
                continue
            if re.match(rf"^{DATE}(?:\s|$)", line):
                finish()
                match = self.operation.fullmatch(line)
                if not match:
                    raise ParseError("Unsupported T-Bank transaction block")
                date, processed, original_money, _, money, currency, description, card = match.groups()
                if currency.upper() not in ("₽", "RUB"):
                    raise ParseError("Unsupported T-Bank account currency")
                try:
                    amount, direction = parse_money(money)
                    _, original_direction = parse_money(original_money)
                    if original_direction != direction:
                        raise ParseError("Inconsistent T-Bank amount directions")
                    pending = dict(date=parse_date(date), processed_date=parse_date(processed),
                                   amount=amount, direction=direction, raw_description=description,
                                   card_last4=card if card.isdigit() else None)
                except ValueError:
                    raise ParseError("Invalid T-Bank transaction block") from None
                needs_times = True
            elif pending is not None:
                if needs_times:
                    times = self.times.fullmatch(line)
                    if not times:
                        raise ParseError("Missing T-Bank time row")
                    continuation = times.group(1)
                    needs_times = False
                else:
                    continuation = line
                if continuation:
                    pending["raw_description"] += " " + continuation
        finish()
        return transactions
