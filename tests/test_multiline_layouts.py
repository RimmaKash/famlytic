"""Invented fixtures reproducing column placement, never private statement text."""
from datetime import date
from decimal import Decimal

import pytest

from app.parsers.base import ParseError
from app.parsers.detector import detect_bank
from app.parsers.sber import SberParser
from app.parsers.tbank import TBankParser

SBER_HEADER = '''Индивидуальная выписка по платёжному счёту
ДАТА ОПЕРАЦИИ (МСК) КАТЕГОРИЯ СУММА В ВАЛЮТЕ СЧЁТА
Дата обработки Описание операции Сумма в валюте
и код авторизации операции'''
TBANK_HEADER = '''Справка о движении средств
Дата и время Дата Сумма в валюте Сумма операции Описание Номер
операции списания операции в валюте карты операции карты'''


def test_tbank_title_wins_over_bank_in_description():
    assert detect_bank('Справка о движении средств\nПеревод в СберБанк') == 'tbank'


def test_sber_title_wins_over_bank_in_description():
    assert detect_bank('Индивидуальная выписка по платёжному счёту\nОплата TBANK.RU') == 'sber'


def test_conflicting_document_titles_rejected():
    with pytest.raises(ParseError):
        detect_bank(SBER_HEADER + '\n' + TBANK_HEADER)


def test_sber_two_line_operations_and_totals():
    text = SBER_HEADER + '''
Пополнение 100,00
Списание 25,50
03.04.2024 10:20 Супермаркеты25,50
04.04.2024 000000 SYNTHETIC SHOP. Операция по карте ****0000
продолжение описания
03.04.2024 11:20 Прочие операции +100,00
04.04.2024 000000 Внутрибанковский перевод. Операция по карте ****0000
Продолжение на следующей странице
служебный текст
'''
    rows = SberParser().parse(text)
    assert len(rows) == 2
    expense, income = rows
    assert expense.amount == Decimal('25.50')
    assert expense.direction == 'expense'
    assert expense.date == date(2024, 4, 3)
    assert expense.processed_date == date(2024, 4, 4)
    assert expense.bank_category == 'Супермаркеты'
    assert expense.card_last4 == '0000'
    assert expense.raw_description == 'SYNTHETIC SHOP. Операция по карте продолжение описания'
    assert income.direction == 'income'
    assert income.is_transfer_candidate
    assert 'служебный' not in income.raw_description


def test_sber_page_break_between_operation_and_processing():
    text = SBER_HEADER + '''
03.04.2024 10:20 Супермаркеты 25,50
Продолжение на следующей странице
неоперационный текст
\f''' + SBER_HEADER + '''
04.04.2024 000000 SYNTHETIC SHOP. Операция по карте ****0000
*
Дата формирования
'''
    rows = SberParser().parse(text)
    assert len(rows) == 1
    assert rows[0].processed_date == date(2024, 4, 4)
    assert rows[0].raw_description == 'SYNTHETIC SHOP. Операция по карте'


@pytest.mark.parametrize('body', [
    '03.04.2024 10:20 Супермаркеты 25,50',
    '03.04.2024 10:20 Супермаркеты BROKEN\n04.04.2024 000000 SHOP ****0000',
    '03.04.2024 10:20 Супермаркеты 25,50\n31.02.2024 000000 SHOP ****0000',
    '04.04.2024 000000 SHOP ****0000',
])
def test_sber_incomplete_real_row_rejected(body):
    with pytest.raises(ParseError):
        SberParser().parse(SBER_HEADER + '\n' + body)


def test_tbank_dual_amounts_times_continuation_and_totals():
    text = TBANK_HEADER + '''
03.04.2024 04.04.2024 -25.50 ₽ -25.50 ₽ Оплата в SYNTHETIC 0000
10:20 12:30 SHOP
продолжение описания
03.04.2024 04.04.2024 +100.00 ₽ +100.00 ₽ Пополнение. Карта другого 0000
11:20 12:30 банка.
03.04.2024 04.04.2024 +5.00 ₽ +5.00 ₽ Кэшбэк —
11:21 12:31 за покупки
АО «ТБанк» служебный текст
неоперационный текст
\fПополнения: 105.00 ₽
Расходы: 25.50 ₽
'''
    rows = TBankParser().parse(text)
    assert len(rows) == 3
    expense, income, cashback = rows
    assert expense.date == date(2024, 4, 3)
    assert expense.processed_date == date(2024, 4, 4)
    assert expense.amount == Decimal('25.50')
    assert expense.card_last4 == '0000'
    assert expense.raw_description == 'Оплата в SYNTHETIC SHOP продолжение описания'
    assert income.raw_description == 'Пополнение. Карта другого банка.'
    assert income.is_transfer_candidate
    assert cashback.card_last4 is None
    assert cashback.raw_description == 'Кэшбэк за покупки'


def test_tbank_uses_card_currency_amount_not_original_amount():
    rows = TBankParser().parse(TBANK_HEADER + '''
03.04.2024 04.04.2024 -1.00 USD -90.00 ₽ Оплата SYNTHETIC SHOP 0000
10:20 12:30
''')
    assert rows[0].amount == Decimal('90')
    assert rows[0].currency == 'RUB'


def test_tbank_continuation_across_page_header():
    text = TBANK_HEADER + '''
03.04.2024 04.04.2024 -25.50 ₽ -25.50 ₽ Оплата в SYNTHETIC 0000
АО «ТБанк» служебный текст
\f''' + TBANK_HEADER + '\n10:20 12:30 SHOP\n'
    row = TBankParser().parse(text)[0]
    assert row.raw_description == 'Оплата в SYNTHETIC SHOP'


@pytest.mark.parametrize('body', [
    '03.04.2024 04.04.2024 -25.50 ₽ -25.50 ₽ SHOP 0000',
    '03.04.2024 04.04.2024 -25.50 ₽ BROKEN SHOP 0000\n10:20 12:30',
    '03.04.2024 31.02.2024 -25.50 ₽ -25.50 ₽ SHOP 0000\n10:20 12:30',
    '03.04.2024 04.04.2024 -25.50 ₽ -25.50 USD SHOP 0000\n10:20 12:30',
])
def test_tbank_incomplete_real_row_rejected(body):
    with pytest.raises(ParseError):
        TBankParser().parse(TBANK_HEADER + '\n' + body)


@pytest.mark.parametrize('parser,text', [
    (SberParser(), SBER_HEADER + '\nПополнение 0,00\nСписание 30,00\n03.04.2024 10:20 Прочие операции 25,50\n04.04.2024 000000 SHOP ****0000'),
    (TBankParser(), TBANK_HEADER + '\n03.04.2024 04.04.2024 -25.50 ₽ -25.50 ₽ SHOP 0000\n10:20 12:30\nРасходы: 30.00 ₽'),
])
def test_statement_total_mismatch_rejects_partial_result(parser, text):
    with pytest.raises(ParseError, match='totals'):
        parser.parse(text)


def test_sber_zero_income_summary_is_valid():
    rows = SberParser().parse(SBER_HEADER + '''
Пополнение 0,00
Списание 25,50
03.04.2024 10:20 Прочие операции 25,50
04.04.2024 000000 SYNTHETIC SHOP ****0000
''')
    assert len(rows) == 1


def test_tbank_repeated_summary_does_not_hide_mismatch():
    with pytest.raises(ParseError, match='totals'):
        TBankParser().parse(TBANK_HEADER + '''
03.04.2024 04.04.2024 -25.50 ₽ -25.50 ₽ SYNTHETIC SHOP 0000
10:20 12:30
Расходы: 25.50 ₽
Расходы: 30.00 ₽
''')


def test_tbank_numeric_description_suffix_is_not_card():
    row = TBankParser().parse(TBANK_HEADER + '''
03.04.2024 04.04.2024 -25.50 ₽ -25.50 ₽ SYNTHETIC SHOP 1234 0000
10:20 12:30
''')[0]
    assert row.raw_description == 'SYNTHETIC SHOP 1234'
    assert row.card_last4 == '0000'


def test_bad_row_after_valid_sber_row_rejects_document():
    with pytest.raises(ParseError):
        SberParser().parse(SBER_HEADER + '''
03.04.2024 10:20 Прочие операции 25,50
04.04.2024 000000 SYNTHETIC SHOP ****0000
03.04.2024 11:20 Прочие операции BROKEN
04.04.2024 000000 SYNTHETIC SHOP ****0000
''')


def test_tbank_disagreeing_amount_signs_rejected():
    with pytest.raises(ParseError):
        TBankParser().parse(TBANK_HEADER + '''
03.04.2024 04.04.2024 -25.50 ₽ +25.50 ₽ SYNTHETIC SHOP 0000
10:20 12:30
''')
