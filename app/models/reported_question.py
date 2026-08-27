from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.quiz import Question


class ReportedQuestion(Base):
    """Foydalanuvchi o'yin tugagach biror savolni "muammoli" deb belgilashi
    mumkin - rasmiy, UGC yoki AI savol farqi yo'q (barchasi `questions`
    jadvalida yashaydi). Admin panelda ko'rib chiqiladi (status: 'pending'
    -> 'reviewed'/'dismissed'). Bitta foydalanuvchi bitta savolni faqat bir
    marta report qila oladi - qayta yuborilsa eski yozuv yangilanadi
    (spam/duplicate qatorlar oldini olish uchun).
    """

    __tablename__ = "reported_questions"
    __table_args__ = (UniqueConstraint("question_id", "reporter_user_id", name="uq_reported_question_reporter"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    question: Mapped["Question"] = relationship()  # admin panelida savol matnini ko'rsatish uchun
    reporter_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(30))  # 'wrong_answer' | 'unclear' | 'offensive' | 'other'
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # 'pending' | 'reviewed' | 'dismissed'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __str__(self) -> str:
        return f"#{self.id} ({self.reason})"
