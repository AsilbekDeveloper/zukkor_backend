import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50))
    icon_name: Mapped[str] = mapped_column(String(50))
    color_key: Mapped[str] = mapped_column(String(20))  # 'coral' | 'terra' | 'teal' | 'pink' | 'green' | 'blue'
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    question_text: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON)  # ["variant1", "variant2", "variant3", "variant4"]
    correct_option_index: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class QuizSession(Base):
    __tablename__ = "quiz_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_ball: Mapped[int] = mapped_column(Integer, default=0)
    total_xp_earned: Mapped[int] = mapped_column(Integer, default=0)


class SessionQuestion(Base):
    __tablename__ = "session_questions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("quiz_sessions.id"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    order: Mapped[int] = mapped_column(Integer)  # 1-based
    total: Mapped[int] = mapped_column(Integer)  # shu sessiyadagi jami savollar soni
    broadcast_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    time_limit_ms: Mapped[int] = mapped_column(Integer, default=15000)
    option_order: Mapped[list | None] = mapped_column(JSON, nullable=True)  # shu savol ko'rsatilganda ishlatilgan tasodifiy tartib


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_question_id: Mapped[int] = mapped_column(ForeignKey("session_questions.id"), unique=True)
    selected_option: Mapped[int | None] = mapped_column(Integer, nullable=True)  # null = vaqt tugadi, javob berilmadi
    is_correct: Mapped[bool] = mapped_column(Boolean)
    ball: Mapped[int] = mapped_column(Integer)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
