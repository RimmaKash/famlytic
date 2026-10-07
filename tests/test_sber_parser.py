from decimal import Decimal

import pytest

from app.parsers.base import ParseError
from app.parsers.sber import SberParser


@pytest.mark.parametrize('category,money,description,amount', [
    ('Прочие операции', '199,00', 'YM YANDEX.PLUS', '199.00'),
    ('Супермаркеты', '4 468,01', 'MAGAZIN SVILKI', '4468.01'),
    ('Супермаркеты', '1 333,36', 'SBERMARKET.RU', '1333.36'),
    ('Рестораны и кафе', '442,44', 'PIROGOVAYA', '442.44'),
])
def test_expense(category, money, description, amount):
    row = SberParser().parse(f'30.06.2022 | {category} | {money} | {description}')[0]
    assert row.amount == Decimal(amount)
    assert row.direction == 'expense'
    assert row.bank_category == category
    assert row.raw_description == description
    assert row.bank == 'sber'


@pytest.mark.parametrize('description', ['Tinkoff Card2Card', 'Тинькофф Банк', 'Внутрибанковский перевод'])
def test_income_transfer(description):
    row = SberParser().parse(f'28.06.2022 | Прочие операции | +2\u00a0000,00 | {description}')[0]
    assert row.amount == Decimal('2000')
    assert row.direction == 'income'
    assert row.is_transfer_candidate


def test_whitespace_row():
    row = SberParser().parse('29.06.2022 Супермаркеты 4 468,01 MAGAZIN SVILKI')[0]
    assert row.amount == Decimal('4468.01')
    assert not row.is_transfer_candidate


@pytest.mark.parametrize('text', ['SBER', '30.06.2022 | broken',
    '31.02.2022 | Прочие операции | 199,00 | SHOP',
    '30.06.2022 | Прочие операции | 0,00 | SHOP'])
def test_malformed(text):
    with pytest.raises(ParseError):
        SberParser().parse(text)
