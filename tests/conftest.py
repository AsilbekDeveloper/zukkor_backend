from datetime import datetime as real_datetime
from datetime import timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.core.database import Base
from app.core.limiter import limiter


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    # Har bir testdan oldin tozalanadi - aks holda bir xil (soxta) klient
    # IP'i bilan qilingan testlar to'plami umumiy hisoblagichni to'ldirib,
    # keyingi testlarda haqiqiy kod bilan aloqasi yo'q 429 xatoga olib kelardi.
    limiter.reset()


def make_request(client_ip: str = "testclient") -> Request:
    """Route funksiyasini to'g'ridan-to'g'ri (HTTP qatlamisiz) chaqiradigan
    testlar uchun - slowapi dekoratori haqiqiy `Request` obyektini talab
    qiladi, chunki undan klient IP'ini o'qiydi."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
        "client": (client_ip, 12345),
    }
    return Request(scope)


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
