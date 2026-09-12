"""Coin/Diamond iqtisodiyoti (2026-09-06, [[ai_cost_architecture]]) -
`app/services/wallet.py`ning asosiy mantig'i: signup bonusi, kunlik/
birinchi-o'yin/7-kunlik-streak Coin mukofotlari, referral bonusi, va
Diamond narxlash formulasi (haqiqiy token sarfidan)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.models.app_config import AppConfig
from app.models.currency_transaction import CurrencyTransaction
from app.models.quiz import Category, Question
from app.models.user import User
from app.routers.wallet import get_diamond_pricing
from app.services import economy_config, wallet


def _default(key: str) -> int:
    return economy_config.DEFAULTS[key]


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


@pytest.mark.anyio
async def test_diamond_pricing_endpoint_matches_settings():
    result = await get_diamond_pricing()
    assert result.input_usd_per_1m_tokens == settings.GEMINI_2027_INPUT_USD_PER_1M_TOKENS
    assert result.output_usd_per_1m_tokens == settings.GEMINI_2027_OUTPUT_USD_PER_1M_TOKENS
    assert result.diamond_markup_multiplier == settings.DIAMOND_MARKUP_MULTIPLIER
    assert result.usd_per_diamond == settings.USD_PER_DIAMOND
    assert result.chars_per_token_estimate == settings.CHARS_PER_TOKEN_ESTIMATE


# --- Signup bonusi + referral kod ---


@pytest.mark.anyio
async def test_apply_signup_defaults_grants_starting_diamonds_coins_and_a_referral_code(db_session):
    user = await _create_user(db_session, "a@example.com")
    await wallet.apply_signup_defaults(db_session, user)

    assert user.diamond_balance == settings.DEFAULT_STARTING_DIAMONDS
    assert user.coin_balance == _default(economy_config.SIGNUP_COIN_BONUS)
    assert user.referral_code is not None
    assert len(user.referral_code) == wallet._REFERRAL_CODE_LENGTH


@pytest.mark.anyio
async def test_signup_bonus_transaction_records_the_starting_grant(db_session):
    user = await _create_user(db_session, "a@example.com")
    await wallet.apply_signup_defaults(db_session, user)
    db_session.add(user)
    await db_session.flush()
    db_session.add(wallet.signup_bonus_transaction(user))
    db_session.add(wallet.signup_coin_bonus_transaction(user))
    await db_session.commit()

    diamond_tx = (
        await db_session.execute(
            select(CurrencyTransaction).where(
                CurrencyTransaction.user_id == user.id, CurrencyTransaction.currency == "diamond"
            )
        )
    ).scalar_one()
    assert diamond_tx.amount == settings.DEFAULT_STARTING_DIAMONDS
    assert diamond_tx.reason == "signup_bonus"
    assert diamond_tx.balance_after == settings.DEFAULT_STARTING_DIAMONDS

    coin_tx = (
        await db_session.execute(
            select(CurrencyTransaction).where(
                CurrencyTransaction.user_id == user.id, CurrencyTransaction.currency == "coin"
            )
        )
    ).scalar_one()
    assert coin_tx.amount == _default(economy_config.SIGNUP_COIN_BONUS)
    assert coin_tx.reason == "signup_bonus"
    assert coin_tx.balance_after == _default(economy_config.SIGNUP_COIN_BONUS)


# --- Kunlik kirish bonusi ---


@pytest.mark.anyio
async def test_daily_login_bonus_granted_once(db_session):
    user = await _create_user(db_session, "a@example.com")
    await db_session.commit()

    await wallet.check_and_grant_daily_login_bonus(db_session, user)
    await db_session.commit()
    assert user.coin_balance == _default(economy_config.DAILY_LOGIN_BONUS)

    # Xuddi shu (Toshkent) kunida yana chaqirilsa - qayta berilmaydi.
    await wallet.check_and_grant_daily_login_bonus(db_session, user)
    await db_session.commit()
    assert user.coin_balance == _default(economy_config.DAILY_LOGIN_BONUS)


@pytest.mark.anyio
async def test_daily_login_bonus_granted_again_on_a_new_day(db_session):
    user = await _create_user(db_session, "a@example.com")
    await db_session.commit()

    # "Kecha" allaqachon olingan deb belgilaymiz.
    user.last_daily_bonus_at = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    await wallet.check_and_grant_daily_login_bonus(db_session, user)
    await db_session.commit()

    assert user.coin_balance == _default(economy_config.DAILY_LOGIN_BONUS)


# --- O'yin tugashi: birinchi-o'yin bonusi, 7-kunlik streak, referral ---


@pytest.mark.anyio
async def test_first_game_of_day_bonus_granted_once_per_day(db_session):
    user = await _create_user(db_session, "a@example.com", games_played=1)
    await db_session.commit()
    now = datetime.now(timezone.utc)

    await wallet.on_game_finished(db_session, user, now, is_first_game_ever=False)
    await db_session.commit()
    assert user.coin_balance == _default(economy_config.FIRST_GAME_OF_DAY_BONUS)

    # Bugun ikkinchi o'yin - bonus qaytadan berilmaydi.
    await wallet.on_game_finished(db_session, user, now + timedelta(minutes=5), is_first_game_ever=False)
    await db_session.commit()
    assert user.coin_balance == _default(economy_config.FIRST_GAME_OF_DAY_BONUS)


@pytest.mark.anyio
async def test_streak_bonus_granted_exactly_at_multiples_of_7(db_session):
    user = await _create_user(db_session, "a@example.com", current_streak=6, games_played=6)
    user.last_played_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    await wallet.on_game_finished(db_session, user, datetime.now(timezone.utc), is_first_game_ever=False)
    await db_session.commit()

    assert user.current_streak == 7
    # Kunning birinchi o'yini (5) + 7 kunlik streak bonusi (50).
    assert user.coin_balance == _default(economy_config.FIRST_GAME_OF_DAY_BONUS) + _default(economy_config.STREAK_BONUS_7D)


@pytest.mark.anyio
async def test_streak_bonus_not_granted_at_non_multiples_of_7(db_session):
    user = await _create_user(db_session, "a@example.com", current_streak=3, games_played=3)
    user.last_played_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    await wallet.on_game_finished(db_session, user, datetime.now(timezone.utc), is_first_game_ever=False)
    await db_session.commit()

    assert user.current_streak == 4
    assert user.coin_balance == _default(economy_config.FIRST_GAME_OF_DAY_BONUS)  # streak bonusisiz


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

    assert referrer.coin_balance == _default(economy_config.REFERRAL_BONUS)


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


# --- Savol o'ynash narxi + muallif ulushi (2026-09-12) ---


async def _create_category(db, **kwargs) -> Category:
    category = Category(name="Test", icon_name="star", color_key="coral", **kwargs)
    db.add(category)
    await db.flush()
    return category


async def _create_question(db, category_id: int, **kwargs) -> Question:
    question = Question(
        category_id=category_id,
        question_text="2+2?",
        options=["3", "4", "5", "6"],
        correct_option_index=1,
        **kwargs,
    )
    db.add(question)
    await db.flush()
    return question


@pytest.mark.anyio
async def test_record_with_require_sufficient_rejects_and_writes_nothing_when_balance_too_low(db_session):
    # 2026-09-13 prod-tayyorlik auditi: balans yetarliligi endi bitta
    # atomik SQL so'rovda tekshiriladi (`WHERE balance + amount >= 0`),
    # oldingi kabi alohida Python `if` emas - bu poyga holatini yopadi.
    # Bu test faqat mantiqni tekshiradi (haqiqiy parallel so'rovlarni
    # emas - db_session fixture'i bitta ulanish, SQLite esa Postgres'dagi
    # kabi qator darajasidagi qulflashni haqiqiy aks ettirmaydi).
    user = await _create_user(db_session, "broke@example.com", coin_balance=2)
    await db_session.commit()

    with pytest.raises(wallet.InsufficientBalanceError):
        await wallet._record(
            db_session, user, currency="coin", amount=-5, reason="test_debit", require_sufficient=True
        )

    assert user.coin_balance == 2
    all_tx = (await db_session.execute(select(CurrencyTransaction))).scalars().all()
    assert all_tx == []


@pytest.mark.anyio
async def test_record_with_require_sufficient_succeeds_at_exactly_zero_remaining(db_session):
    user = await _create_user(db_session, "exact@example.com", coin_balance=5)
    await db_session.commit()

    await wallet._record(db_session, user, currency="coin", amount=-5, reason="test_debit", require_sufficient=True)
    await db_session.commit()

    assert user.coin_balance == 0


@pytest.mark.anyio
async def test_debit_coin_subtracts_and_records_a_negative_ledger_entry(db_session):
    user = await _create_user(db_session, "a@example.com", coin_balance=10)
    await db_session.commit()

    await wallet.debit_coin(db_session, user, 3, "test_debit")
    await db_session.commit()

    assert user.coin_balance == 7
    tx = (
        await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))
    ).scalar_one()
    assert tx.amount == -3
    assert tx.balance_after == 7


@pytest.mark.anyio
async def test_charge_for_question_play_pays_a_share_to_the_questions_author(db_session):
    player = await _create_user(db_session, "player@example.com", coin_balance=10)
    author = await _create_user(db_session, "author@example.com")
    category = await _create_category(db_session)
    question = await _create_question(db_session, category.id, created_by_user_id=author.id)
    await db_session.commit()

    await wallet.charge_for_question_play(db_session, player, question.id)
    await db_session.commit()

    cost = _default(economy_config.COIN_COST_PER_QUESTION)
    share_percent = _default(economy_config.QUESTION_AUTHOR_SHARE_PERCENT)
    assert player.coin_balance == 10 - cost
    assert author.coin_balance == (cost * share_percent) // 100


@pytest.mark.anyio
async def test_charge_for_question_play_pays_nobody_for_an_official_question(db_session):
    player = await _create_user(db_session, "player2@example.com", coin_balance=10)
    category = await _create_category(db_session)
    question = await _create_question(db_session, category.id)  # created_by_user_id=None
    await db_session.commit()

    await wallet.charge_for_question_play(db_session, player, question.id)
    await db_session.commit()

    cost = _default(economy_config.COIN_COST_PER_QUESTION)
    assert player.coin_balance == 10 - cost
    all_tx = (
        await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.reason == "question_royalty"))
    ).scalars().all()
    assert all_tx == []


@pytest.mark.anyio
async def test_charge_for_question_play_does_not_pay_the_author_for_their_own_play(db_session):
    author = await _create_user(db_session, "author2@example.com", coin_balance=10)
    category = await _create_category(db_session)
    question = await _create_question(db_session, category.id, created_by_user_id=author.id)
    await db_session.commit()

    await wallet.charge_for_question_play(db_session, author, question.id)
    await db_session.commit()

    cost = _default(economy_config.COIN_COST_PER_QUESTION)
    # Faqat o'zidan yechildi - o'ziga muallif ulushi qo'shilmadi.
    assert author.coin_balance == 10 - cost


@pytest.mark.anyio
async def test_charge_for_question_play_charges_nothing_when_balance_is_insufficient(db_session):
    player = await _create_user(db_session, "poor@example.com", coin_balance=0)
    author = await _create_user(db_session, "author3@example.com")
    category = await _create_category(db_session)
    question = await _create_question(db_session, category.id, created_by_user_id=author.id)
    await db_session.commit()

    await wallet.charge_for_question_play(db_session, player, question.id)
    await db_session.commit()

    assert player.coin_balance == 0
    assert author.coin_balance == 0


@pytest.mark.anyio
async def test_economy_config_get_int_falls_back_to_default_when_unset(db_session):
    value = await economy_config.get_int(db_session, economy_config.COIN_COST_PER_QUESTION)
    assert value == economy_config.DEFAULTS[economy_config.COIN_COST_PER_QUESTION]


@pytest.mark.anyio
async def test_economy_config_get_int_uses_admin_overridden_value(db_session):
    db_session.add(AppConfig(key=economy_config.COIN_COST_PER_QUESTION, value="7"))
    await db_session.commit()

    value = await economy_config.get_int(db_session, economy_config.COIN_COST_PER_QUESTION)
    assert value == 7


@pytest.mark.anyio
async def test_seed_defaults_fills_missing_keys_without_overwriting_existing(db_session):
    db_session.add(AppConfig(key=economy_config.COIN_COST_PER_QUESTION, value="99"))
    await db_session.commit()

    await economy_config.seed_defaults(db_session)

    overridden = await economy_config.get_int(db_session, economy_config.COIN_COST_PER_QUESTION)
    assert overridden == 99  # admin qiymati saqlanib qoladi
    seeded = await economy_config.get_int(db_session, economy_config.DAILY_LOGIN_BONUS)
    assert seeded == economy_config.DEFAULTS[economy_config.DAILY_LOGIN_BONUS]


# --- Admin panel validatsiyasi (2026-09-13 prod-tayyorlik auditi) ---


def test_validate_value_accepts_a_valid_non_negative_integer():
    assert economy_config.validate_value(economy_config.DAILY_LOGIN_BONUS, "10") == 10
    assert economy_config.validate_value(economy_config.DAILY_LOGIN_BONUS, "0") == 0


def test_validate_value_rejects_a_non_numeric_string():
    with pytest.raises(ValueError):
        economy_config.validate_value(economy_config.DAILY_LOGIN_BONUS, "abc")


def test_validate_value_rejects_negative_numbers_for_any_key():
    with pytest.raises(ValueError):
        economy_config.validate_value(economy_config.DAILY_LOGIN_BONUS, "-5")
    with pytest.raises(ValueError):
        economy_config.validate_value(economy_config.QUESTION_AUTHOR_SHARE_PERCENT, "-1")


def test_validate_value_rejects_percent_keys_above_100():
    with pytest.raises(ValueError):
        economy_config.validate_value(economy_config.QUESTION_AUTHOR_SHARE_PERCENT, "150")


def test_validate_value_allows_percent_key_at_exactly_100():
    assert economy_config.validate_value(economy_config.QUESTION_AUTHOR_SHARE_PERCENT, "100") == 100


def test_validate_value_allows_non_percent_keys_above_100():
    # Faqat foiz turidagi kalitlar 100 bilan chegaralangan - bonus/narx
    # kabi kalitlar istalgan katta musbat songa ega bo'lishi mumkin.
    assert economy_config.validate_value(economy_config.STREAK_BONUS_7D, "500") == 500
