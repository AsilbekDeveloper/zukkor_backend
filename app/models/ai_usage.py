from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DailyAiUsage(Base):
    """Kunlik (Toshkent mahalliy kuni) Gemini API chaqiruvlari soni - global
    "circuit breaker" uchun (`app/services/ai_usage_limiter.py`,
    `settings.MAX_DAILY_GEMINI_CALLS`). Bitta qator = bitta kun - kun
    boshlanganda avtomatik nolldan boshlanadi (yangi `usage_date` bilan
    yangi qator), alohida tozalash vazifasi kerak emas.
    """

    __tablename__ = "daily_ai_usage"

    # 'YYYY-MM-DD' (Toshkent mahalliy kuni).
    usage_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    gemini_call_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
