"""Synthetic review state transitions; no real statement content."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.transaction import Transaction


def tx(identifier, bank='sber', direction='expense', amount='100', candidate=True):
    return Transaction(id=identifier,date=date(2024,4,3),bank=bank,direction=direction,
                       amount=amount,is_transfer_candidate=candidate,raw_description='SYNTHETIC OPERATION')


def review_rows():
    return [tx('out'),tx('in',bank='tbank',direction='income',candidate=False)]


def ambiguous_rows():
    return [tx('out'),tx('in1',bank='tbank',direction='income'),tx('in2',bank='tbank',direction='income')]


def store():
    from app.services.transfer_review import TransferReviewStore
    return TransferReviewStore()


def pending_ids(result):
    return [c.id for c in result.candidates if c.status=='pending' and c.available]


def test_confirm_review_pair_excludes_both_transactions():
    service=store()
    result=service.create(review_rows())
    assert result.candidates[0].status=='pending'
    updated=service.confirm(result.analysis_id,result.candidates[0].id)
    assert updated.candidates[0].status=='confirmed_internal'
    assert updated.summary.confirmed_transfer_outflow==Decimal('100')
    assert updated.summary.confirmed_transfer_inflow==Decimal('100')
    assert updated.summary.adjusted_expense_total==0
    assert updated.summary.adjusted_income_total==0
    assert updated.summary.pending_transfer_candidates_count==0


def test_reject_review_pair_keeps_normal_totals():
    service=store()
    result=service.create(review_rows())
    updated=service.reject(result.analysis_id,result.candidates[0].id)
    assert updated.candidates[0].status=='rejected'
    assert not updated.candidates[0].available
    assert updated.summary.confirmed_transfer_outflow==0
    assert updated.summary.adjusted_expense_total==Decimal('100')
    assert updated.summary.adjusted_income_total==Decimal('100')
    assert updated.summary.pending_transfer_candidates_count==0


def test_all_ambiguous_options_exposed_and_one_can_be_confirmed():
    service=store()
    result=service.create(ambiguous_rows())
    assert len(result.candidates)==2
    assert all(c.status=='pending' for c in result.candidates)
    assert result.summary.ambiguous_groups_count==1
    assert result.summary.adjusted_income_total==Decimal('200')
    chosen,other=result.candidates
    updated=service.confirm(result.analysis_id,chosen.id)
    by_id={c.id:c for c in updated.candidates}
    assert by_id[chosen.id].status=='confirmed_internal'
    assert by_id[other.id].status=='pending'
    assert not by_id[other.id].available
    assert by_id[other.id].unavailable_reason=='A linked transaction already belongs to an internal transfer'
    assert updated.summary.adjusted_expense_total==0
    assert updated.summary.adjusted_income_total==Decimal('100')
    assert updated.summary.ambiguous_groups_count==0


def test_competing_pair_cannot_be_confirmed_twice():
    from app.services.transfer_review import TransferReviewError
    service=store()
    result=service.create(ambiguous_rows())
    service.confirm(result.analysis_id,result.candidates[0].id)
    with pytest.raises(TransferReviewError) as caught:
        service.confirm(result.analysis_id,result.candidates[1].id)
    assert caught.value.status_code==409


def test_disjoint_remaining_pair_stays_pending_in_two_by_two_group():
    service=store()
    result=service.create([tx('out1'),tx('out2'),tx('in1',bank='tbank',direction='income'),tx('in2',bank='tbank',direction='income')])
    updated=service.confirm(result.analysis_id,result.candidates[0].id)
    assert len(pending_ids(updated))==1
    assert updated.summary.confirmed_matches_count==1
    assert updated.summary.ambiguous_groups_count==0
    final=service.confirm(result.analysis_id,pending_ids(updated)[0])
    assert final.summary.confirmed_matches_count==2
    assert final.summary.adjusted_expense_total==0
    assert final.summary.adjusted_income_total==0


def test_rejecting_ambiguous_option_does_not_auto_confirm_other_option():
    service=store()
    result=service.create(ambiguous_rows())
    updated=service.reject(result.analysis_id,result.candidates[0].id)
    assert updated.summary.confirmed_matches_count==0
    assert len(pending_ids(updated))==1
    assert updated.summary.adjusted_expense_total==Decimal('100')
    assert updated.summary.adjusted_income_total==Decimal('200')


def test_automatic_matches_remain_automatic_and_cannot_be_rejected():
    from app.services.transfer_review import TransferReviewError
    service=store()
    result=service.create([tx('out'),tx('in',bank='tbank',direction='income')])
    pair=result.candidates[0]
    assert pair.status=='automatic'
    assert result.summary.automatic_transfer_outflow==Decimal('100')
    assert result.summary.confirmed_transfer_outflow==0
    assert result.summary.adjusted_expense_total==0
    assert service.confirm(result.analysis_id,pair.id).candidates[0].status=='automatic'
    with pytest.raises(TransferReviewError) as caught:
        service.reject(result.analysis_id,pair.id)
    assert caught.value.status_code==409


@pytest.mark.parametrize('first,second', [('reject','confirm'),('confirm','reject')])
def test_terminal_state_conflicts(first,second):
    from app.services.transfer_review import TransferReviewError
    service=store()
    result=service.create(review_rows())
    getattr(service,first)(result.analysis_id,result.candidates[0].id)
    with pytest.raises(TransferReviewError) as caught:
        getattr(service,second)(result.analysis_id,result.candidates[0].id)
    assert caught.value.status_code==409


@pytest.mark.parametrize('action', ['confirm','reject'])
def test_repeated_action_is_idempotent(action):
    service=store()
    result=service.create(review_rows())
    first=getattr(service,action)(result.analysis_id,result.candidates[0].id)
    second=getattr(service,action)(result.analysis_id,result.candidates[0].id)
    assert first.model_dump()==second.model_dump()


@pytest.mark.parametrize('action', ['confirm','reject'])
def test_invalid_transfer_id(action):
    from app.services.transfer_review import TransferReviewError
    service=store()
    result=service.create(review_rows())
    with pytest.raises(TransferReviewError) as caught:
        getattr(service,action)(result.analysis_id,'missing')
    assert caught.value.status_code==404


def test_invalid_analysis_id():
    from app.services.transfer_review import TransferReviewError
    with pytest.raises(TransferReviewError) as caught:
        store().get('missing')
    assert caught.value.status_code==404


def test_analyses_are_isolated_and_response_mutation_cannot_change_state():
    service=store()
    first=service.create(review_rows())
    second=service.create(review_rows())
    assert first.analysis_id!=second.analysis_id
    service.confirm(first.analysis_id,first.candidates[0].id)
    assert service.get(second.analysis_id).candidates[0].status=='pending'
    second.candidates[0].status='confirmed_internal'
    assert service.get(second.analysis_id).candidates[0].status=='pending'


def test_summary_combines_automatic_confirmed_and_normal_transactions():
    service=store()
    rows=review_rows()+[tx('auto_out',amount='75'),tx('auto_in',amount='75',bank='tbank',direction='income'),
                       tx('purchase',amount='25',candidate=False),tx('pay',amount='40',bank='tbank',direction='income',candidate=False)]
    result=service.create(rows)
    updated=service.confirm(result.analysis_id,pending_ids(result)[0])
    summary=updated.summary
    assert summary.raw_expense_total==Decimal('200')
    assert summary.raw_income_total==Decimal('215')
    assert summary.automatic_transfer_outflow==summary.automatic_transfer_inflow==Decimal('75')
    assert summary.confirmed_transfer_outflow==summary.confirmed_transfer_inflow==Decimal('100')
    assert summary.adjusted_expense_total==Decimal('25')
    assert summary.adjusted_income_total==Decimal('40')
    assert summary.raw_income_total-summary.raw_expense_total==summary.adjusted_income_total-summary.adjusted_expense_total


def test_unmatched_candidates_remain_exposed_without_descriptions():
    result=store().create([tx('unpaired')])
    assert result.unmatched_transfer_candidates==['unpaired']
    assert result.summary.unmatched_candidates_count==1
    assert result.summary.pending_transfer_candidates_count==0
    assert 'raw_description' not in result.model_dump_json()


def test_private_fields_never_enter_review_store_or_response():
    rows=review_rows()
    rows[0].owner='SYNTHETIC_PRIVATE_OWNER'
    rows[0].raw_description='SYNTHETIC_PRIVATE_DESCRIPTION'
    rows[0].card_last4='0000'
    service=store()
    result=service.create(rows)
    serialized=result.model_dump_json()
    assert 'SYNTHETIC_PRIVATE' not in serialized
    assert 'owner' not in serialized
    assert 'card_last4' not in serialized
    assert 'raw_description' not in serialized


def test_concurrent_confirmation_of_competing_options_is_atomic():
    from app.services.transfer_review import TransferReviewError
    service=store()
    result=service.create(ambiguous_rows())
    def confirm(identifier):
        try:
            service.confirm(result.analysis_id,identifier)
            return 200
        except TransferReviewError as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(confirm,[c.id for c in result.candidates]))
    assert sorted(statuses)==[200,409]
    assert service.get(result.analysis_id).summary.confirmed_matches_count==1


def test_empty_analysis():
    result=store().create([])
    assert result.candidates==[]
    assert result.summary.adjusted_expense_total==0


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app.state,'transfer_reviews',store(),raising=False)
    return TestClient(app)


def create_api(client,rows=None):
    response=client.post('/api/transfers/review',json={'transactions':[t.model_dump(mode='json') for t in (review_rows() if rows is None else rows)]})
    assert response.status_code==201
    return response.json()


def test_api_create_get_confirm_and_updated_summary(client):
    created=create_api(client)
    query={'analysis_id':created['analysis_id']}
    assert client.get('/api/transfers/review',params=query).json()==created
    response=client.post(f"/api/transfers/{created['candidates'][0]['id']}/confirm",params=query)
    assert response.status_code==200
    assert response.json()['candidates'][0]['status']=='confirmed_internal'
    assert Decimal(response.json()['summary']['adjusted_expense_total'])==0
    assert client.get('/api/transfers/review',params=query).json()==response.json()


def test_api_reject_then_confirm_returns_conflict(client):
    created=create_api(client)
    query={'analysis_id':created['analysis_id']}
    prefix=f"/api/transfers/{created['candidates'][0]['id']}"
    assert client.post(prefix+'/reject',params=query).status_code==200
    assert client.post(prefix+'/confirm',params=query).status_code==409


def test_api_confirm_then_reject_returns_conflict(client):
    created=create_api(client)
    query={'analysis_id':created['analysis_id']}
    prefix=f"/api/transfers/{created['candidates'][0]['id']}"
    assert client.post(prefix+'/confirm',params=query).status_code==200
    assert client.post(prefix+'/reject',params=query).status_code==409


def test_api_confirm_specific_ambiguous_option(client):
    created=create_api(client,ambiguous_rows())
    query={'analysis_id':created['analysis_id']}
    assert len(created['ambiguous_groups'][0]['candidate_ids'])==2
    ids=[candidate['id'] for candidate in created['candidates']]
    assert client.post(f'/api/transfers/{ids[0]}/confirm',params=query).status_code==200
    assert client.post(f'/api/transfers/{ids[1]}/confirm',params=query).status_code==409


def test_api_unknown_ids_and_missing_analysis_id(client):
    created=create_api(client)
    assert client.get('/api/transfers/review',params={'analysis_id':'missing'}).status_code==404
    assert client.post('/api/transfers/missing/confirm',params={'analysis_id':created['analysis_id']}).status_code==404
    assert client.get('/api/transfers/review').status_code==422


def test_api_invalid_batch_is_not_stored(client):
    rows=review_rows()
    rows[1].id=rows[0].id
    response=client.post('/api/transfers/review',json={'transactions':[t.model_dump(mode='json') for t in rows]})
    assert response.status_code==422


def test_destination_transaction_cannot_be_reserved_twice():
    from app.services.transfer_review import TransferReviewError
    service=store()
    result=service.create([tx('out1'),tx('out2'),tx('in',bank='tbank',direction='income')])
    service.confirm(result.analysis_id,result.candidates[0].id)
    with pytest.raises(TransferReviewError) as caught:
        service.confirm(result.analysis_id,result.candidates[1].id)
    assert caught.value.status_code==409
    assert service.get(result.analysis_id).summary.adjusted_expense_total==Decimal('100')


def test_rejected_alternative_does_not_reserve_shared_transaction():
    service=store()
    result=service.create(ambiguous_rows())
    service.reject(result.analysis_id,result.candidates[0].id)
    updated=service.confirm(result.analysis_id,result.candidates[1].id)
    assert updated.summary.confirmed_matches_count==1
    assert updated.summary.adjusted_expense_total==0
    assert updated.summary.adjusted_income_total==Decimal('100')


def test_rejecting_blocked_alternative_returns_conflict():
    from app.services.transfer_review import TransferReviewError
    service=store()
    result=service.create(ambiguous_rows())
    service.confirm(result.analysis_id,result.candidates[0].id)
    with pytest.raises(TransferReviewError) as caught:
        service.reject(result.analysis_id,result.candidates[1].id)
    assert caught.value.status_code==409


def test_review_store_does_not_retain_private_input_fields():
    rows=review_rows()
    rows[0].owner='SYNTHETIC_PRIVATE_OWNER'
    rows[0].raw_description='SYNTHETIC_PRIVATE_DESCRIPTION'
    service=store()
    service.create(rows)
    assert 'SYNTHETIC_PRIVATE' not in repr(service._analyses)


def test_rejected_candidate_returns_to_unmatched_flags():
    service=store()
    result=service.create(review_rows())
    assert result.unmatched_transfer_candidates==[]
    updated=service.reject(result.analysis_id,result.candidates[0].id)
    assert updated.unmatched_transfer_candidates==['out']
    assert updated.summary.unmatched_candidates_count==1


def test_api_rejects_automatic_match(client):
    created=create_api(client,[tx('out'),tx('in',bank='tbank',direction='income')])
    response=client.post(f"/api/transfers/{created['candidates'][0]['id']}/reject",params={'analysis_id':created['analysis_id']})
    assert response.status_code==409
    fetched=client.get('/api/transfers/review',params={'analysis_id':created['analysis_id']}).json()
    assert fetched['candidates'][0]['status']=='automatic'
    assert Decimal(fetched['summary']['adjusted_expense_total'])==0
