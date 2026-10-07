from app.models.transaction import Bank
from app.parsers.base import ParseError


class UnknownBankError(ParseError):
    pass


def detect_bank(text: str) -> Bank:
    normalized = " ".join(text.casefold().split()).replace("ё", "е")
    # Document titles identify the issuer; bank names in transaction descriptions do not.
    titles = {
        Bank.SBER: ("индивидуальная выписка по платежному счету",),
        Bank.TBANK: ("справка о движении средств",),
    }
    markers = {
        Bank.SBER: ("sber", "сбербанк"),
        Bank.TBANK: ("ао «тбанк»", "tbank.ru"),
    }
    for signatures in (titles, markers):
        matches = [bank for bank, values in signatures.items() if any(s in normalized for s in values)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            break
    raise UnknownBankError("Unknown or ambiguous bank statement")
