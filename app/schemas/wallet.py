from datetime import datetime

from pydantic import BaseModel


class CurrencyTransactionOut(BaseModel):
    id: str
    currency: str
    amount: int
    reason: str
    balance_after: int
    extra: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class WalletTransactionsOut(BaseModel):
    entries: list[CurrencyTransactionOut]
    has_more: bool
