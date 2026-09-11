from datetime import datetime as real_datetime
from datetime import timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.notification import Notification
from app.models.user import User
from app.services.streak import TASHKENT_OFFSET
from app.services.streak_reminders import REMINDER_HOUR_LOCAL, send_due_streak_reminders


async def _create_user(db, email: str, **kwargs) -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"), **kwargs)
    db.add(user)
    await db.flush()
    return user


def _local(local_date: str, hour: int, minute: int = 0) -> real_datetime:
    naive_local = real_datetime.fromisoformat(f"{local_date}T{hour:02d}:{minute:02d}:00")
    return naive_local.replace(tzinfo=timezone.utc) - TASHKENT_OFFSET


def _fixed_now(instant: real_datetime):
    class _FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz else instant.replace(tzinfo=None)

    return _FixedDatetime


@pytest.mark.anyio
async def test_sends_reminder_when_streak_is_at_risk_today(db_session, monkeypatch):
    # "Now" = 2026-01-05 20:00 Tashkent (the reminder hour) - user last
    # played 2026-01-04 (yesterday local), so their streak is at risk today.
    monkeypatch.setattr(
        "app.services.streak_reminders.datetime", _fixed_now(_local("2026-01-05", REMINDER_HOUR_LOCAL))
    )
    user = await _create_user(
        db_session, "risk@example.com", current_streak=3, last_played_at=_local("2026-01-04", 12)
    )

    sent = await send_due_streak_reminders(db_session)

    assert sent == 1
    await db_session.refresh(user)
    assert user.last_streak_reminder_at is not None
    notifications = (await db_session.execute(select(Notification).where(Notification.user_id == user.id))).scalars().all()
    assert len(notifications) == 1
    assert notifications[0].kind == "streak_reminder"


@pytest.mark.anyio
async def test_does_nothing_outside_the_reminder_hour(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.streak_reminders.datetime", _fixed_now(_local("2026-01-05", REMINDER_HOUR_LOCAL - 1))
    )
    await _create_user(db_session, "toolate@example.com", current_streak=3, last_played_at=_local("2026-01-04", 12))

    sent = await send_due_streak_reminders(db_session)

    assert sent == 0


@pytest.mark.anyio
async def test_skips_user_who_already_played_today(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.streak_reminders.datetime", _fixed_now(_local("2026-01-05", REMINDER_HOUR_LOCAL))
    )
    await _create_user(db_session, "already_played@example.com", current_streak=3, last_played_at=_local("2026-01-05", 9))

    sent = await send_due_streak_reminders(db_session)

    assert sent == 0


@pytest.mark.anyio
async def test_skips_user_whose_streak_is_already_broken(db_session, monkeypatch):
    # Last played 3 days ago (local) - the streak is already dead, not
    # "at risk today", so this must not fire a confusing late reminder.
    monkeypatch.setattr(
        "app.services.streak_reminders.datetime", _fixed_now(_local("2026-01-05", REMINDER_HOUR_LOCAL))
    )
    await _create_user(db_session, "already_broken@example.com", current_streak=5, last_played_at=_local("2026-01-02", 12))

    sent = await send_due_streak_reminders(db_session)

    assert sent == 0


@pytest.mark.anyio
async def test_skips_user_with_reminders_disabled(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.streak_reminders.datetime", _fixed_now(_local("2026-01-05", REMINDER_HOUR_LOCAL))
    )
    await _create_user(
        db_session,
        "opted_out@example.com",
        current_streak=3,
        last_played_at=_local("2026-01-04", 12),
        streak_reminders=False,
    )

    sent = await send_due_streak_reminders(db_session)

    assert sent == 0


@pytest.mark.anyio
async def test_does_not_send_twice_in_the_same_local_day(db_session, monkeypatch):
    now = _local("2026-01-05", REMINDER_HOUR_LOCAL)
    monkeypatch.setattr("app.services.streak_reminders.datetime", _fixed_now(now))
    await _create_user(
        db_session,
        "already_reminded@example.com",
        current_streak=3,
        last_played_at=_local("2026-01-04", 12),
        last_streak_reminder_at=now - timedelta(hours=1),
    )

    sent = await send_due_streak_reminders(db_session)

    assert sent == 0


@pytest.mark.anyio
async def test_skips_user_with_no_streak(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.streak_reminders.datetime", _fixed_now(_local("2026-01-05", REMINDER_HOUR_LOCAL))
    )
    await _create_user(db_session, "no_streak@example.com", current_streak=0, last_played_at=_local("2026-01-04", 12))

    sent = await send_due_streak_reminders(db_session)

    assert sent == 0
