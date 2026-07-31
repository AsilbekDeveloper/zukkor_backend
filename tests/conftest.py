from datetime import datetime as real_datetime
from datetime import timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base


@pytest.fixture
def anyio_backend():
    return "asyncio"


# aiosqlite (unlike asyncpg/Postgres) returns tz-naive datetimes for a
# `DateTime(timezone=True)` column on read-back, so a real route's
# `x.expires_at < datetime.now(timezone.utc)` would crash comparing naive
# vs aware. This is purely a SQLite test-driver quirk - production runs on
# Postgres/asyncpg, which round-trips tzinfo correctly - so the workaround
# lives here in the test fixtures, not in the routes.
class _NaiveNowDatetime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        return real_datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _patch_naive_now(monkeypatch):
    monkeypatch.setattr("app.routers.auth.datetime", _NaiveNowDatetime)


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session
    await engine.dispose()
