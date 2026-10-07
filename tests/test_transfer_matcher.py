"""Synthetic transfer scenarios, unrelated to private statement contents."""
from datetime import date, timedelta
from decimal import Decimal
from itertools import permutations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.transaction import Transaction


def tx(identifier, bank='sber', direction='expense', amount='100.00', day=0,
       candidate=True, description='Synthetic operation', currency='RUB'):
    return Transaction(id=identifier, date=date(2024, 4, 3)+timedelta(days=day),
                       bank=bank, direction=direction, amount=amount, currency=currency,
                       is_transfer_candidate=candidate, raw_description=description)


def match(rows):
    from app.services.transfer_matcher import match_internal_transfers
    return match_internal_transfers(rows)


def pair(**kwargs):
    return [tx('out'), tx('in', bank='tbank', direction='income', **kwargs)]


@pytest.mark.parametrize('source,destination', [('sber','tbank'),('tbank','sber')])
def test_same_day_both_candidates_automatic(source, destination):
    result=match([tx('out',bank=source),tx('in',bank=destination,direction='income')])
    assert len(result.matches)==1
    found=result.matches[0]
    assert found.status=='automatic'
    assert found.source_transaction_id=='out'
    assert found.destination_transaction_id=='in'
    assert found.source_bank==source
    assert found.destination_bank==destination
    assert found.amount==Decimal('100')
    assert found.source_date==date(2024,4,3)
    assert found.destination_date==date(2024,4,3)
    assert found.confidence==0.90
    assert result.statistics.automatic_match_count==1


@pytest.mark.parametrize('day', [-2,-1,1,2])
def test_delays_within_window_produce_review(day):
    result=match(pair(day=day))
    assert len(result.matches)==1
    assert result.matches[0].status=='review'
    assert result.statistics.review_match_count==1
    assert result.statistics.matched_internal_transfer_amount==0


def test_one_day_strong_signal_reaches_automatic_threshold():
    result=match([tx('out',description='Tinkoff Card2Card'),tx('in',bank='tbank',direction='income',day=1)])
    assert result.matches[0].confidence==0.85
    assert result.matches[0].status=='automatic'


@pytest.mark.parametrize('day', [-3,3])
def test_outside_window_no_match(day):
    result=match(pair(day=day))
    assert result.matches==[]
    assert set(result.unmatched_transfer_candidates)=={'out','in'}


@pytest.mark.parametrize('rows', [
    [tx('a'),tx('b',bank='tbank',direction='income',amount='100.01')],
    [tx('a'),tx('b',bank='tbank')],
    [tx('a',direction='income'),tx('b',bank='tbank',direction='income')],
    [tx('a'),tx('b',direction='income')],
])
def test_required_pair_constraints(rows):
    assert match(rows).matches==[]
    assert match(rows).ambiguous_groups==[]


def test_descriptions_do_not_replace_candidate_requirement():
    rows=[tx('out',candidate=False,description='Tinkoff Card2Card'),
          tx('in',bank='tbank',direction='income',candidate=False,description='Пополнение. Карта другого банка')]
    result=match(rows)
    assert result.matches==[]
    assert result.unmatched_transfer_candidates==[]


def test_unrelated_equal_value_purchases_not_matched():
    rows=[tx('out',candidate=False,description='SYNTHETIC SHOP'),
          tx('in',bank='tbank',direction='income',candidate=False,description='SYNTHETIC REFUND')]
    assert match(rows).matches==[]


def test_only_one_candidate_unique_pair_requires_review_without_signal():
    result=match(pair(candidate=False))
    assert result.matches[0].status=='review'
    assert result.matches[0].confidence==0.80
    assert result.statistics.transfer_candidate_count==1
    assert result.statistics.unmatched_candidate_count==0
    assert result.statistics.adjusted_expense_total==Decimal('100')


def test_only_one_candidate_and_strong_signal_can_be_automatic():
    rows=[tx('out',description='Tinkoff Card2Card'),
          tx('in',bank='tbank',direction='income',candidate=False)]
    assert match(rows).matches[0].status=='automatic'


@pytest.mark.parametrize('description', [
    'Tinkoff Card2Card','Тинькофф Банк','Пополнение. Карта другого банка',
    'Внутрибанковский перевод','SBOL transfer','SBOL перевод',
])
def test_recognized_descriptions_raise_confidence(description):
    rows=[tx('out',description=description),tx('in',bank='tbank',direction='income',candidate=False)]
    assert match(rows).matches[0].confidence==0.85


def test_sbol_purchase_alone_is_not_transfer_signal():
    rows=[tx('out',description='SBOL SYNTHETIC SHOP'),tx('in',bank='tbank',direction='income',candidate=False)]
    assert match(rows).matches[0].status=='review'


def test_candidate_without_partner_remains_unmatched():
    result=match([tx('out')])
    assert result.unmatched_transfer_candidates==['out']
    assert result.statistics.unmatched_candidate_count==1


def test_equal_score_duplicate_amounts_form_review_group_without_assignment():
    rows=[tx('out'),tx('in1',bank='tbank',direction='income'),tx('in2',bank='tbank',direction='income')]
    result=match(rows)
    assert result.matches==[]
    assert len(result.ambiguous_groups)==1
    group=result.ambiguous_groups[0]
    assert group.status=='review'
    assert set(group.transaction_ids)=={'out','in1','in2'}
    assert len(group.alternatives)==2
    assert result.statistics.ambiguous_group_count==1
    assert result.statistics.ambiguous_pair_count==2
    assert result.statistics.matched_internal_transfer_amount==0
    assert set(result.unmatched_transfer_candidates)=={'out','in1','in2'}


def test_competing_pairs_with_unequal_scores_still_require_review():
    result=match([tx('out'),tx('in1',bank='tbank',direction='income'),tx('in2',bank='tbank',direction='income',day=1)])
    assert result.matches==[]
    assert len(result.ambiguous_groups)==1


def test_two_by_two_duplicates_do_not_create_arbitrary_pairs():
    rows=[tx('out1'),tx('out2'),tx('in1',bank='tbank',direction='income'),tx('in2',bank='tbank',direction='income')]
    result=match(rows)
    assert result.matches==[]
    assert len(result.ambiguous_groups[0].alternatives)==4
    assert result.statistics.adjusted_expense_total==Decimal('200')


def test_transaction_ids_never_reused_in_matches():
    rows=pair()+[tx('out2',amount='75'),tx('in2',bank='tbank',direction='income',amount='75'),
                 tx('out3',amount='50'),tx('in3',bank='tbank',direction='income',amount='50',day=2)]
    result=match(rows)
    ids=[identifier for m in result.matches for identifier in [m.source_transaction_id,m.destination_transaction_id]]
    assert len(ids)==len(set(ids))==6


def test_result_is_stable_across_input_order_and_does_not_mutate_transactions():
    rows=pair()+[tx('unpaired',amount='75')]
    before=[row.model_dump() for row in rows]
    expected=match(rows).model_dump()
    for ordering in permutations(rows):
        assert match(list(ordering)).model_dump()==expected
    assert [row.model_dump() for row in rows]==before


def test_duplicate_transaction_ids_rejected_without_exposing_content():
    from app.services.transfer_matcher import TransferMatchingError
    with pytest.raises(TransferMatchingError,match='unique'):
        match([tx('same'),tx('same',bank='tbank',direction='income')])


def test_mixed_currencies_rejected_to_avoid_adding_incompatible_totals():
    from app.services.transfer_matcher import TransferMatchingError
    with pytest.raises(TransferMatchingError,match='currency'):
        match(pair(currency='USD'))


def test_single_non_rub_currency_is_supported_and_labeled():
    rows=[tx('out',currency='usd'),tx('in',bank='tbank',direction='income',currency='USD')]
    result=match(rows)
    assert result.statistics.currency=='USD'
    assert result.matches[0].currency=='USD'


def test_adjusted_totals_exclude_only_automatic_matches():
    rows=pair()+[tx('purchase',amount='25',candidate=False),
        tx('pay',bank='tbank',direction='income',amount='70',candidate=False),
        tx('review_out',amount='10'),tx('review_in',bank='tbank',direction='income',amount='10',day=2)]
    result=match(rows)
    stats=result.statistics
    assert stats.total_transaction_count==6
    assert stats.transfer_candidate_count==4
    assert stats.raw_expense_total==Decimal('135')
    assert stats.raw_income_total==Decimal('180')
    assert stats.internal_transfer_outflow==Decimal('100')
    assert stats.internal_transfer_inflow==Decimal('100')
    assert stats.adjusted_expense_total==Decimal('35')
    assert stats.adjusted_income_total==Decimal('80')
    assert stats.matched_internal_transfer_amount==Decimal('100')
    assert stats.raw_income_total-stats.raw_expense_total==stats.adjusted_income_total-stats.adjusted_expense_total


def test_empty_input_has_zero_statistics():
    result=match([])
    assert result.matches==[]
    assert result.statistics.total_transaction_count==0
    assert result.statistics.adjusted_expense_total==0


def test_reasons_never_echo_descriptions():
    rows=[tx('out',description='Tinkoff Card2Card SYNTHETIC_PRIVATE_MARKER'),
          tx('in',bank='tbank',direction='income')]
    assert 'SYNTHETIC_PRIVATE_MARKER' not in match(rows).model_dump_json()


def test_api_returns_independent_matching_result():
    rows=pair()
    response=TestClient(app).post('/api/analyze-transfers',json={'transactions':[t.model_dump(mode='json') for t in rows]})
    assert response.status_code==200
    data=response.json()
    assert data['statistics']['automatic_match_count']==1
    assert data['matches'][0]['amount']=='100.00'
    assert 'raw_description' not in response.text


def test_api_rejects_duplicate_ids():
    rows=[tx('same'),tx('same',bank='tbank',direction='income')]
    response=TestClient(app).post('/api/analyze-transfers',json={'transactions':[t.model_dump(mode='json') for t in rows]})
    assert response.status_code==422
    assert 'unique' in response.json()['detail']


def test_api_rejects_invalid_transaction():
    response=TestClient(app).post('/api/analyze-transfers',json={'transactions':[{'bank':'unknown'}]})
    assert response.status_code==422


def test_ambiguity_group_does_not_block_independent_automatic_pair():
    rows=[tx('out1'),tx('in1',bank='tbank',direction='income'),
          tx('in2',bank='tbank',direction='income'),
          tx('other_out',amount='75'),tx('other_in',bank='tbank',direction='income',amount='75')]
    result=match(rows)
    assert len(result.matches)==1
    assert len(result.ambiguous_groups)==1
    assert result.statistics.matched_internal_transfer_amount==Decimal('75')
    group_ids=set(result.ambiguous_groups[0].transaction_ids)
    assigned_ids={identifier for m in result.matches for identifier in [m.source_transaction_id,m.destination_transaction_id]}
    assert group_ids.isdisjoint(assigned_ids)
    assert result.statistics.unmatched_candidate_count==3


def test_group_output_is_independent_of_input_order():
    rows=[tx('out'),tx('in1',bank='tbank',direction='income'),tx('in2',bank='tbank',direction='income')]
    expected=match(rows).model_dump()
    for ordering in permutations(rows):
        assert match(list(ordering)).model_dump()==expected


def test_api_rejects_mixed_currency_batch():
    response=TestClient(app).post('/api/analyze-transfers',json={'transactions':[t.model_dump(mode='json') for t in pair(currency='USD')]})
    assert response.status_code==422
    assert 'currency' in response.json()['detail']


def test_exact_decimal_amounts_need_no_rounding_tolerance():
    rows=[tx('out',amount='100.00'),tx('in',bank='tbank',direction='income',amount='100.0')]
    assert len(match(rows).matches)==1


def test_empty_currency_rejected():
    from app.services.transfer_matcher import TransferMatchingError
    with pytest.raises(TransferMatchingError,match='currency'):
        match([tx('out',currency=' ')])
