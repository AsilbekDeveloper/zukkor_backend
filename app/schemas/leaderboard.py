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


class CategoryRankEntryOut(BaseModel):
    """Kategoriya reytingidagi bitta qator. `level`/`level_title` ATAYLAB
    yo'q - bular umumiy (lifetime) XP'ga bog'liq global tushuncha, faqat
    shu kategoriyadagi XP asosida hisoblansa, foydalanuvchining ilovaning
    boshqa joylaridagi (Profil, umumiy reyting) darajasiga mos kelmay,
    chalkashlik keltirib chiqarardi."""

    user_id: str
    rank: int
    username: str | None
    first_name: str | None
    last_name: str | None
    avatar_color: str | None
    avatar_image_path: str | None
    category_xp: int
    is_me: bool


class CategoryLeaderboardOut(BaseModel):
    category_id: int
    category_name: str
    entries: list[CategoryRankEntryOut]
    me: CategoryRankEntryOut | None
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
    current_level_xp: int
    current_streak: int
    longest_streak: int
    games_played: int
    win_rate_percent: int
    total_wins: int
    best_rank_achieved: int
    friends_count: int
    public_quiz_count: int
