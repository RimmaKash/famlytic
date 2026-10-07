from app.models.transaction import Bank
from app.parsers.base import ParseError


class UnknownBankError(ParseError):
    pass


def detect_bank(text: str) -> Bank:
    normalized = " ".join(text.casefold().split()).replace("ё", "е")
    markers = {
        Bank.SBER: ("индивидуальная выписка по платежному счету", "sber", "сбербанк"),
        Bank.TBANK: ("справка о движении средств", "ао «тбанк»", "tbank.ru"),
    }
    matches = [bank for bank, signatures in markers.items() if any(s in normalized for s in signatures)]
    if len(matches) != 1:
        raise UnknownBankError("Unknown or ambiguous bank statement")
    return matches[0]
