"""`lobby_manager._finish_game` - unlike test_lobby_leave_mid_game.py
(which mocks `_finish_game` out entirely to test the leave/removal
bookkeeping in isolation), this exercises the REAL finish logic against
an in-memory DB, specifically the case that crashed in production-
readiness review (2026-09-13): the game ending EARLY because other
participants left, while the sole remaining player hasn't answered
every question yet."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.lobby_game import LobbyGameResult
from app.models.quiz import Category
from app.models.user import User
from app.services import lobby_manager


class _FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


def _user(user_id: str) -> User:
    # Bu testlar parolni hech qachon tekshirmaydi - bcrypt endi async
    # (`asyncio.to_thread`) bo'lgani uchun, faqat DB ustunini to'ldirish
    # uchun soxta qiymat, haqiqiy hash emas.
    return User(id=user_id, email=f"{user_id}@example.com", hashed_password="not-a-real-hash")


@pytest.fixture(autouse=True)
async def _test_db(monkeypatch):
    # `_finish_game` writes via the module-level AsyncSessionLocal - point
    # that at an isolated in-memory SQLite engine (same pattern as
    # test_duel_forfeit.py) instead of the real DATABASE_URL.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(lobby_manager, "AsyncSessionLocal", session_maker)

    async with session_maker() as db:
        db.add(Category(id=1, name="Test", icon_name="star", color_key="coral"))
        for uid in ("user-a", "user-b", "user-c"):
            db.add(_user(uid))
        await db.commit()

    yield session_maker

    await engine.dispose()
    lobby_manager._rooms.clear()
    lobby_manager._room_code_index.clear()


def _make_room_with_game(participant_ids: list[str], total_questions: int = 10) -> lobby_manager._Room:
    room_id = "room-1"
    room = lobby_manager._Room(room_id, "654321", host_participant_id=participant_ids[0])
    for i, pid in enumerate(participant_ids):
        room.participants[pid] = lobby_manager._Participant(
            pid, _user(f"user-{pid}"), _FakeWebSocket(), is_host=(i == 0)
        )

    participant_user_ids = {pid: f"user-{pid}" for pid in participant_ids}
    game = lobby_manager._GameState(
        room_id, category_id=1, total_questions=total_questions, participant_user_ids=participant_user_ids
    )
    game.questions = [
        {"question_id": i + 1, "question_text": f"Savol {i + 1}", "correct_option": 0, "time_limit_ms": 15000}
        for i in range(total_questions)
    ]
    room.game = game
    lobby_manager._rooms[room_id] = room
    lobby_manager._room_code_index[room.room_code] = room_id
    return room


def _answer(question_id: int, *, is_correct: bool, elapsed_ms: int = 3000, time_limit_ms: int = 15000) -> dict:
    return {"question_id": question_id, "elapsed_ms": elapsed_ms, "is_correct": is_correct, "time_limit_ms": time_limit_ms}


@pytest.mark.anyio
async def test_finish_game_does_not_crash_when_the_survivor_has_not_answered_every_question(_test_db):
    # 10-question game; "a" has only answered 4 of them (questions 1-4)
    # when the other two participants leave and force an early finish -
    # this exact shape raised IndexError before the fix (_breakdown_for
    # iterated range(total_questions)=10 against a 4-entry answers_log).
    room = _make_room_with_game(["a", "b", "c"], total_questions=10)
    room.game.answers_log["a"] = [_answer(i + 1, is_correct=True) for i in range(4)]
    del room.game.participant_user_ids["b"]
    del room.game.participant_user_ids["c"]
    game = room.game

    await lobby_manager._finish_game(room)  # must not raise

    assert game.finished is True
    assert room.game is None  # _finish_game clears it once done
    async with _test_db() as db:
        result = (await db.execute(LobbyGameResult.__table__.select())).mappings().all()
    survivor_result = next(r for r in result if r["user_id"] == "user-a")
    assert survivor_result["correct"] == 4


@pytest.mark.anyio
async def test_finish_game_breakdown_only_covers_actually_answered_questions(_test_db, monkeypatch):
    sent: list[dict] = []

    async def _fake_safe_send(websocket, message):
        sent.append(message)
        return True

    monkeypatch.setattr(lobby_manager, "_safe_send", _fake_safe_send)

    room = _make_room_with_game(["a", "b"], total_questions=10)
    room.game.answers_log["a"] = [_answer(i + 1, is_correct=(i % 2 == 0)) for i in range(3)]
    del room.game.participant_user_ids["b"]

    await lobby_manager._finish_game(room)

    finished_messages = [m for m in sent if m.get("type") == "lobby_game_finished"]
    assert len(finished_messages) == 1
    breakdown = finished_messages[0]["breakdown"]
    assert len(breakdown) == 3
    assert [b["is_correct"] for b in breakdown] == [True, False, True]


@pytest.mark.anyio
async def test_finish_game_handles_a_survivor_with_zero_answered_questions(_test_db):
    # The most extreme case of the same bug shape: everyone else leaves
    # before the remaining player has answered a single question.
    room = _make_room_with_game(["a", "b"], total_questions=10)
    del room.game.participant_user_ids["b"]
    game = room.game

    await lobby_manager._finish_game(room)  # must not raise

    assert game.finished is True
    assert room.game is None
