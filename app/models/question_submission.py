from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.quiz import Category, Question


class QuestionSubmission(Base):
    """Foydalanuvchi tizimga (ochiq, global kategoriyalarga) o'zi yangi
    savol qo'shmoqchi bo'lganda har bir urinish shu yerda saqlanadi - AI
    tasdiqlagan ham, rad etgan ham bo'lsa. Bu faqat audit/tarix jadvali:
    haqiqiy o'yinda ishlatiladigan savol (tasdiqlansa) alohida `questions`
    qatori sifatida yaratiladi (`resulting_question_id` orqali bog'lanadi).

    Moderatsiya to'liq AI (Gemini) tomonidan, so'rov paytida sinxron amalga
    oshiriladi - admin tasdiqlash navbati yo'q (tasdiqlangan savol darhol
    faol bo'lib, Solo rejimda chiqa boshlaydi). `status` shuning uchun
    amalda faqat ikkita yakuniy holatga ega ('approved'/'rejected') -
    'pending' faqat AI hali javob bermagan (masalan so'rov davomida xato
    ketgan va qator umuman yaratilmagan) holatlar uchun nazariy zaxira.
    """

    __tablename__ = "question_submissions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    submitter_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))

    question_text: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON)
    correct_option_index: Mapped[int] = mapped_column(Integer)

    # Foydalanuvchi o'zi tanlagan kategoriya (ixtiyoriy - tanlamagan bo'lishi
    # mumkin, shunda AI o'zi tanlaydi). `resulting_category_id` esa AI'ning
    # YAKUNIY qarori - ular farq qilishi mumkin (AI foydalanuvchi tanlovini
    # noto'g'ri deb topib, boshqasiga qayta tayinlashi mumkin).
    requested_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    resulting_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    # Admin panelida kategoriya nomini dropdown/matn qilib ko'rsatish uchun -
    # ikkita FK bir xil jadvalga (categories) ishora qilgani uchun
    # `foreign_keys` majburiy, aks holda SQLAlchemy qaysi ustunni
    # ishlatishni aniqlay olmaydi.
    resulting_category: Mapped["Category | None"] = relationship(foreign_keys=[resulting_category_id])

    status: Mapped[str] = mapped_column(String(20), default="pending")  # 'pending' | 'approved' | 'rejected'
    # AI'ning izohi - tasdiqlansa ham (masalan nima uchun shu kategoriya
    # tanlangani), rad etilsa ham (foydalanuvchiga ko'rsatiladigan sabab).
    ai_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tasdiqlangan bo'lsa, yaratilgan haqiqiy `Question` qatoriga ishora -
    # rad etilganda bo'sh qoladi.
    resulting_question_id: Mapped[int | None] = mapped_column(
        ForeignKey("questions.id", ondelete="SET NULL"), nullable=True
    )
    resulting_question: Mapped["Question | None"] = relationship()

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __str__(self) -> str:
        return f"#{self.id} ({self.status})"
