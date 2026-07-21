from pydantic import BaseModel


class RankEntryOut(BaseModel):
    user_id: str
    rank: int
    username: str | None
    first_name: str | None
    last_name: str | None
    avatar_color: str | None
    avatar_image_path: str | None
    total_xp: int
    level: int
    level_title: str
    next_level_xp: int
    is_me: bool


class LeaderboardOut(BaseModel):
    entries: list[RankEntryOut]
    me: RankEntryOut
    has_more: bool


class PlayerStatsOut(BaseModel):
    user_id: str
    rank: int
    username: str | None
    first_name: str | None
    last_name: str | None
    avatar_color: str | None
    avatar_image_path: str | None
    total_xp: int
    level: int
    level_title: str
    next_level_xp: int
    current_streak: int
    longest_streak: int
    games_played: int
    win_rate_percent: int
