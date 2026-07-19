import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LobbyGame(Base):
    __tablename__ = "lobby_games"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    category_id: Mapped[int] = mapped_column(Integer, ForeignKey("categories.id"))
    total_questions: Mapped[int] = mapped_column(Integer)
    participant_count: Mapped[int] = mapped_column(Integer)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LobbyGameResult(Base):
    __tablename__ = "lobby_game_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lobby_game_id: Mapped[str] = mapped_column(String(36), ForeignKey("lobby_games.id"))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    rank: Mapped[int] = mapped_column(Integer)
    correct: Mapped[int] = mapped_column(Integer)
    total_time_ms: Mapped[int] = mapped_column(Integer)
    ball: Mapped[int] = mapped_column(Integer)
    xp: Mapped[int] = mapped_column(Integer)
