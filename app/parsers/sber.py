import re

from app.models.transaction import Bank, Direction, Transaction
from app.parsers.base import (
    BankParser, DATE, MONEY, ParseError, parse_date, parse_money,
    transfer_candidate, validate_statement_totals,
)


class SberParser(BankParser):
    row = re.compile(
        rf"^({DATE})\s*(?:\|\s*)?([^|]+?)\s*(?:\|\s*)?({MONEY})"
        rf"\s*(?:₽|RUB|руб\.?)?\s*(?:\|\s*)?(.+)$", re.IGNORECASE
    )
    operation = re.compile(rf"^({DATE}) \d{{2}}:\d{{2}}(?::\d{{2}})? (.+?)\s*({MONEY})$")
    processing = re.compile(rf"^({DATE}) \d+ (.+?)(?:\s+\*{{4}}(\d{{4}}))?$")

    def parse(self, text: str) -> list[Transaction]:
        if re.search(rf"^{DATE} \d{{2}}:\d{{2}}", text, re.M) or "ДАТА ОПЕРАЦИИ" in text:
            transactions = self._parse_multiline(text)
        else:
            transactions = self._parse_simple(text)
        if not transactions:
            raise ParseError("No supported Sber transactions found")
        validate_statement_totals(text, transactions, {
            Direction.INCOME: "Пополнение", Direction.EXPENSE: "Списание",
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
                raise ParseError("Unsupported Sber transaction row")
            date, category, money, description = match.groups()
            try:
                amount, direction = parse_money(money)
                transactions.append(Transaction(
                    date=parse_date(date), bank=Bank.SBER, bank_category=category.strip(),
                    raw_description=description.strip(), amount=amount, direction=direction,
                    is_transfer_candidate=transfer_candidate(description),
                ))
            except ValueError:
                raise ParseError("Invalid Sber transaction row") from None
        return transactions

    def _parse_multiline(self, text: str) -> list[Transaction]:
        transactions = []
        pending = None
        active = "ДАТА ОПЕРАЦИИ" not in text

        def finish():
            if pending is None:
                return
            if not pending.get("raw_description") or not pending.get("processed_date"):
                raise ParseError("Incomplete Sber transaction block")
            pending["is_transfer_candidate"] = transfer_candidate(pending["raw_description"])
            try:
                transactions.append(Transaction(bank=Bank.SBER, **pending))
            except ValueError:
                raise ParseError("Invalid Sber transaction block") from None

        for original in text.splitlines():
            line = " ".join(original.split())
            if not line:
                continue
            if line.startswith("ДАТА ОПЕРАЦИИ"):
                active = True
                continue
            if line.startswith(("Индивидуальная выписка", "Дата обработки", "и код авторизации")):
                continue
            if line.startswith(("Продолжение на следующей странице", "Дата формирования")) or line == "*":
                active = False
                continue
            if not active:
                continue
            match = self.operation.fullmatch(line)
            if match:
                finish()
                date, category, money = match.groups()
                try:
                    amount, direction = parse_money(money)
                    pending = dict(date=parse_date(date), bank_category=category.strip(), amount=amount,
                                   direction=direction)
                except ValueError:
                    raise ParseError("Invalid Sber transaction block") from None
                continue
            if re.match(rf"^{DATE}(?:\s|$)", line):
                detail = self.processing.fullmatch(line)
                if not detail or pending is None or pending.get("processed_date"):
                    raise ParseError("Unsupported Sber transaction block")
                processed, description, card = detail.groups()
                try:
                    pending.update(processed_date=parse_date(processed),
                                   raw_description=description, card_last4=card)
                except ValueError:
                    raise ParseError("Invalid Sber processing date") from None
            elif pending is not None:
                if not pending.get("processed_date"):
                    raise ParseError("Missing Sber processing row")
                pending["raw_description"] += " " + line
        finish()
        return transactions
