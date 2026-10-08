"""Process-local review sessions with atomic transitions and sanitized storage."""
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
import json
from threading import RLock
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.models.transaction import Transaction
from app.models.transfer import TransferMatchingStatistics
from app.models.transfer_review import (
    TransferReviewCandidate, TransferReviewGroup, TransferReviewResult,
    TransferReviewStatus as Status, TransferReviewSummary,
)
from app.services.transfer_matcher import match_internal_transfers


class TransferReviewError(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class _Analysis:
    statistics: TransferMatchingStatistics
    candidates: dict[str, TransferReviewCandidate]
    groups: dict[str, list[str]]
    candidate_transaction_ids: set[str]


def _linked_ids(candidate: TransferReviewCandidate) -> set[str]:
    return {candidate.source_transaction_id, candidate.destination_transaction_id}


def _reserved_ids(analysis: _Analysis) -> set[str]:
    return {identifier for candidate in analysis.candidates.values()
            if candidate.status in (Status.AUTOMATIC, Status.CONFIRMED_INTERNAL)
            for identifier in _linked_ids(candidate)}


class TransferReviewStore:
    """No database, disk writes, or raw Transaction objects retained.

    Each create call makes a separate analysis. Review state is lost on process
    restart. One lock serializes transitions so competing requests cannot reserve
    the same transaction. Returned models are detached copies.
    """
    def __init__(self):
        self._analyses: dict[str, _Analysis] = {}
        self._lock = RLock()

    def create(self, transactions: list[Transaction]) -> TransferReviewResult:
        matching = match_internal_transfers(transactions)
        candidates = {}
        groups = {}
        for match in matching.matches:
            candidate = TransferReviewCandidate(
                **match.model_dump(exclude={'status'}),
                status=Status.AUTOMATIC if match.status == 'automatic' else Status.PENDING,
            )
            candidates[candidate.id] = candidate
        for group in matching.ambiguous_groups:
            groups[group.id] = []
            for alternative in group.alternatives:
                payload = json.dumps(['famlytic-review-pair', alternative.source_transaction_id,
                                      alternative.destination_transaction_id])
                identifier = str(uuid5(NAMESPACE_URL, payload))
                candidates[identifier] = TransferReviewCandidate(
                    **alternative.model_dump(), id=identifier,
                    status=Status.PENDING, group_id=group.id,
                )
                groups[group.id].append(identifier)
        analysis = _Analysis(
            statistics=matching.statistics, candidates=candidates, groups=groups,
            candidate_transaction_ids={tx.id for tx in transactions if tx.is_transfer_candidate},
        )
        analysis_id = str(uuid4())
        with self._lock:
            self._analyses[analysis_id] = analysis
            return self._snapshot(analysis_id, analysis)

    def get(self, analysis_id: str) -> TransferReviewResult:
        with self._lock:
            return self._snapshot(analysis_id, self._find(analysis_id))

    def confirm(self, analysis_id: str, transfer_id: str) -> TransferReviewResult:
        return self._transition(analysis_id, transfer_id, Status.CONFIRMED_INTERNAL)

    def reject(self, analysis_id: str, transfer_id: str) -> TransferReviewResult:
        return self._transition(analysis_id, transfer_id, Status.REJECTED)

    def _find(self, analysis_id: str) -> _Analysis:
        if analysis_id not in self._analyses:
            raise TransferReviewError(404, 'Transfer analysis not found')
        return self._analyses[analysis_id]

    def _transition(self, analysis_id: str, transfer_id: str, target: Status) -> TransferReviewResult:
        with self._lock:
            analysis = self._find(analysis_id)
            if transfer_id not in analysis.candidates:
                raise TransferReviewError(404, 'Transfer candidate not found')
            candidate = analysis.candidates[transfer_id]
            if candidate.status == target or (
                candidate.status == Status.AUTOMATIC and target == Status.CONFIRMED_INTERNAL
            ):
                return self._snapshot(analysis_id, analysis)
            if candidate.status != Status.PENDING:
                raise TransferReviewError(409, 'Transfer candidate is already resolved')
            if _linked_ids(candidate) & _reserved_ids(analysis):
                raise TransferReviewError(409, 'A linked transaction already belongs to an internal transfer')
            candidate.status = target
            return self._snapshot(analysis_id, analysis)

    def _snapshot(self, analysis_id: str, analysis: _Analysis) -> TransferReviewResult:
        reserved = _reserved_ids(analysis)
        candidates = []
        for stored in sorted(analysis.candidates.values(), key=lambda candidate: candidate.id):
            candidate = stored.model_copy(deep=True)
            candidate.available = candidate.status == Status.PENDING and not (_linked_ids(candidate) & reserved)
            if candidate.status == Status.PENDING and not candidate.available:
                candidate.unavailable_reason = 'A linked transaction already belongs to an internal transfer'
            candidates.append(candidate)
        by_id = {candidate.id: candidate for candidate in candidates}
        groups = []
        for group_id, ids in sorted(analysis.groups.items()):
            available_ids = sorted(identifier for identifier in ids if by_id[identifier].available)
            degrees = Counter(identifier for candidate_id in available_ids for identifier in _linked_ids(by_id[candidate_id]))
            groups.append(TransferReviewGroup(
                id=group_id, candidate_ids=sorted(ids), available_candidate_ids=available_ids,
                is_ambiguous=any(degree > 1 for degree in degrees.values()),
            ))
        # Unique pending review pairs are paired proposals, matching iteration 1.2.
        # Ambiguous participants and rejected pair candidates remain unmatched.
        pending_unique_ids = {identifier for candidate in candidates if candidate.available and candidate.group_id is None
                              for identifier in _linked_ids(candidate)}
        unmatched = sorted(analysis.candidate_transaction_ids - reserved - pending_unique_ids)
        automatic = [candidate for candidate in candidates if candidate.status == Status.AUTOMATIC]
        confirmed = [candidate for candidate in candidates if candidate.status == Status.CONFIRMED_INTERNAL]
        automatic_amount = sum((candidate.amount for candidate in automatic), Decimal(0))
        confirmed_amount = sum((candidate.amount for candidate in confirmed), Decimal(0))
        excluded = automatic_amount + confirmed_amount
        base = analysis.statistics
        summary = TransferReviewSummary(
            currency=base.currency, total_transaction_count=base.total_transaction_count,
            automatic_matches_count=len(automatic), confirmed_matches_count=len(confirmed),
            review_pairs_count=sum(candidate.available and candidate.group_id is None for candidate in candidates),
            unmatched_candidates_count=len(unmatched), raw_expense_total=base.raw_expense_total,
            raw_income_total=base.raw_income_total, automatic_transfer_outflow=automatic_amount,
            automatic_transfer_inflow=automatic_amount, confirmed_transfer_outflow=confirmed_amount,
            confirmed_transfer_inflow=confirmed_amount,
            adjusted_expense_total=base.raw_expense_total-excluded,
            adjusted_income_total=base.raw_income_total-excluded,
            pending_transfer_candidates_count=sum(candidate.available for candidate in candidates),
            ambiguous_groups_count=sum(group.is_ambiguous for group in groups),
        )
        return TransferReviewResult(analysis_id=analysis_id, candidates=candidates,
                                    ambiguous_groups=groups, unmatched_transfer_candidates=unmatched,
                                    summary=summary)
