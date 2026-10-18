from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReferralReward(Base):
    """Taklif qilingan har bir foydalanuvchi uchun mukofot FAQAT BIR MARTA
    berilganini belgilaydi (`referred_user_id` primary key). Mukofot
    taklif qilingan foydalanuvchi birinchi SIFATLI o'yinini (rasmiy
    kategoriya, kamida `REFERRAL_MIN_QUESTIONS` savol) tugatganda ikkala
    tomonga beriladi. Saqlangan miqdorlar HAQIQATDA qo'shilgan qiymatlar
    (Diamond balans chegarasi tufayli so'ralgandan kam bo'lishi mumkin)."""

    __tablename__ = "referral_rewards"

    referred_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    referrer_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    referrer_coins: Mapped[int] = mapped_column(Integer, default=0)
    referrer_diamonds: Mapped[int] = mapped_column(Integer, default=0)
    referred_coins: Mapped[int] = mapped_column(Integer, default=0)
    referred_diamonds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReferralClick(Base):
    """Ommaviy taklif havolasi (`GET /r/{kod}`) har ochilganda bitta qator -
    voronka (bosilish -> ro'yxatdan o'tish -> birinchi o'yin) statistikasi
    uchun."""

    __tablename__ = "referral_clicks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    # "android" | "other"
    platform: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
