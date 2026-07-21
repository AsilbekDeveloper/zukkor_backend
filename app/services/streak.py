from datetime import datetime, timedelta, timezone

from app.models.user import User

# O'zbekiston doim UTC+5, DST yo'q - kun chegarasi shu offset bo'yicha hisoblanadi
TASHKENT_OFFSET = timedelta(hours=5)


def update_streak(user: User, played_at: datetime) -> None:
    """O'yin tugagach chaqiriladi - current_streak/longest_streak/last_played_at'ni yangilaydi."""
    if played_at.tzinfo is None:
        played_at = played_at.replace(tzinfo=timezone.utc)

    played_local_date = (played_at + TASHKENT_OFFSET).date()

    if user.last_played_at is None:
        user.current_streak = 1
    else:
        last_played_at = user.last_played_at
        if last_played_at.tzinfo is None:
            last_played_at = last_played_at.replace(tzinfo=timezone.utc)
        last_local_date = (last_played_at + TASHKENT_OFFSET).date()
        diff_days = (played_local_date - last_local_date).days

        if diff_days == 1:
            user.current_streak += 1
        elif diff_days > 1:
            user.current_streak = 1
        # diff_days <= 0 (bugun allaqachon o'ynagan) - streak o'zgarmaydi

    user.longest_streak = max(user.longest_streak, user.current_streak)
    user.last_played_at = played_at
