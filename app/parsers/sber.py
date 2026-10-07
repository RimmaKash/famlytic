import re

from app.models.transaction import Bank, Transaction
from app.parsers.base import BankParser, DATE, MONEY, ParseError, parse_date, parse_money, transfer_candidate


class SberParser(BankParser):
    row = re.compile(
        rf"^({DATE})\s*(?:\|\s*)?([^|]+?)\s*(?:\|\s*)?({MONEY})"
        rf"\s*(?:₽|RUB|руб\.?)?\s*(?:\|\s*)?(.+)$", re.IGNORECASE
    )

    def parse(self, text: str) -> list[Transaction]:
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
            except ValueError as exc:
                raise ParseError("Invalid Sber transaction row") from exc
        if not transactions:
            raise ParseError("No supported Sber transactions found")
        return transactions
