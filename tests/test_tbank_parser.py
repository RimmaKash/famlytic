from datetime import date
from decimal import Decimal

import pytest

from app.parsers.base import ParseError
from app.parsers.tbank import TBankParser


@pytest.mark.parametrize('money,description,amount,direction,transfer', [
    ('-50.00 ₽', 'Оплата в YANDEX.TAXI', '50', 'expense', False),
    ('-34.99 ₽', 'Оплата в PYATEROCHKA', '34.99', 'expense', False),
    ('+2 000.00 ₽', 'Пополнение. Карта другого банка.', '2000', 'income', True),
    ('-1 026.00 ₽', 'Оплата в KOFEJJNYA COFFEE KINQ', '1026', 'expense', False),
    ('+291.00 ₽', 'Кэшбэк за обычные покупки', '291', 'income', False),
])
def test_rows(money, description, amount, direction, transfer):
    row = TBankParser().parse(f'28.06.2022 | {money} | {description}')[0]
    assert row.amount == Decimal(amount)
    assert row.direction == direction
    assert row.raw_description == description
    assert row.is_transfer_candidate == transfer
    assert row.bank == 'tbank'


def test_processing_date_and_card():
    row = TBankParser().parse('28.06.2022 12:30 | 29.06.2022 | -50.00 ₽ | Оплата в SHOP | ****0000')[0]
    assert row.date == date(2022, 6, 28)
    assert row.processed_date == date(2022, 6, 29)
    assert row.card_last4 == '0000'


def test_whitespace_row():
    row = TBankParser().parse('28.06.2022 12:30 29.06.2022 -50.00 ₽ Оплата в SHOP')[0]
    assert row.processed_date == date(2022, 6, 29)
    assert row.amount == Decimal('50')


@pytest.mark.parametrize('text', ['TBANK.RU', '28.06.2022 | broken',
    '28.06.2022 | -50.00 ₽ | SHOP\n29.06.2022 | broken'])
def test_malformed(text):
    with pytest.raises(ParseError):
        TBankParser().parse(text)
