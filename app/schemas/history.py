from datetime import datetime

from pydantic import BaseModel


class HistoryCategoryOut(BaseModel):
    id: int
    name: str
    icon_name: str
    color_key: str


class HistoryOpponentOut(BaseModel):
    name: str
    avatar_color: str | None
    avatar_image_path: str | None


class HistoryLobbyOut(BaseModel):
    rank: int
    participant_count: int


class HistoryEntryOut(BaseModel):
    session_id: str
    category: HistoryCategoryOut
    finished_at: datetime
    correct_count: int
    total_questions: int
    total_ball: int
    xp_earned: int
    game_mode: str  # "solo" | "duel" | "lobby"
    opponent: HistoryOpponentOut | None = None
    outcome: str | None = None  # "won" | "lost" | "draw" - faqat duel uchun
    lobby: HistoryLobbyOut | None = None  # faqat lobby uchun


class HistoryOut(BaseModel):
    entries: list[HistoryEntryOut]
    has_more: bool
