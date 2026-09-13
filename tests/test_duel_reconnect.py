"""`duel_engine.handle_disconnect`/`handle_reconnect` (2026-09-13, user
request) - a dropped WebSocket mid-duel now gets a
`DISCONNECT_GRACE_SECONDS` reconnect window instead of an immediate
`forfeit_duel`, which used to void the whole match for a one-second
network blip."""

import asyncio

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
    state.questions = [
        {
            "question_id": i + 1,
            "question_text": f"Savol {i + 1}",
            "shuffled_options": ["1", "2", "3", "4"],
            "correct_option": 0,
            "time_limit_ms": 15000,
        }
        for i in range(5)
    ]
    duel_engine._active_duels[duel_id] = state
    duel_engine._user_active_duel["user-a"] = duel_id
    duel_engine._user_active_duel["user-b"] = duel_id
    return state


@pytest.fixture(autouse=True)
async def _test_db(monkeypatch):
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
async def test_handle_disconnect_starts_a_grace_period_without_ending_the_duel():
    state = _make_active_duel()
    ws = _FakeWebSocket()
    manager.connect("user-b", ws)

    await duel_engine.handle_disconnect("user-a", "duel-1")

    assert "user-a" in state.disconnect_grace_task
    assert state.finished is False
    assert "duel-1" in duel_engine._active_duels
    assert ws.sent == [{"type": "duel_opponent_disconnected", "duel_id": "duel-1", "grace_seconds": 15}]


@pytest.mark.anyio
async def test_handle_reconnect_cancels_the_grace_period_and_notifies_the_opponent():
    state = _make_active_duel()
    state.user_index["user-a"] = 2
    ws_b = _FakeWebSocket()
    manager.connect("user-b", ws_b)

    await duel_engine.handle_disconnect("user-a", "duel-1")
    await duel_engine.handle_reconnect("user-a")

    assert "user-a" not in state.disconnect_grace_task
    assert state.finished is False
    assert {"type": "duel_opponent_reconnected", "duel_id": "duel-1"} in ws_b.sent


@pytest.mark.anyio
async def test_handle_reconnect_resends_the_current_question_with_a_fresh_timer():
    state = _make_active_duel()
    state.user_index["user-a"] = 2
    ws_a = _FakeWebSocket()
    manager.connect("user-a", ws_a)
    manager.connect("user-b", _FakeWebSocket())

    await duel_engine.handle_disconnect("user-a", "duel-1")
    await duel_engine.handle_reconnect("user-a")

    question_messages = [m for m in ws_a.sent if m.get("type") == "duel_question"]
    assert len(question_messages) == 1
    assert question_messages[0]["question_index"] == 2
    assert "user-a" in state.user_timeout_task  # a fresh timeout was started


@pytest.mark.anyio
async def test_handle_reconnect_does_not_resend_a_question_once_the_user_already_finished():
    state = _make_active_duel()
    state.user_finished["user-a"] = True
    ws_a = _FakeWebSocket()
    manager.connect("user-a", ws_a)

    await duel_engine.handle_disconnect("user-a", "duel-1")
    await duel_engine.handle_reconnect("user-a")

    assert [m for m in ws_a.sent if m.get("type") == "duel_question"] == []


@pytest.mark.anyio
async def test_disconnect_grace_period_expiring_without_reconnect_forfeits_the_duel(monkeypatch, _test_db):
    monkeypatch.setattr(duel_engine, "DISCONNECT_GRACE_SECONDS", 0.05)
    _make_active_duel()
    ws_b = _FakeWebSocket()
    manager.connect("user-b", ws_b)

    await duel_engine.handle_disconnect("user-a", "duel-1")
    await asyncio.sleep(0.15)

    assert "duel-1" not in duel_engine._active_duels
    assert not duel_engine.is_user_in_active_duel("user-a")
    assert {"type": "duel_cancelled", "duel_id": "duel-1", "reason": "opponent_left"} in ws_b.sent

    async with _test_db() as db:
        duel = await db.get(Duel, "duel-1")
        assert duel.status == "cancelled"


@pytest.mark.anyio
async def test_handle_disconnect_is_a_noop_for_an_unknown_duel():
    # Never crashes for a stray disconnect event after the duel already ended some other way.
    await duel_engine.handle_disconnect("user-a", "does-not-exist")


@pytest.mark.anyio
async def test_handle_reconnect_is_a_noop_when_the_user_was_never_disconnected():
    state = _make_active_duel()
    ws_b = _FakeWebSocket()
    manager.connect("user-b", ws_b)

    await duel_engine.handle_reconnect("user-a")  # never disconnected - nothing to cancel

    assert ws_b.sent == []
    assert state.finished is False


@pytest.mark.anyio
async def test_handle_reconnect_is_a_noop_for_a_user_with_no_active_duel():
    await duel_engine.handle_reconnect("some-random-user")  # must not raise
