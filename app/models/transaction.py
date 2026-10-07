from datetime import date as Date
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class Bank(str, Enum):
    SBER = "sber"
    TBANK = "tbank"


class Direction(str, Enum):
    INCOME = "income"
    EXPENSE = "expense"


class Transaction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    date: Date
    processed_date: Date | None = None
    bank: Bank
    owner: str | None = None
    raw_description: str
    merchant: str | None = None
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    currency: str = "RUB"
    direction: Direction
    bank_category: str | None = None
    category: str | None = None
    card_last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    is_transfer_candidate: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)
