from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.security import hash_password
from app.models.notification import Notification
from app.models.user import User
from app.routers import notifications


@pytest.fixture
async def _isolated_session_maker(monkeypatch):
    # _cleanup_old_notifications_once writes via the module-level
    # AsyncSessionLocal - point that at an isolated in-memory SQLite engine
    # instead of the real DATABASE_URL (same pattern as test_duel_forfeit.py).
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(notifications, "AsyncSessionLocal", session_maker)
    yield session_maker
    await engine.dispose()


@pytest.mark.anyio
async def test_cleanup_deletes_only_notifications_older_than_retention(_isolated_session_maker):
    now = datetime.now(timezone.utc)
    old_cutoff = now - timedelta(days=notifications._NOTIFICATION_RETENTION_DAYS + 1)
    recent = now - timedelta(days=1)

    async with _isolated_session_maker() as db:
        user = User(email="u@t.co", hashed_password=hash_password("Parol1234"))
        db.add(user)
        await db.flush()
        db.add(Notification(user_id=user.id, kind="welcome", created_at=old_cutoff))
        db.add(Notification(user_id=user.id, kind="welcome", created_at=recent))
        await db.commit()
        user_id = user.id

    await notifications._cleanup_old_notifications_once()

    async with _isolated_session_maker() as db:
        remaining = (await db.execute(select(Notification).where(Notification.user_id == user_id))).scalars().all()

    # SQLite doesn't round-trip tzinfo on DateTime columns - the deletion
    # logic itself (which ran against real tz-aware datetimes) already
    # proved correct via the count/kind checks above, so compare naively.
    assert len(remaining) == 1
    assert remaining[0].kind == "welcome"
    assert remaining[0].created_at.replace(tzinfo=timezone.utc) >= recent - timedelta(seconds=1)
