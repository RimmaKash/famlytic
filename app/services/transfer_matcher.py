"""Deterministic, conservative cross-bank transfer matching with Decimal totals."""
from collections import defaultdict
from decimal import Decimal
import json
import re
from uuid import NAMESPACE_URL, uuid5

from app.models.transaction import Direction, Transaction
from app.models.transfer import (
    AmbiguousTransferGroup, TransferAlternative, TransferMatch,
    TransferMatchingResult, TransferMatchingStatistics,
)

AUTOMATIC_CONFIDENCE_THRESHOLD = 0.85
MAX_DATE_DIFFERENCE_DAYS = 2
TRANSFER_SIGNALS = (
    'tinkoff card2card', 'тинькофф банк',
    'пополнение. карта другого банка', 'внутрибанковский перевод',
)


class TransferMatchingError(ValueError):
    """Invalid batch, reported without echoing IDs or financial data."""


def _has_transfer_signal(description: str) -> bool:
    normalized = ' '.join(description.casefold().split())
    if any(marker in normalized for marker in TRANSFER_SIGNALS):
        return True
    # SBOL alone can describe a purchase; require an explicit transfer marker.
    return bool(re.search(r'\bsbol\b', normalized)) and any(
        marker in normalized for marker in ('transfer', 'перевод', 'card2card')
    )


def _stable_id(kind: str, transaction_ids: list[str]) -> str:
    payload = json.dumps([kind, transaction_ids], ensure_ascii=True)
    return str(uuid5(NAMESPACE_URL, 'famlytic:' + payload))


def _alternative(source: Transaction, destination: Transaction, currency: str) -> TransferAlternative:
    days = abs((source.date - destination.date).days)
    candidate_count = int(source.is_transfer_candidate) + int(destination.is_transfer_candidate)
    signal_count = int(_has_transfer_signal(source.raw_description)) + int(
        _has_transfer_signal(destination.raw_description)
    )
    # Integer arithmetic avoids float-dependent tie/threshold behavior.
    score = 50 + (25, 15, 5)[days] + (15 if candidate_count == 2 else 5) + 5 * signal_count
    reason = (
        f'Equal amount and currency; opposite directions; different banks; '
        f'date gap {days} day(s); {candidate_count} candidate flag(s); '
        f'{signal_count} recognized transfer signal(s)'
    )
    return TransferAlternative(
        source_transaction_id=source.id, destination_transaction_id=destination.id,
        amount=source.amount, currency=currency, source_bank=source.bank,
        destination_bank=destination.bank, source_date=source.date,
        destination_date=destination.date, confidence=score / 100, reason=reason,
    )


def match_internal_transfers(transactions: list[Transaction]) -> TransferMatchingResult:
    """Return disjoint proposals and unassigned competing alternatives.

    Every connected eligible-pair component containing multiple edges is reserved
    for review. This deliberately avoids greedy allocation, even for unequal scores.
    Only isolated high-confidence edges affect adjusted income/expense totals.
    Unmatched candidates include ambiguous participants, but exclude unique review
    proposals. All input accounts are assumed to belong to the supplied family.
    """
    ids = [tx.id for tx in transactions]
    if len(set(ids)) != len(ids):
        raise TransferMatchingError('Transaction IDs must be unique')
    currencies = {tx.currency.strip().upper() for tx in transactions}
    if len(currencies) > 1 or '' in currencies:
        raise TransferMatchingError('A batch must contain one nonempty currency')
    currency = next(iter(currencies), 'RUB')
    incomes = defaultdict(list)
    for tx in sorted(transactions, key=lambda tx: tx.id):
        if tx.direction == Direction.INCOME:
            incomes[tx.amount].append(tx)

    edges: dict[tuple[str, str], TransferAlternative] = {}
    adjacency: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for source in sorted(transactions, key=lambda tx: tx.id):
        if source.direction != Direction.EXPENSE:
            continue
        for destination in incomes[source.amount]:
            if source.bank == destination.bank:
                continue
            if not (source.is_transfer_candidate or destination.is_transfer_candidate):
                continue
            if abs((source.date - destination.date).days) > MAX_DATE_DIFFERENCE_DAYS:
                continue
            key = (source.id, destination.id)
            edges[key] = _alternative(source, destination, currency)
            adjacency[source.id].add(key)
            adjacency[destination.id].add(key)

    matches = []
    ambiguous_groups = []
    visited = set()
    paired_ids = set()
    for root in sorted(adjacency):
        if root in visited:
            continue
        stack = [root]
        component_ids = set()
        component_edges = set()
        while stack:
            node = stack.pop()
            if node in component_ids:
                continue
            component_ids.add(node)
            for key in adjacency[node]:
                component_edges.add(key)
                stack.extend(identifier for identifier in key if identifier not in component_ids)
        visited.update(component_ids)
        alternatives = [edges[key] for key in sorted(component_edges)]
        if len(alternatives) == 1:
            edge = alternatives[0]
            status = 'automatic' if edge.confidence >= AUTOMATIC_CONFIDENCE_THRESHOLD else 'review'
            reason = edge.reason + (
                '; unique pair above threshold' if status == 'automatic'
                else '; unique pair below automatic threshold; confirmation required'
            )
            matches.append(TransferMatch(
                **edge.model_dump(exclude={'reason'}), reason=reason,
                id=_stable_id('match', [edge.source_transaction_id, edge.destination_transaction_id]),
                status=status,
            ))
            paired_ids.update(component_ids)
        else:
            group_ids = sorted(component_ids)
            ambiguous_groups.append(AmbiguousTransferGroup(
                id=_stable_id('ambiguity', group_ids), transaction_ids=group_ids,
                alternatives=alternatives,
                reason='Competing eligible pairs; no assignment made; manual review required',
            ))

    matches.sort(key=lambda match: (match.source_transaction_id, match.destination_transaction_id))
    ambiguous_groups.sort(key=lambda group: group.transaction_ids)
    candidates = {tx.id for tx in transactions if tx.is_transfer_candidate}
    unmatched = sorted(candidates - paired_ids)
    automatic = [match for match in matches if match.status == 'automatic']
    internal_amount = sum((match.amount for match in automatic), Decimal(0))
    raw_expense = sum((tx.amount for tx in transactions if tx.direction == Direction.EXPENSE), Decimal(0))
    raw_income = sum((tx.amount for tx in transactions if tx.direction == Direction.INCOME), Decimal(0))
    statistics = TransferMatchingStatistics(
        currency=currency, total_transaction_count=len(transactions),
        transfer_candidate_count=len(candidates), automatic_match_count=len(automatic),
        review_match_count=len(matches)-len(automatic), ambiguous_group_count=len(ambiguous_groups),
        ambiguous_pair_count=sum(len(group.alternatives) for group in ambiguous_groups),
        matched_internal_transfer_amount=internal_amount, unmatched_candidate_count=len(unmatched),
        raw_expense_total=raw_expense, raw_income_total=raw_income,
        internal_transfer_outflow=internal_amount, internal_transfer_inflow=internal_amount,
        adjusted_expense_total=raw_expense-internal_amount, adjusted_income_total=raw_income-internal_amount,
    )
    return TransferMatchingResult(matches=matches, unmatched_transfer_candidates=unmatched,
                                  ambiguous_groups=ambiguous_groups, statistics=statistics)
