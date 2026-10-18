"""Yakka o'yin javobi `coin_delta` qaytaradi va balansni o'zgartiradi
(2026-10-14 iqtisodiyot qarori: rasmiy savolga birinchi to'g'ri javob +1,
xato -1, balans 0 dan tushmaydi)."""

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.currency_transaction import CurrencyTransaction
from app.models.quiz import Category, Question
from app.models.user import User
from app.routers.quiz import answer_question, start_quiz
from app.schemas.quiz import AnswerRequest, QuizStartRequest
from app.services import economy_config


async def _setup(db, *, coins: int) -> tuple[User, Category]:
    user = User(email="solo@example.com", hashed_password=await hash_password("Parol1234"), coin_balance=coins)
    category = Category(name="Tarix", icon_name="star", color_key="coral", is_active=True)
    db.add_all([user, category])
    await db.flush()
    for i in range(2):
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
    return user, category


@pytest.mark.anyio
async def test_correct_then_wrong_answer_returns_matching_coin_deltas(db_session):
    user, category = await _setup(db_session, coins=10)
    started = await start_quiz(QuizStartRequest(category_id=category.id, question_count=2), user, db_session)

    first_q = started.question
    right = await answer_question(
        started.session_id,
        AnswerRequest(session_question_id=first_q.session_question_id, selected_option=first_q.correct_option_index),
        user,
        db_session,
    )
    second_q = right.next_question
    wrong = await answer_question(
        started.session_id,
        AnswerRequest(
            session_question_id=second_q.session_question_id,
            selected_option=(second_q.correct_option_index + 1) % 4,
        ),
        user,
        db_session,
    )

    reward = economy_config.DEFAULTS[economy_config.CORRECT_ANSWER_COINS]
    penalty = economy_config.DEFAULTS[economy_config.WRONG_ANSWER_PENALTY_COINS]
    assert right.coin_delta == reward
    assert wrong.coin_delta == -penalty
    # Sessiya oxiridagi bonuslar (kunning birinchi o'yini) ham qo'shiladi -
    # shuning uchun faqat javoblar ta'sirini alohida tekshiramiz.
    first_game_bonus = economy_config.DEFAULTS[economy_config.FIRST_GAME_OF_DAY_BONUS]
    assert user.coin_balance == 10 + reward - penalty + first_game_bonus

    # Ikkala javob ham BITTA ledger qatoriga yozilgan (id = sessiya id'si).
    rows = (
        await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.reason == "solo_game"))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == started.session_id
    assert rows[0].amount == reward - penalty
    assert rows[0].extra["correct"] == 1 and rows[0].extra["wrong"] == 1
    assert rows[0].extra["category_name"] == "Tarix"

@pytest.mark.anyio
async def test_timeout_with_zero_balance_takes_nothing(db_session):
    user, category = await _setup(db_session, coins=0)
    started = await start_quiz(QuizStartRequest(category_id=category.id, question_count=2), user, db_session)

    timed_out = await answer_question(
        started.session_id,
        AnswerRequest(session_question_id=started.question.session_question_id, selected_option=None),
        user,
        db_session,
    )

    assert timed_out.coin_delta == 0
    assert user.coin_balance == 0


@pytest.mark.anyio
async def test_finishing_a_five_question_official_game_pays_the_referral_to_both_sides(db_session):
    from app.models.referral import ReferralReward

    referrer = User(email="inviter@example.com", hashed_password=await hash_password("Parol1234"), referral_code="INVITER1")
    friend = User(email="invited@example.com", hashed_password=await hash_password("Parol1234"))
    category = Category(name="Sport", icon_name="star", color_key="coral", is_active=True)
    db_session.add_all([referrer, friend, category])
    await db_session.flush()
    friend.referred_by_user_id = referrer.id
    for i in range(6):
        db_session.add(
            Question(
                category_id=category.id,
                question_text=f"Savol {i}?",
                options=["a", "b", "c", "d"],
                correct_option_index=0,
                is_active=True,
            )
        )
    await db_session.commit()

    started = await start_quiz(QuizStartRequest(category_id=category.id, question_count=5), friend, db_session)
    question = started.question
    for _ in range(5):
        answered = await answer_question(
            started.session_id,
            AnswerRequest(session_question_id=question.session_question_id, selected_option=question.correct_option_index),
            friend,
            db_session,
        )
        question = answered.next_question

    reward = economy_config.DEFAULTS[economy_config.REFERRAL_REWARD_COINS]
    assert (await db_session.get(ReferralReward, friend.id)) is not None
    assert referrer.coin_balance == reward
    # Do'st: 5 ta to'g'ri javob (+5) + kunning birinchi o'yini (+5) + taklif mukofoti.
    first_game_bonus = economy_config.DEFAULTS[economy_config.FIRST_GAME_OF_DAY_BONUS]
    assert friend.coin_balance >= reward + first_game_bonus
