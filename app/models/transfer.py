"""Transfer proposals and aggregate totals; no statement descriptions are returned."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.models.transaction import Bank


class TransferAlternative(BaseModel):
    """An eligible edge for review, not an assigned match."""
    source_transaction_id: str
    destination_transaction_id: str
    amount: Decimal = Field(gt=0)
    currency: str
    source_bank: Bank
    destination_bank: Bank
    source_date: date
    destination_date: date
    confidence: float = Field(ge=0, le=1)
    reason: str


class TransferMatch(TransferAlternative):
    id: str
    status: Literal['automatic', 'review']


class AmbiguousTransferGroup(BaseModel):
    id: str
    status: Literal['review'] = 'review'
    transaction_ids: list[str]
    alternatives: list[TransferAlternative]
    reason: str


class TransferMatchingStatistics(BaseModel):
    currency: str
    total_transaction_count: int = Field(ge=0)
    transfer_candidate_count: int = Field(ge=0)
    automatic_match_count: int = Field(ge=0)
    review_match_count: int = Field(ge=0)
    ambiguous_group_count: int = Field(ge=0)
    ambiguous_pair_count: int = Field(ge=0)
    matched_internal_transfer_amount: Decimal = Field(ge=0)
    unmatched_candidate_count: int = Field(ge=0)
    raw_expense_total: Decimal = Field(ge=0)
    raw_income_total: Decimal = Field(ge=0)
    internal_transfer_outflow: Decimal = Field(ge=0)
    internal_transfer_inflow: Decimal = Field(ge=0)
    adjusted_expense_total: Decimal = Field(ge=0)
    adjusted_income_total: Decimal = Field(ge=0)


class TransferMatchingResult(BaseModel):
    matches: list[TransferMatch]
    unmatched_transfer_candidates: list[str]
    ambiguous_groups: list[AmbiguousTransferGroup]
    statistics: TransferMatchingStatistics
