import pytest

from app.parsers.detector import UnknownBankError, detect_bank


@pytest.mark.parametrize('text,bank', [
    ('Индивидуальная выписка по платёжному счёту', 'sber'),
    ('SBER', 'sber'), ('СберБанк', 'sber'),
    ('Справка о движении средств', 'tbank'), ('АО «ТБанк»', 'tbank'), ('TBANK.RU', 'tbank'),
])
def test_detection(text, bank):
    assert detect_bank(text) == bank


@pytest.mark.parametrize('text', ['', 'unknown document', 'SBER TBANK.RU'])
def test_unknown_or_ambiguous(text):
    with pytest.raises(UnknownBankError):
        detect_bank(text)
