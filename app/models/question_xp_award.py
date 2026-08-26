from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class QuestionXpAward(Base):
    """Bitta (user, question) juftligi XP/reyting uchun faqat BIR MARTA -
    birinchi to'g'ri javobda - hisobga olinganini belgilaydi. Solo/Duel/
    Lobby'ning barchasi shu jadvalni bir xil tarzda tekshiradi va yozadi -
    shu sababli bir xil rasmiy savolni istalgan rejimda qayta-qayta o'ynab,
    XP/reyting ballini cheksiz "fermalab" bo'lmaydi. O'yin ichidagi ball
    (duel g'olibini aniqlash, breakdown va h.k.) bunga bog'liq emas -
    faqat XP/reyting qatlami cheklanadi.
    """

    __tablename__ = "question_xp_awards"
    __table_args__ = (UniqueConstraint("user_id", "question_id", name="uq_question_xp_award_user_question"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
