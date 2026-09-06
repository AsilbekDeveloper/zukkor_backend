import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CurrencyTransaction(Base):
    """Coin/Diamond iqtisodiyotining yagona daftari (ledger) - har bir
    balans o'zgarishi (ijobiy yoki manfiy) shu yerga yoziladi. Ikki maqsadga
    xizmat qiladi: (1) audit - `User.coin_balance`/`diamond_balance` doim
    shu yozuvlar yig'indisiga mos kelishi kerak, (2) foydalanuvchiga
    ko'rsatiladigan "Diamond/Coin tarixi" ekrani (`GET /wallet/transactions`).

    Ikkala valyuta ham shu bitta jadvalda (alohida CoinTransaction/
    DiamondTransaction jadvali emas) - "foydalanuvchining butun iqtisodiy
    tarixi" bitta so'rov bilan olinishi uchun; ular hech qachon bir-biriga
    aylantirilmagani uchun `currency` ustuni orqali ajratish yetarli.
    """

    __tablename__ = "currency_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # "coin" | "diamond"
    currency: Mapped[str] = mapped_column(String(10), index=True)
    # Musbat - kirim (topildi/sotib olindi/admin qo'shdi), manfiy - chiqim
    # (sarflandi/admin ayirdi).
    amount: Mapped[int] = mapped_column(Integer)
    # Nima uchun ekanligi - "daily_login", "first_game", "streak_bonus_7d",
    # "referral", "signup_bonus", "ai_generation", "admin_adjustment",
    # "purchase" (keyinroq, Payme/Click qo'shilganda), "cosmetic_purchase",
    # "streak_freeze" (keyinroq).
    reason: Mapped[str] = mapped_column(String(30), index=True)
    # Tranzaksiyadan KEYINGI balans - har bir qatorni alohida o'qiganda ham
    # o'sha paytdagi balansni ko'rsatish uchun (tarix ekranida foydali).
    balance_after: Mapped[int] = mapped_column(Integer)
    # Qo'shimcha kontekst - masalan AI-generatsiya uchun {"input_tokens":
    # ..., "output_tokens": ..., "category_id": ...}, referral uchun
    # {"referred_user_id": ...} - erkin shakl, faqat diagnostika/ko'rsatish
    # uchun, hech qanday hisob-kitob shu yerga tayanmaydi.
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
