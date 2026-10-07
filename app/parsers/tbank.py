import re

from app.models.transaction import Bank, Transaction
from app.parsers.base import BankParser, DATE, MONEY, ParseError, parse_date, parse_money, transfer_candidate


class TBankParser(BankParser):
    # Supports optional time, processing date, and trailing masked/last-four card field.
    row = re.compile(
        rf"^({DATE})(?:[ T]+\d{{2}}:\d{{2}}(?::\d{{2}})?)?\s*(?:\|\s*)?"
        rf"(?:({DATE})\s*(?:\|\s*)?)?({MONEY})\s*(?:₽|RUB|руб\.?)?"
        rf"\s*(?:\|\s*)?([^|]+?)(?:\s*\|\s*(?:\*+\s*)?(\d{{4}}))?$",
        re.IGNORECASE,
    )

    def parse(self, text: str) -> list[Transaction]:
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
            except ValueError as exc:
                raise ParseError("Invalid T-Bank transaction row") from exc
        if not transactions:
            raise ParseError("No supported T-Bank transactions found")
        return transactions
