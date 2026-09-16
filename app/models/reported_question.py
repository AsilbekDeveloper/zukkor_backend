from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.core.database import Base
from app.models.quiz import Question


class ReportedQuestion(Base):
    """Foydalanuvchi o'yin tugagach biror savolni "muammoli" deb belgilashi
    mumkin - rasmiy, UGC yoki AI savol farqi yo'q (barchasi `questions`
    jadvalida yashaydi). Admin panelda ko'rib chiqiladi (status: 'pending'
    -> 'reviewed'/'dismissed'). Bitta foydalanuvchi bitta savolni faqat bir
    marta report qila oladi - qayta yuborilsa eski yozuv yangilanadi
    (spam/duplicate qatorlar oldini olish uchun).

    2026-09-19, sifat auditi - "AI galyutsinatsiyasi" himoyasi #3 (False
    Positive: AI noto'g'ri tasdiqlagan yomon savol, keyinchalik
    o'yinchilar tomonidan payqalgan) - bu jadval ALLAQACHON aynan shu
    vazifa uchun mavjud edi, yangi model YARATISH shart emas: bitta savol
    uchun bir nechta TURLI foydalanuvchidan (`uq_reported_question_reporter`
    tufayli) report kelsa (`app/routers/reports.py`dagi
    `_AUTO_DEACTIVATE_REPORT_THRESHOLD`), savol AVTOMATIK `is_active=False`
    qilinadi - qo'lda admin ko'rib chiqishini kutmasdan darhol o'yindan
    olib tashlanadi. `Question.report_count` (pastda) admin panelida shu
    sonni ko'rsatadi."""

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


# `Question.report_count` shu yerda (ReportedQuestion tomonida) biriktiriladi,
# `quiz.py`ning O'ZIDA emas - `quiz.py`ga bu yerdan import qilish (Question
# uchun) allaqachon bor, aks holda `quiz.py` ham `reported_question.py`dan
# import qilishga majbur bo'lib, AYLANMA IMPORT (circular import) hosil
# bo'lardi. Bu haqiqiy DB ustuni EMAS - har safar o'qilganda jonli hisoblanadigan
# SQL subquery (`column_property`), shuning uchun hech qachon "eskirib"
# qolmaydi (alohida hisoblagichni sinxronlashtirish shart emas).
# `admin.py`da `QuestionAdmin`dan OLDIN import qilinishi kerak - aks holda
# bu atribut hali `Question`ga biriktirilmagan bo'ladi.
#
# `deferred=True` ATAYLAB ISHLATILMAYDI - bu bitta o'z-ID'siga bog'langan
# (indekslangan `question_id` bo'yicha) arzon `COUNT` subquery, `ORDER BY
# RANDOM()`ning butun jadvalni skanerlashi bilan solishtirsa ahamiyatsiz
# xarajat (Duel/Lobby/Solo savol tanlashda ham sezilarli sekinlashtirmaydi).
# Kechiktirilgan (`deferred=True`) qilinsa, admin panel/testlar uni ANIQ
# `await`langan holda qayta yuklashi shart bo'lardi - aks holda asyncio
# kengaytmasida oddiy atribut o'qish `MissingGreenlet` xatosiga olib
# kelishi mumkin edi (bu loyihada bir necha marta uchragan xato turi).
# Deferred bo'lmasa, bu ustun oddiy `select(Question)`/`db.get(Question,
# id)`ning O'ZIDA keladi - qo'shimcha yuklash kerak emas.
Question.report_count = column_property(
    select(func.count(ReportedQuestion.id))
    .where(ReportedQuestion.question_id == Question.id, ReportedQuestion.status != "dismissed")
    .correlate_except(ReportedQuestion)
    .scalar_subquery()
)
