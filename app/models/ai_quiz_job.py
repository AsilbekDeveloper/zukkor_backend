import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AiQuizGenerationJob(Base):
    """Asinxron AI quiz generatsiyasining holati - `POST /ai-quiz/generate-async`
    darhol shu qatorni yaratib job_id qaytaradi, haqiqiy Gemini chaqiruvi va
    Category/Question yaratish esa fon vazifasida (background task) sodir
    bo'ladi. Foydalanuvchi `GET /ai-quiz/generate-async/{job_id}` orqali
    holatni so'rashi MUMKIN, lekin asosiy yo'l - tayyor bo'lgach push orqali
    xabar olish (`type: pdf_ready`), chunki 1-2 daqiqa ochiq HTTP ulanishini
    kutish mobil UX uchun yaramaydi (bu jadval aynan shuni oldini olish
    uchun qo'shildi).
    """

    __tablename__ = "ai_quiz_generation_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))

    # 'pending' -> 'completed' | 'failed'
    status: Mapped[str] = mapped_column(String(20), default="pending")

    # Faqat status='completed' bo'lganda to'ldiriladi.
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)

    # Faqat status='failed' bo'lganda to'ldiriladi - foydalanuvchiga
    # ko'rsatiladigan, allaqachon tarjima qilingan xabar
    # (masalan QuizGenerationError'ning matni).
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    question_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __str__(self) -> str:
        return f"#{self.id} ({self.status})"
