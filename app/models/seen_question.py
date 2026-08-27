from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SeenQuestion(Base):
    """Bitta (user, question) juftligi shu foydalanuvchiga savol birinchi
    marta Solo'da SERVER TOMONIDAN YUBORILGAN payt (to'g'ri/noto'g'ri
    javob berish-bermasligidan qat'i nazar) belgilanadi. [[question_xp_award]]
    dan farqi shunda - bu yerda to'g'ri javob shart emas, faqat savol
    ko'rsatilgani muhim.

    Solo sessiya boshlaganda/keyingi savolga o'tganda ishlatiladi: bir xil
    kategoriyani qayta-qayta o'ynaganda, foydalanuvchiga hali umuman
    ko'rsatilmagan savollar birinchi navbatda beriladi - ular tugagach
    (kategoriyadagi barcha savollar kamida bir marta ko'rilgach), qolgan
    o'rinlar oldin ko'rilgan savollardan tasodifiy to'ldiriladi.
    """

    __tablename__ = "seen_questions"
    __table_args__ = (UniqueConstraint("user_id", "question_id", name="uq_seen_question_user_question"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
