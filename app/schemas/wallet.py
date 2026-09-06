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


class DiamondPricingOut(BaseModel):
    """Flutter'ning generatsiyadan OLDIN taxminiy narxni JONLI (server bilan
    aloqasiz, har gal foydalanuvchi savol sonini/hujjatni o'zgartirganda)
    hisoblab ko'rsatishi uchun - formulaning o'zi shu yerdan bir marta
    olinadi (masalan ekran ochilganda), so'ng `wallet.py`dagi
    `diamond_cost_from_tokens`/`estimate_diamond_cost` bilan BIR XIL
    formula Dart'da qaytariladi. Haqiqiy (final) narx baribir har doim
    serverda, generatsiya tugagach hisoblanadi - bu faqat taxmin uchun."""

    input_usd_per_1m_tokens: float
    output_usd_per_1m_tokens: float
    diamond_markup_multiplier: float
    usd_per_diamond: float
    chars_per_token_estimate: int
