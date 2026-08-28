import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.duel import Duel
from app.services import duel_engine
from app.services.ws_manager import manager


class _FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


def _make_active_duel(duel_id: str = "duel-1") -> duel_engine._ActiveDuel:
    state = duel_engine._ActiveDuel(duel_id, "user-a", "user-b", category_id=1, total_questions=5)
    duel_engine._active_duels[duel_id] = state
    duel_engine._user_active_duel["user-a"] = duel_id
    duel_engine._user_active_duel["user-b"] = duel_id
    return state


@pytest.fixture(autouse=True)
async def _test_db(monkeypatch):
    # forfeit_duel writes to the real Duel row via the module-level
    # AsyncSessionLocal - point that at an isolated in-memory SQLite engine
    # (same pattern as conftest's db_session) instead of the real
    # DATABASE_URL, and seed a matching Duel row so the status update can
    # actually be verified.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(duel_engine, "AsyncSessionLocal", session_maker)

    async with session_maker() as db:
        db.add(
            Duel(
                id="duel-1",
                category_id=1,
                user_a_id="user-a",
                user_b_id="user-b",
                total_questions=5,
                status="in_progress",
            )
        )
        await db.commit()

    yield session_maker

    await engine.dispose()
    duel_engine._active_duels.clear()
    duel_engine._user_active_duel.clear()
    manager.active.clear()


@pytest.mark.anyio
async def test_forfeit_ends_the_duel_and_clears_tracking():
    state = _make_active_duel()

    await duel_engine.forfeit_duel("user-a", "duel-1")

    assert state.finished is True
    assert "duel-1" not in duel_engine._active_duels
    assert not duel_engine.is_user_in_active_duel("user-a")
    assert not duel_engine.is_user_in_active_duel("user-b")


@pytest.mark.anyio
async def test_forfeit_marks_the_duel_row_cancelled_in_the_db(_test_db):
    _make_active_duel()

    await duel_engine.forfeit_duel("user-a", "duel-1")

    async with _test_db() as db:
        duel = await db.get(Duel, "duel-1")
        assert duel.status == "cancelled"
        assert duel.finished_at is not None
        # No ball/XP for either side on a voided departure.
        assert duel.user_a_result is None
        assert duel.user_b_result is None


@pytest.mark.anyio
async def test_forfeit_notifies_the_opponent():
    _make_active_duel()
    ws = _FakeWebSocket()
    manager.connect("user-b", ws)

    await duel_engine.forfeit_duel("user-a", "duel-1")

    assert len(ws.sent) == 1
    assert ws.sent[0] == {"type": "duel_cancelled", "duel_id": "duel-1", "reason": "opponent_left"}


@pytest.mark.anyio
async def test_forfeit_cancels_pending_timeout_tasks():
    state = _make_active_duel()

    async def _never_ends():
        await duel_engine.asyncio.sleep(999)

    task = duel_engine.asyncio.create_task(_never_ends())
    state.user_timeout_task["user-b"] = task

    await duel_engine.forfeit_duel("user-a", "duel-1")
    await duel_engine.asyncio.sleep(0)

    with pytest.raises(duel_engine.asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_forfeit_is_a_no_op_for_an_unknown_or_already_finished_duel():
    # Never crashes when called for a duel that doesn't exist (e.g. a
    # stray duel_leave arriving after the duel already ended some other way).
    await duel_engine.forfeit_duel("user-a", "does-not-exist")

    state = _make_active_duel()
    state.finished = True
    await duel_engine.forfeit_duel("user-a", "duel-1")  # should not raise or re-notify
    assert "duel-1" in duel_engine._active_duels  # untouched - already-finished path returns early


@pytest.mark.anyio
async def test_forfeit_rejects_a_user_not_in_that_duel():
    _make_active_duel()

    # An unrelated user id can't forfeit someone else's duel.
    await duel_engine.forfeit_duel("some-other-user", "duel-1")

    assert "duel-1" in duel_engine._active_duels
    assert not duel_engine._active_duels["duel-1"].finished
