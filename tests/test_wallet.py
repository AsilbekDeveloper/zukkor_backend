"""Coin/Diamond iqtisodiyoti (2026-09-06, [[ai_cost_architecture]]) -
`app/services/wallet.py`ning asosiy mantig'i: signup bonusi, kunlik/
birinchi-o'yin/7-kunlik-streak Coin mukofotlari, referral bonusi, va
Diamond narxlash formulasi (haqiqiy token sarfidan)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.models.currency_transaction import CurrencyTransaction
from app.models.user import User
from app.services import wallet


async def _create_user(db, email: str, **kwargs) -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"), **kwargs)
    db.add(user)
    await db.flush()
    return user


# --- Diamond narxlash formulasi (sof funksiyalar, DB kerak emas) ---


def test_diamond_cost_scales_with_token_count():
    small = wallet.diamond_cost_from_tokens(input_tokens=100, output_tokens=200)
    large = wallet.diamond_cost_from_tokens(input_tokens=50_000, output_tokens=2_000)
    assert large > small


def test_diamond_cost_never_zero_even_for_tiny_requests():
    assert wallet.diamond_cost_from_tokens(input_tokens=1, output_tokens=1) >= 1


def test_diamond_cost_matches_4x_markup_formula_exactly():
    input_tokens, output_tokens = 10_000, 1_000
    expected_usd = (
        (input_tokens / 1_000_000) * settings.GEMINI_2027_INPUT_USD_PER_1M_TOKENS
        + (output_tokens / 1_000_000) * settings.GEMINI_2027_OUTPUT_USD_PER_1M_TOKENS
    ) * settings.DIAMOND_MARKUP_MULTIPLIER
    expected_diamonds = round(expected_usd / settings.USD_PER_DIAMOND)
    assert wallet.diamond_cost_from_tokens(input_tokens, output_tokens) == expected_diamonds


def test_estimate_diamond_cost_grows_with_question_count():
    small = wallet.estimate_diamond_cost(estimated_input_tokens=100, question_count=1)
    large = wallet.estimate_diamond_cost(estimated_input_tokens=100, question_count=20)
    assert large > small


# --- Signup bonusi + referral kod ---


@pytest.mark.anyio
async def test_apply_signup_defaults_grants_starting_diamonds_and_a_referral_code(db_session):
    user = await _create_user(db_session, "a@example.com")
    wallet.apply_signup_defaults(user)

    assert user.diamond_balance == settings.DEFAULT_STARTING_DIAMONDS
    assert user.referral_code is not None
    assert len(user.referral_code) == wallet._REFERRAL_CODE_LENGTH


@pytest.mark.anyio
async def test_signup_bonus_transaction_records_the_starting_grant(db_session):
    user = await _create_user(db_session, "a@example.com")
    wallet.apply_signup_defaults(user)
    db_session.add(user)
    await db_session.flush()
    db_session.add(wallet.signup_bonus_transaction(user))
    await db_session.commit()

    tx = (
        await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))
    ).scalar_one()
    assert tx.currency == "diamond"
    assert tx.amount == settings.DEFAULT_STARTING_DIAMONDS
    assert tx.reason == "signup_bonus"
    assert tx.balance_after == settings.DEFAULT_STARTING_DIAMONDS


# --- Kunlik kirish bonusi ---


@pytest.mark.anyio
async def test_daily_login_bonus_granted_once(db_session):
    user = await _create_user(db_session, "a@example.com")
    await db_session.commit()

    await wallet.check_and_grant_daily_login_bonus(db_session, user)
    await db_session.commit()
    assert user.coin_balance == wallet.DAILY_LOGIN_BONUS

    # Xuddi shu (Toshkent) kunida yana chaqirilsa - qayta berilmaydi.
    await wallet.check_and_grant_daily_login_bonus(db_session, user)
    await db_session.commit()
    assert user.coin_balance == wallet.DAILY_LOGIN_BONUS


@pytest.mark.anyio
async def test_daily_login_bonus_granted_again_on_a_new_day(db_session):
    user = await _create_user(db_session, "a@example.com")
    await db_session.commit()

    # "Kecha" allaqachon olingan deb belgilaymiz.
    user.last_daily_bonus_at = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    await wallet.check_and_grant_daily_login_bonus(db_session, user)
    await db_session.commit()

    assert user.coin_balance == wallet.DAILY_LOGIN_BONUS


# --- O'yin tugashi: birinchi-o'yin bonusi, 7-kunlik streak, referral ---


@pytest.mark.anyio
async def test_first_game_of_day_bonus_granted_once_per_day(db_session):
    user = await _create_user(db_session, "a@example.com", games_played=1)
    await db_session.commit()
    now = datetime.now(timezone.utc)

    await wallet.on_game_finished(db_session, user, now, is_first_game_ever=False)
    await db_session.commit()
    assert user.coin_balance == wallet.FIRST_GAME_OF_DAY_BONUS

    # Bugun ikkinchi o'yin - bonus qaytadan berilmaydi.
    await wallet.on_game_finished(db_session, user, now + timedelta(minutes=5), is_first_game_ever=False)
    await db_session.commit()
    assert user.coin_balance == wallet.FIRST_GAME_OF_DAY_BONUS


@pytest.mark.anyio
async def test_streak_bonus_granted_exactly_at_multiples_of_7(db_session):
    user = await _create_user(db_session, "a@example.com", current_streak=6, games_played=6)
    user.last_played_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    await wallet.on_game_finished(db_session, user, datetime.now(timezone.utc), is_first_game_ever=False)
    await db_session.commit()

    assert user.current_streak == 7
    # Kunning birinchi o'yini (5) + 7 kunlik streak bonusi (50).
    assert user.coin_balance == wallet.FIRST_GAME_OF_DAY_BONUS + wallet.STREAK_BONUS_7D


@pytest.mark.anyio
async def test_streak_bonus_not_granted_at_non_multiples_of_7(db_session):
    user = await _create_user(db_session, "a@example.com", current_streak=3, games_played=3)
    user.last_played_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    await wallet.on_game_finished(db_session, user, datetime.now(timezone.utc), is_first_game_ever=False)
    await db_session.commit()

    assert user.current_streak == 4
    assert user.coin_balance == wallet.FIRST_GAME_OF_DAY_BONUS  # streak bonusisiz


@pytest.mark.anyio
async def test_referral_bonus_paid_to_referrer_on_friends_first_game(db_session):
    referrer = await _create_user(db_session, "referrer@example.com")
    friend = await _create_user(db_session, "friend@example.com", referred_by_user_id=None)
    await db_session.flush()
    friend.referred_by_user_id = referrer.id
    friend.games_played = 1
    await db_session.commit()

    await wallet.on_game_finished(db_session, friend, datetime.now(timezone.utc), is_first_game_ever=True)
    await db_session.commit()

    assert referrer.coin_balance == wallet.REFERRAL_BONUS


@pytest.mark.anyio
async def test_referral_bonus_not_paid_on_second_game(db_session):
    referrer = await _create_user(db_session, "referrer2@example.com")
    friend = await _create_user(db_session, "friend2@example.com")
    await db_session.flush()
    friend.referred_by_user_id = referrer.id
    friend.games_played = 2
    await db_session.commit()

    await wallet.on_game_finished(db_session, friend, datetime.now(timezone.utc), is_first_game_ever=False)
    await db_session.commit()

    assert referrer.coin_balance == 0
