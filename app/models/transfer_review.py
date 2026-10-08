"""Review responses contain pair metadata and aggregates, never statement text."""
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from app.models.transfer import TransferAlternative


class TransferReviewStatus(str, Enum):
    PENDING = 'pending'
    CONFIRMED_INTERNAL = 'confirmed_internal'
    REJECTED = 'rejected'
    AUTOMATIC = 'automatic'


class TransferReviewCandidate(TransferAlternative):
    id: str
    status: TransferReviewStatus
    group_id: str | None = None
    available: bool = False
    unavailable_reason: str | None = None


class TransferReviewGroup(BaseModel):
    id: str
    candidate_ids: list[str]
    available_candidate_ids: list[str]
    is_ambiguous: bool


class TransferReviewSummary(BaseModel):
    currency: str
    total_transaction_count: int = Field(ge=0)
    automatic_matches_count: int = Field(ge=0)
    confirmed_matches_count: int = Field(ge=0)
    review_pairs_count: int = Field(ge=0)
    unmatched_candidates_count: int = Field(ge=0)
    raw_expense_total: Decimal = Field(ge=0)
    raw_income_total: Decimal = Field(ge=0)
    automatic_transfer_outflow: Decimal = Field(ge=0)
    automatic_transfer_inflow: Decimal = Field(ge=0)
    confirmed_transfer_outflow: Decimal = Field(ge=0)
    confirmed_transfer_inflow: Decimal = Field(ge=0)
    adjusted_expense_total: Decimal = Field(ge=0)
    adjusted_income_total: Decimal = Field(ge=0)
    pending_transfer_candidates_count: int = Field(ge=0)
    ambiguous_groups_count: int = Field(ge=0)


class TransferReviewResult(BaseModel):
    analysis_id: str
    candidates: list[TransferReviewCandidate]
    ambiguous_groups: list[TransferReviewGroup]
    unmatched_transfer_candidates: list[str]
    summary: TransferReviewSummary
