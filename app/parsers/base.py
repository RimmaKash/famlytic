import re
from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.models.transaction import Direction, Transaction

DATE = r"\d{2}\.\d{2}\.\d{4}"
MONEY = r"[+−-]?\s*\d+(?:[ \u00a0\u202f]\d{3})*[,.]\d{2}"
TRANSFER_MARKERS = (
    "tinkoff card2card", "тинькофф банк", "пополнение. карта другого банка",
    "внутрибанковский перевод",
)


class ParseError(ValueError):
    """The document does not contain supported, valid transaction rows."""


def parse_date(value: str):
    return datetime.strptime(value, "%d.%m.%Y").date()


def parse_money(value: str) -> tuple[Decimal, Direction]:
    normalized = re.sub(r"\s", "", value).replace(",", ".").replace("−", "-")
    try:
        amount = Decimal(normalized)
        if not amount.is_finite() or amount == 0:
            raise ParseError("Transaction amount must be nonzero and finite")
        return abs(amount), Direction.INCOME if normalized.startswith("+") else Direction.EXPENSE
    except InvalidOperation as exc:
        raise ParseError("Invalid transaction amount") from exc


def transfer_candidate(description: str) -> bool:
    return any(marker in description.casefold() for marker in TRANSFER_MARKERS)


class BankParser(ABC):
    @abstractmethod
    def parse(self, text: str) -> list[Transaction]:
        """Parse extracted statement text, raising ParseError for unsupported rows."""


def validate_statement_totals(
    text: str, transactions: list[Transaction], labels: dict[Direction, str]
) -> None:
    """Reconcile optional document summaries, without exposing values in errors."""
    for direction, label in labels.items():
        pattern = re.compile(rf"^\s*{label}\s*:?\s*({MONEY})\s*(?:₽|RUB|руб\.?)?\s*$", re.I | re.M)
        matches = pattern.findall(text)
        actual = sum((tx.amount for tx in transactions if tx.direction == direction), Decimal(0))
        for value in matches:
            expected = Decimal(re.sub(r"\s", "", value).replace(",", "."))
            if expected != actual:
                raise ParseError("Statement totals do not match parsed transactions")
