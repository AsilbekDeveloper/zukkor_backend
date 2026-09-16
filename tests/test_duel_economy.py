"""Duel Coin stavkasi (2026-09-17, anti-farming/inflyatsiya himoyasi):

1. Anti-Rage-Quit - duel ACTIVE bo'lishi bilanoq (`start_duel`) ikkala
   o'yinchidan ham stavka darhol yechiladi; chiqib ketgan/aloqasi uzilgan
   o'yinchi buni qaytarib olmaydi.
2. Anti-Farming/Deflyatsiya - g'olib yutuq fondini SOLIQdan keyin oladi
   (ushlab qolingan qism hech kimga yozilmaydi - yo'q qilinadi); durang
   bo'lsa ikkalasi ham o'zining stavkasini soliqsiz qaytarib oladi.
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


# --- 1. Anti-Rage-Quit: stavka duel ACTIVE bo'lganda darhol yechiladi ---


@pytest.mark.anyio
async def test_start_duel_charges_the_stake_from_both_players(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine)

    await duel_engine.start_duel(category_id, "user-a", "user-b", 2)

    async with _isolated_engine() as db:
        user_a = await db.get(User, "user-a")
        user_b = await db.get(User, "user-b")
        assert user_a.coin_balance == 90
        assert user_b.coin_balance == 90

    [duel_id] = list(duel_engine._active_duels.keys())
    assert duel_engine._active_duels[duel_id].stake_coins == 10


@pytest.mark.anyio
async def test_start_duel_charges_nothing_when_one_player_lacks_the_stake(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=5, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine)

    await duel_engine.start_duel(category_id, "user-a", "user-b", 2)

    async with _isolated_engine() as db:
        user_a = await db.get(User, "user-a")
        user_b = await db.get(User, "user-b")
        assert user_a.coin_balance == 5
        # B had enough, but nothing is charged unless BOTH can pay.
        assert user_b.coin_balance == 100

    assert duel_engine._active_duels == {}
    async with _isolated_engine() as db:
        assert (await db.execute(select(Duel))).scalars().all() == []


@pytest.mark.anyio
async def test_start_duel_notifies_both_players_when_the_stake_cannot_be_charged(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=0, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine)
    ws_a, ws_b = _FakeWebSocket(), _FakeWebSocket()
    manager.connect("user-a", ws_a)
    manager.connect("user-b", ws_b)

    await duel_engine.start_duel(category_id, "user-a", "user-b", 2)

    expected = {"type": "duel_start_failed", "reason": "insufficient_coins", "stake_coins": 10}
    assert ws_a.sent == [expected]
    assert ws_b.sent == [expected]


@pytest.mark.anyio
async def test_forfeit_does_not_refund_the_leaving_players_stake(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine)
    await duel_engine.start_duel(category_id, "user-a", "user-b", 2)
    [duel_id] = list(duel_engine._active_duels.keys())

    await duel_engine.forfeit_duel("user-a", duel_id)

    async with _isolated_engine() as db:
        user_a = await db.get(User, "user-a")
        # Still down the 10 it staked at duel start - never refunded.
        assert user_a.coin_balance == 90


# --- 2. Anti-Farming/Deflyatsiya: g'olib soliqdan keyin oladi, durang soliqsiz qaytadi ---


@pytest.mark.anyio
async def test_finish_duel_awards_the_winner_the_pool_minus_tax(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)

    state, duel_id = await _play_and_finish(
        _isolated_engine, category_id, a_correct_count=3, b_correct_count=1, question_count=3
    )

    async with _isolated_engine() as db:
        user_a = await db.get(User, "user-a")
        user_b = await db.get(User, "user-b")
        # Pool = 10 + 10 = 20; 10% soliq = 2; g'olib 18 oladi.
        assert user_a.coin_balance == 100 - 10 + 18
        assert user_b.coin_balance == 100 - 10  # mag'lub - hech narsa qaytmaydi

        duel = await db.get(Duel, duel_id)
        assert duel.user_a_result == "won"
        assert duel.user_b_result == "lost"

    assert state.finished is True


@pytest.mark.anyio
async def test_finish_duel_burns_exactly_the_tax_amount(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)

    await _play_and_finish(_isolated_engine, category_id, a_correct_count=3, b_correct_count=0, question_count=3)

    async with _isolated_engine() as db:
        user_a = await db.get(User, "user-a")
        user_b = await db.get(User, "user-b")
        total_after = user_a.coin_balance + user_b.coin_balance
        # 200 boshlang'ich - 2 (10% of the 20-coin pool) = 198. Qolgan hech
        # kimga yozilmadi - butunlay yo'q qilindi.
        assert total_after == 200 - 2


@pytest.mark.anyio
async def test_finish_duel_refunds_both_stakes_on_a_draw_with_no_tax(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)

    state, duel_id = await _play_and_finish(
        _isolated_engine, category_id, a_correct_count=2, b_correct_count=2, question_count=3
    )

    async with _isolated_engine() as db:
        user_a = await db.get(User, "user-a")
        user_b = await db.get(User, "user-b")
        assert user_a.coin_balance == 100
        assert user_b.coin_balance == 100

        duel = await db.get(Duel, duel_id)
        assert duel.user_a_result == "draw"
        assert duel.user_b_result == "draw"


@pytest.mark.anyio
async def test_forfeit_pays_the_remaining_player_the_pool_minus_tax(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)
    await duel_engine.start_duel(category_id, "user-a", "user-b", 3)
    [duel_id] = list(duel_engine._active_duels.keys())

    await duel_engine.forfeit_duel("user-a", duel_id)

    async with _isolated_engine() as db:
        user_a = await db.get(User, "user-a")
        user_b = await db.get(User, "user-b")
        assert user_a.coin_balance == 90  # forfeits its own stake
        assert user_b.coin_balance == 90 + 18  # pool (20) minus 10% tax (2)


@pytest.mark.anyio
async def test_duel_stake_and_tax_are_configurable_via_economy_config(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=1)

    async with _isolated_engine() as db:
        db.add(AppConfig(key=economy_config.DUEL_STAKE_COINS, value="20"))
        db.add(AppConfig(key=economy_config.DUEL_TAX_PERCENT, value="50"))
        await db.commit()

    state, duel_id = await _play_and_finish(
        _isolated_engine, category_id, a_correct_count=1, b_correct_count=0, question_count=1
    )

    async with _isolated_engine() as db:
        user_a = await db.get(User, "user-a")
        user_b = await db.get(User, "user-b")
        # Stavka 20 - balans 80/80 bo'ladi. Pool = 40, 50% soliq = 20,
        # g'olib 20 oladi -> 80 + 20 = 100 (o'zgarmagandek ko'rinadi, lekin
        # sinovda muhimi: konfiguratsiya haqiqatan o'qildi).
        assert user_a.coin_balance == 100
        assert user_b.coin_balance == 80


# --- 3. Xavfsizlik: Duel yutug'i hech qachon Diamond bermaydi ---


@pytest.mark.anyio
async def test_finish_duel_never_touches_diamond_balance(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)

    async with _isolated_engine() as db:
        user_a = await db.get(User, "user-a")
        user_b = await db.get(User, "user-b")
        user_a.diamond_balance = 42
        user_b.diamond_balance = 77
        await db.commit()

    await _play_and_finish(_isolated_engine, category_id, a_correct_count=3, b_correct_count=0, question_count=3)

    async with _isolated_engine() as db:
        user_a = await db.get(User, "user-a")
        user_b = await db.get(User, "user-b")
        assert user_a.diamond_balance == 42
        assert user_b.diamond_balance == 77


@pytest.mark.anyio
async def test_forfeit_never_touches_diamond_balance(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)

    async with _isolated_engine() as db:
        user_a = await db.get(User, "user-a")
        user_b = await db.get(User, "user-b")
        user_a.diamond_balance = 5
        user_b.diamond_balance = 9
        await db.commit()

    await duel_engine.start_duel(category_id, "user-a", "user-b", 3)
    [duel_id] = list(duel_engine._active_duels.keys())
    await duel_engine.forfeit_duel("user-a", duel_id)

    async with _isolated_engine() as db:
        user_a = await db.get(User, "user-a")
        user_b = await db.get(User, "user-b")
        assert user_a.diamond_balance == 5
        assert user_b.diamond_balance == 9


@pytest.mark.anyio
async def test_all_duel_stake_ledger_entries_use_only_the_coin_currency(_isolated_engine):
    await _seed_users(_isolated_engine, a_coins=100, b_coins=100)
    category_id = await _seed_category_with_questions(_isolated_engine, count=3)

    await _play_and_finish(_isolated_engine, category_id, a_correct_count=3, b_correct_count=0, question_count=3)

    async with _isolated_engine() as db:
        rows = (await db.execute(select(CurrencyTransaction))).scalars().all()
        duel_rows = [r for r in rows if r.reason in ("duel_stake", "duel_prize", "duel_stake_refund")]
        assert duel_rows  # sanity - something was actually recorded
        assert all(r.currency == "coin" for r in duel_rows)
        reasons = {r.reason for r in duel_rows}
        assert reasons == {"duel_stake", "duel_prize"}
