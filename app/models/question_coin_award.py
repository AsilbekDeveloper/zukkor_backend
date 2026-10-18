from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class QuestionCoinAward(Base):
    """Bitta (user, question) juftligi uchun "to'g'ri javob" Coin'i
    berilganini belgilaydi - har bir rasmiy savoldan umrida faqat BIR
    marta. `QuestionXpAward`dan alohida, chunki u faqat sessiya oxirida
    yoziladi: yarim yo'lda tashlab ketilgan sessiyada XP yozilmaydi,
    Coin esa javob paytida beriladi va qayta olinmasligi kerak.
    """

    __tablename__ = "question_coin_awards"
    __table_args__ = (UniqueConstraint("user_id", "question_id", name="uq_question_coin_award_user_question"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
