from datetime import datetime

from pydantic import BaseModel


class NotificationPreferences(BaseModel):
    duel_invites: bool
    streak_reminders: bool
    leaderboard_updates: bool
    friend_requests: bool
    product_updates: bool


class NotificationEntryOut(BaseModel):
    id: str
    kind: str
    created_at: datetime
    is_read: bool


class NotificationsOut(BaseModel):
    entries: list[NotificationEntryOut]
