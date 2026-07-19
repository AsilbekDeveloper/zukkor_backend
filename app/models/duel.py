import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DuelInvite(Base):
    __tablename__ = "duel_invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    from_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    to_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    category_id: Mapped[int] = mapped_column(Integer, ForeignKey("categories.id"))
    question_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|accepted|declined|expired
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Duel(Base):
    __tablename__ = "duels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    category_id: Mapped[int] = mapped_column(Integer, ForeignKey("categories.id"))
    user_a_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))  # taklif yuborgan
    user_b_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))  # qabul qilgan
    total_questions: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(15), default="in_progress")  # in_progress|finished
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Yakunlangan natijalar — _finish_duel'da hisoblanib shu yerga saqlanadi (Tarix uchun qayta hisoblanmasin)
    user_a_correct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_a_total_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_a_ball: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_a_xp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_a_result: Mapped[str | None] = mapped_column(String(10), nullable=True)  # won|lost|draw

    user_b_correct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_b_total_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_b_ball: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_b_xp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_b_result: Mapped[str | None] = mapped_column(String(10), nullable=True)


class DuelQuestion(Base):
    __tablename__ = "duel_questions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    duel_id: Mapped[str] = mapped_column(String(36), ForeignKey("duels.id"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    order: Mapped[int] = mapped_column(Integer)  # 0-based, = question_index
    option_order: Mapped[list] = mapped_column(JSON)
    broadcast_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    time_limit_ms: Mapped[int] = mapped_column(Integer, default=15000)


class DuelAnswer(Base):
    __tablename__ = "duel_answers"
    __table_args__ = (UniqueConstraint("duel_question_id", "user_id", name="uq_duel_answer_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    duel_question_id: Mapped[int] = mapped_column(ForeignKey("duel_questions.id"))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    selected_option: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
