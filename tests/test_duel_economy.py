"""Duel Coin qoidalari (2026-10-14 qarori - stavka YO'Q):

1. Duel boshlanganda hech kimdan hech narsa yechilmaydi, Coin'i yo'q
   o'yinchi ham duel o'ynay oladi.
2. G'olib tizimdan bonus oladi (raqibdan olinmaydi); mag'lub va durang
   hech narsa yo'qotmaydi; chiqib ketishda (forfeit) hech kimga bonus yo'q.
3. Xavfsizlik - Duel hech qachon Diamond bermaydi/yechmaydi, faqat Coin.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.app_config import AppConfig
from app.models.currency_transaction import CurrencyTransaction
from app.models.duel import Duel, DuelAnswer
from app.models.quiz import Category, Question
from app.models.user import User
from app.services import duel_engine, economy_config
from app.services.ws_manager import manager


class _FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


@pytest.fixture(autouse=True)
async def _isolated_engine(monkeypatch):
    # duel_engine.start_duel/_finish_duel/forfeit_duel write via the
    # module-level AsyncSessionLocal - point that at an isolated
    # in-memory SQLite engine (same pattern as test_duel_forfeit.py).
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(duel_engine, "AsyncSessionLocal", session_maker)
    # `start_duel` real 5s kutadi (pre-game countdown) - testlarda kerak
    # emas.
    monkeypatch.setattr(duel_engine, "PRE_GAME_COUNTDOWN_SECONDS", 0)

    yield session_maker

    await engine.dispose()
    duel_engine._active_duels.clear()
    duel_engine._user_active_duel.clear()
    manager.active.clear()


async def _seed_users(session_maker, *, a_coins: int = 100, b_coins: int = 100) -> None:
    # `last_first_game_bonus_at` seeded to "now" so `wallet.on_game_finished`
    # (called by `_finish_duel` for every completed game, unrelated to the
    # stake/prize logic under test here) doesn't also hand out its own
    # 5-Coin "first game of the day" bonus and throw off the balance math.
    now = datetime.now(timezone.utc)
    async with session_maker() as db:
        db.add(
            User(
                id="user-a", email="a@example.com", hashed_password="x",
                coin_balance=a_coins, last_first_game_bonus_at=now,
            )
        )
        db.add(
            User(
                id="user-b", email="b@example.com", hashed_password="x",
                coin_balance=b_coins, last_first_game_bonus_at=now,
            )
        )
        await db.commit()


async def _seed_category_with_questions(session_maker, count: int = 3) -> int:
    async with session_maker() as db:
        category = Category(name="Test", icon_name="star", color_key="coral", is_active=True)
        db.add(category)
        await db.flush()
        for i in range(count):
            db.add(
                Question(
                    category_id=category.id,
                    question_text=f"Savol {i}?",
                    options=["a", "b", "c", "d"],
                    correct_option_index=0,
                    is_active=True,
                )
            )
        await db.commit()
        return category.id


async def _play_and_finish(session_maker, category_id: int, *, a_correct_count: int, b_correct_count: int, question_count: int):
    await duel_engine.start_duel(category_id, "user-a", "user-b", question_count)
    [duel_id] = list(duel_engine._active_duels.keys())
    state = duel_engine._active_duels[duel_id]

    now = datetime.now(timezone.utc)
    async with session_maker() as db:
        for i, q in enumerate(state.questions):
            wrong_option = (q["correct_option"] + 1) % 4
            db.add(
                DuelAnswer(
                    duel_question_id=q["duel_question_id"],
                    user_id="user-a",
                    selected_option=q["correct_option"] if i < a_correct_count else wrong_option,
                    is_correct=i < a_correct_count,
                    answered_at=now,
                    elapsed_ms=1000,
                )
            )
            db.add(
                DuelAnswer(
                    duel_question_id=q["duel_question_id"],
                    user_id="user-b",
                    selected_option=q["correct_option"] if i < b_correct_count else wrong_option,
                    is_correct=i < b_correct_count,
                    answered_at=now,
                    elapsed_ms=1000,
                )
            )
        await db.commit()

    async with state.lock:
        await duel_engine._finish_duel(state)
    return state, duel_id


# --- 1. Stavka yo'q: duel boshida hech narsa yechilmaydi ---


@pytest.mark.anyio
async def test_start_duel_charges_nothing(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine)

    await duel_engine.start_duel(category_id, "user-a", "user-b", 2)

    async with _isolated_engine() as db:
        assert (await db.get(User, "user-a")).coin_balance == 100
        assert (await db.get(User, "user-b")).coin_balance == 100
    assert len(duel_engine._active_duels) == 1


@pytest.mark.anyio
async def test_players_with_zero_coins_can_still_duel(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=0, b_coins=0)
    category_id = await _seed_category_with_questions(_isolated_engine)
    ws_a = _FakeWebSocket()
    manager.connect("user-a", ws_a)

    await duel_engine.start_duel(category_id, "user-a", "user-b", 2)

    assert len(duel_engine._active_duels) == 1
    assert ws_a.sent[0]["type"] == "duel_started"
    assert "stake_coins" not in ws_a.sent[0]


# --- 2. G'olib bonusi ---


@pytest.mark.anyio
async def test_finish_duel_gives_the_winner_the_bonus_and_takes_nothing_from_the_loser(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)

    state, duel_id = await _play_and_finish(
        _isolated_engine, category_id, a_correct_count=3, b_correct_count=1, question_count=3
    )

    bonus = economy_config.DEFAULTS[economy_config.DUEL_WIN_BONUS]
    async with _isolated_engine() as db:
        assert (await db.get(User, "user-a")).coin_balance == 100 + bonus
        assert (await db.get(User, "user-b")).coin_balance == 100
        duel = await db.get(Duel, duel_id)
        assert (duel.user_a_result, duel.user_b_result) == ("won", "lost")

    assert state.finished is True


@pytest.mark.anyio
async def test_finish_duel_draw_gives_nobody_anything(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)

    _, duel_id = await _play_and_finish(
        _isolated_engine, category_id, a_correct_count=2, b_correct_count=2, question_count=3
    )

    async with _isolated_engine() as db:
        assert (await db.get(User, "user-a")).coin_balance == 100
        assert (await db.get(User, "user-b")).coin_balance == 100
        duel = await db.get(Duel, duel_id)
        assert (duel.user_a_result, duel.user_b_result) == ("draw", "draw")


@pytest.mark.anyio
async def test_a_win_with_zero_correct_answers_gives_no_bonus(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)

    await _play_and_finish(_isolated_engine, category_id, a_correct_count=0, b_correct_count=0, question_count=3)

    async with _isolated_engine() as db:
        assert (await db.get(User, "user-a")).coin_balance == 100
        assert (await db.get(User, "user-b")).coin_balance == 100


@pytest.mark.anyio
async def test_forfeit_gives_nobody_coins(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)
    ws_b = _FakeWebSocket()
    manager.connect("user-b", ws_b)
    await duel_engine.start_duel(category_id, "user-a", "user-b", 3)
    [duel_id] = list(duel_engine._active_duels.keys())

    await duel_engine.forfeit_duel("user-a", duel_id)

    async with _isolated_engine() as db:
        assert (await db.get(User, "user-a")).coin_balance == 100
        assert (await db.get(User, "user-b")).coin_balance == 100
    cancelled = [m for m in ws_b.sent if m["type"] == "duel_cancelled"]
    assert cancelled[0]["coins_earned"] == 0


@pytest.mark.anyio
async def test_duel_win_bonus_is_configurable_via_economy_config(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=1)

    async with _isolated_engine() as db:
        db.add(AppConfig(key=economy_config.DUEL_WIN_BONUS, value="12"))
        await db.commit()

    await _play_and_finish(_isolated_engine, category_id, a_correct_count=1, b_correct_count=0, question_count=1)

    async with _isolated_engine() as db:
        assert (await db.get(User, "user-a")).coin_balance == 112


# --- 3. Xavfsizlik: Duel hech qachon Diamond bermaydi ---


@pytest.mark.anyio
async def test_finish_duel_never_touches_diamond_balance(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)

    async with _isolated_engine() as db:
        (await db.get(User, "user-a")).diamond_balance = 42
        (await db.get(User, "user-b")).diamond_balance = 77
        await db.commit()

    await _play_and_finish(_isolated_engine, category_id, a_correct_count=3, b_correct_count=0, question_count=3)

    async with _isolated_engine() as db:
        assert (await db.get(User, "user-a")).diamond_balance == 42
        assert (await db.get(User, "user-b")).diamond_balance == 77


@pytest.mark.anyio
async def test_duel_ledger_entries_use_only_the_coin_currency(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)

    await _play_and_finish(_isolated_engine, category_id, a_correct_count=3, b_correct_count=0, question_count=3)

    async with _isolated_engine() as db:
        rows = (await db.execute(select(CurrencyTransaction))).scalars().all()
        duel_rows = [r for r in rows if r.reason == "duel_win_bonus"]
        assert len(duel_rows) == 1
        assert duel_rows[0].currency == "coin"
