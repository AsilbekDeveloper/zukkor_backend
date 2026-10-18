"""Coin/Diamond iqtisodiyoti (2026-09-06, [[ai_cost_architecture]]) -
`app/services/wallet.py`ning asosiy mantig'i: signup bonusi, kunlik/
birinchi-o'yin/7-kunlik-streak Coin mukofotlari, referral bonusi, va
Diamond narxlash formulasi (haqiqiy token sarfidan)."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

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
    user = User(email=email, hashed_password=await hash_password("Parol1234"), **kwargs)
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


# --- O'yin tugashi: birinchi-o'yin bonusi, 7-kunlik streak ---


@pytest.mark.anyio
async def test_first_game_of_day_bonus_granted_once_per_day(db_session):
    user = await _create_user(db_session, "a@example.com", games_played=1)
    await db_session.commit()
    now = datetime.now(timezone.utc)

    await wallet.on_game_finished(db_session, user, now)
    await db_session.commit()
    assert user.coin_balance == _default(economy_config.FIRST_GAME_OF_DAY_BONUS)

    # Bugun ikkinchi o'yin - bonus qaytadan berilmaydi.
    await wallet.on_game_finished(db_session, user, now + timedelta(minutes=5))
    await db_session.commit()
    assert user.coin_balance == _default(economy_config.FIRST_GAME_OF_DAY_BONUS)


@pytest.mark.anyio
async def test_streak_bonus_granted_exactly_at_multiples_of_7(db_session):
    user = await _create_user(db_session, "a@example.com", current_streak=6, games_played=6)
    user.last_played_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    await wallet.on_game_finished(db_session, user, datetime.now(timezone.utc))
    await db_session.commit()

    assert user.current_streak == 7
    # Kunning birinchi o'yini (5) + 7 kunlik streak bonusi (50).
    assert user.coin_balance == _default(economy_config.FIRST_GAME_OF_DAY_BONUS) + _default(economy_config.STREAK_BONUS_7D)


@pytest.mark.anyio
async def test_streak_bonus_not_granted_at_non_multiples_of_7(db_session):
    user = await _create_user(db_session, "a@example.com", current_streak=3, games_played=3)
    user.last_played_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    await wallet.on_game_finished(db_session, user, datetime.now(timezone.utc))
    await db_session.commit()

    assert user.current_streak == 4
    assert user.coin_balance == _default(economy_config.FIRST_GAME_OF_DAY_BONUS)  # streak bonusisiz


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


# --- apply_atomic_balance_delta (admin panel, 2026-09-13 prod-tayyorlik auditi) ---


@pytest.mark.anyio
async def test_apply_atomic_balance_delta_changes_balance_and_returns_new_value(db_session):
    user = await _create_user(db_session, "a@example.com", coin_balance=10)
    await db_session.commit()

    balance_after = await wallet.apply_atomic_balance_delta(db_session, user, currency="coin", amount=5)
    await db_session.commit()

    assert balance_after == 15
    assert user.coin_balance == 15


@pytest.mark.anyio
async def test_apply_atomic_balance_delta_creates_no_ledger_row(db_session):
    # `CurrencyTransactionAdmin`ning o'zi (SQLAdmin orqali) o'z ledger
    # qatorini yaratadi - agar bu funksiya HAM qator qo'shsa, bitta admin
    # amali uchun ikkita yozuv paydo bo'lardi.
    user = await _create_user(db_session, "a@example.com", diamond_balance=100)
    await db_session.commit()

    await wallet.apply_atomic_balance_delta(db_session, user, currency="diamond", amount=-30)
    await db_session.commit()

    assert user.diamond_balance == 70
    rows = (
        await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))
    ).scalars().all()
    assert rows == []


@pytest.mark.anyio
async def test_apply_atomic_balance_delta_rejects_unknown_currency(db_session):
    user = await _create_user(db_session, "a@example.com", coin_balance=10)
    await db_session.commit()

    with pytest.raises(ValueError):
        await wallet.apply_atomic_balance_delta(db_session, user, currency="gold", amount=5)


async def _solo(db, player, category, question, session_id, *, is_correct):
    delta = await wallet.apply_solo_answer_coins(
        db, player, question.id, session_id=session_id, category=category, is_correct=is_correct
    )
    await db.commit()
    return delta


async def _game_rows(db, user):
    return (
        await db.execute(
            select(CurrencyTransaction).where(
                CurrencyTransaction.user_id == user.id, CurrencyTransaction.reason == "solo_game"
            )
        )
    ).scalars().all()


@pytest.mark.anyio
async def test_solo_correct_answer_gives_coin_only_the_first_time(db_session):
    player = await _create_user(db_session, "player@example.com", coin_balance=10)
    category = await _create_category(db_session)
    question = await _create_question(db_session, category.id)
    await db_session.commit()

    first = await _solo(db_session, player, category, question, "game-1", is_correct=True)
    second = await _solo(db_session, player, category, question, "game-2", is_correct=True)

    reward = _default(economy_config.CORRECT_ANSWER_COINS)
    assert first == reward
    assert second == 0  # yod olingan savolni qayta o'ynab fermalab bo'lmaydi
    assert player.coin_balance == 10 + reward


@pytest.mark.anyio
async def test_solo_wrong_answer_takes_the_penalty(db_session):
    player = await _create_user(db_session, "wrong@example.com", coin_balance=10)
    category = await _create_category(db_session)
    question = await _create_question(db_session, category.id)
    await db_session.commit()

    delta = await _solo(db_session, player, category, question, "game-1", is_correct=False)

    penalty = _default(economy_config.WRONG_ANSWER_PENALTY_COINS)
    assert delta == -penalty
    assert player.coin_balance == 10 - penalty


@pytest.mark.anyio
async def test_solo_wrong_answer_never_takes_the_balance_below_zero(db_session):
    player = await _create_user(db_session, "poor@example.com", coin_balance=0)
    category = await _create_category(db_session)
    question = await _create_question(db_session, category.id)
    await db_session.commit()

    delta = await _solo(db_session, player, category, question, "game-1", is_correct=False)

    assert delta == 0
    assert player.coin_balance == 0
    assert await _game_rows(db_session, player) == []  # Coin o'zgarmagan o'yinga qator yo'q


@pytest.mark.anyio
async def test_solo_answers_in_user_or_ai_quizzes_move_no_coins(db_session):
    owner = await _create_user(db_session, "owner@example.com")
    player = await _create_user(db_session, "ugc@example.com", coin_balance=10)
    category = await _create_category(db_session, owner_user_id=owner.id)
    question = await _create_question(db_session, category.id)
    await db_session.commit()

    right = await _solo(db_session, player, category, question, "game-1", is_correct=True)
    wrong = await _solo(db_session, player, category, question, "game-1", is_correct=False)

    assert (right, wrong) == (0, 0)
    assert player.coin_balance == 10
    assert await _game_rows(db_session, player) == []


@pytest.mark.anyio
async def test_a_whole_game_is_one_ledger_row_with_a_breakdown(db_session):
    player = await _create_user(db_session, "game@example.com", coin_balance=20)
    category = await _create_category(db_session)
    questions = [await _create_question(db_session, category.id) for _ in range(5)]
    await db_session.commit()

    # 3 ta yangi savolga to'g'ri, 1 tasiga xato, 1 tasi avval to'g'ri topilgan.
    await _solo(db_session, player, category, questions[0], "game-1", is_correct=True)
    await _solo(db_session, player, category, questions[1], "game-1", is_correct=True)
    await _solo(db_session, player, category, questions[2], "game-1", is_correct=False)
    await _solo(db_session, player, category, questions[3], "game-1", is_correct=True)
    await _solo(db_session, player, category, questions[0], "game-1", is_correct=True)

    [row] = await _game_rows(db_session, player)
    assert row.id == "game-1"
    assert row.currency == "coin"
    assert row.amount == 3 - 1
    assert row.balance_after == player.coin_balance == 22
    assert row.extra == {
        "category_id": category.id,
        "category_name": "Test",
        "correct": 3,
        "wrong": 1,
        "repeat": 1,
        "coins_up": 3,
        "coins_down": 1,
    }


@pytest.mark.anyio
async def test_two_games_make_two_ledger_rows(db_session):
    player = await _create_user(db_session, "two@example.com", coin_balance=10)
    category = await _create_category(db_session)
    q1 = await _create_question(db_session, category.id)
    q2 = await _create_question(db_session, category.id)
    await db_session.commit()

    await _solo(db_session, player, category, q1, "game-a", is_correct=True)
    await _solo(db_session, player, category, q2, "game-b", is_correct=False)

    rows = await _game_rows(db_session, player)
    assert sorted(r.id for r in rows) == ["game-a", "game-b"]

@pytest.mark.anyio
async def test_question_approved_bonus_is_credited_to_the_author(db_session):
    author = await _create_user(db_session, "author@example.com")
    category = await _create_category(db_session)
    question = await _create_question(db_session, category.id, created_by_user_id=author.id)
    await db_session.commit()

    credited = await wallet.award_question_approved_bonus(db_session, author, question.id)
    await db_session.commit()

    bonus = _default(economy_config.QUESTION_APPROVED_BONUS)
    assert credited == bonus
    assert author.coin_balance == bonus


@pytest.mark.anyio
async def test_duel_win_bonus_stops_at_the_daily_limit(db_session):
    db_session.add(AppConfig(key=economy_config.DUEL_WIN_BONUS_DAILY_LIMIT, value="2"))
    winner = await _create_user(db_session, "winner@example.com")
    await db_session.commit()

    awarded = []
    for i in range(3):
        awarded.append(await wallet.award_duel_win_bonus(db_session, winner, f"duel-{i}"))
        await db_session.commit()

    bonus = _default(economy_config.DUEL_WIN_BONUS)
    assert awarded == [bonus, bonus, 0]
    assert winner.coin_balance == bonus * 2


@pytest.mark.anyio
async def test_economy_config_get_int_falls_back_to_default_when_unset(db_session):
    value = await economy_config.get_int(db_session, economy_config.DUEL_WIN_BONUS)
    assert value == economy_config.DEFAULTS[economy_config.DUEL_WIN_BONUS]


@pytest.mark.anyio
async def test_economy_config_get_int_uses_admin_overridden_value(db_session):
    db_session.add(AppConfig(key=economy_config.DUEL_WIN_BONUS, value="7"))
    await db_session.commit()

    value = await economy_config.get_int(db_session, economy_config.DUEL_WIN_BONUS)
    assert value == 7


@pytest.mark.anyio
async def test_seed_defaults_fills_missing_keys_without_overwriting_existing(db_session):
    db_session.add(AppConfig(key=economy_config.DUEL_WIN_BONUS, value="99"))
    await db_session.commit()

    await economy_config.seed_defaults(db_session)

    overridden = await economy_config.get_int(db_session, economy_config.DUEL_WIN_BONUS)
    assert overridden == 99  # admin qiymati saqlanib qoladi
    seeded = await economy_config.get_int(db_session, economy_config.DAILY_LOGIN_BONUS)
    assert seeded == economy_config.DEFAULTS[economy_config.DAILY_LOGIN_BONUS]


@pytest.mark.anyio
async def test_seed_defaults_removes_obsolete_stake_and_royalty_keys(db_session):
    db_session.add(AppConfig(key="duel_stake_coins", value="10"))
    db_session.add(AppConfig(key="question_author_share_percent", value="70"))
    await db_session.commit()

    await economy_config.seed_defaults(db_session)

    keys = set((await db_session.execute(select(AppConfig.key))).scalars().all())
    assert "duel_stake_coins" not in keys
    assert "question_author_share_percent" not in keys


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
        economy_config.validate_value(economy_config.DUEL_WIN_BONUS, "-1")


def test_validate_value_allows_large_values():
    assert economy_config.validate_value(economy_config.STREAK_BONUS_7D, "500") == 500


# --- reserve/release/finalize Diamond (2026-09-16, xavfsizlik auditi:
# AI-generatsiya endi Gemini chaqirilishidan OLDIN taxminiy narxni ATOMIK
# ravishda band qiladi, TOCTOU poyga holatini yopish uchun) ---


@pytest.mark.anyio
async def test_reserve_diamond_deducts_immediately_without_a_ledger_row(db_session):
    user = await _create_user(db_session, "reserve@example.com", diamond_balance=100)
    await db_session.commit()

    await wallet.reserve_diamond(db_session, user, 30)

    assert user.diamond_balance == 70
    # Ledger'da hali hech narsa yo'q - foydalanuvchiga faqat
    # `finalize_diamond_reservation` orqali BITTA yakuniy yozuv ko'rinadi.
    rows = (await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))).scalars().all()
    assert rows == []


@pytest.mark.anyio
async def test_reserve_diamond_rejects_and_writes_nothing_when_balance_too_low(db_session):
    user = await _create_user(db_session, "reserve_poor@example.com", diamond_balance=10)
    await db_session.commit()

    with pytest.raises(wallet.InsufficientBalanceError):
        await wallet.reserve_diamond(db_session, user, 30)

    assert user.diamond_balance == 10


@pytest.mark.anyio
async def test_release_diamond_reservation_refunds_in_full(db_session):
    user = await _create_user(db_session, "release@example.com", diamond_balance=100)
    await db_session.commit()

    await wallet.reserve_diamond(db_session, user, 30)
    assert user.diamond_balance == 70

    await wallet.release_diamond_reservation(db_session, user, 30)
    assert user.diamond_balance == 100
    # To'liq muvaffaqiyatsizlikda ham ledger'ga hech narsa yozilmaydi.
    rows = (await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))).scalars().all()
    assert rows == []


@pytest.mark.anyio
async def test_finalize_diamond_reservation_writes_exactly_one_row_at_the_actual_price(db_session):
    user = await _create_user(db_session, "finalize@example.com", diamond_balance=100)
    await db_session.commit()

    await wallet.reserve_diamond(db_session, user, 30)  # taxminiy narx
    await wallet.finalize_diamond_reservation(
        db_session, user, reserved_amount=30, actual_amount=45, reason="ai_generation", extra={"question_count": 5}
    )
    await db_session.commit()

    # Band qilingan 30 + qo'shimcha 15 = umumiy 45 yechildi.
    assert user.diamond_balance == 55
    rows = (await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].amount == -45
    assert rows[0].reason == "ai_generation"
    assert rows[0].balance_after == 55


@pytest.mark.anyio
async def test_finalize_diamond_reservation_refunds_the_difference_when_actual_cost_is_lower(db_session):
    user = await _create_user(db_session, "finalize_cheap@example.com", diamond_balance=100)
    await db_session.commit()

    await wallet.reserve_diamond(db_session, user, 30)
    await wallet.finalize_diamond_reservation(
        db_session, user, reserved_amount=30, actual_amount=10, reason="ai_generation"
    )
    await db_session.commit()

    assert user.diamond_balance == 90
    tx = (await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))).scalar_one()
    assert tx.amount == -10


@pytest.mark.anyio
async def test_finalize_diamond_reservation_can_take_balance_negative_when_actual_cost_is_higher(db_session):
    # Gemini xarajati ALLAQACHON qilingan - shu bosqichda "yetarli emas"
    # deb rad etish endi hech narsaga foyda bermaydi.
    user = await _create_user(db_session, "finalize_over@example.com", diamond_balance=20)
    await db_session.commit()

    await wallet.reserve_diamond(db_session, user, 20)
    await wallet.finalize_diamond_reservation(
        db_session, user, reserved_amount=20, actual_amount=35, reason="ai_generation"
    )
    await db_session.commit()

    assert user.diamond_balance == -15


@pytest.mark.anyio
async def test_reserve_diamond_never_overdraws_under_concurrent_requests(db_engine):
    """2026-09-16 xavfsizlik auditi topilmasining aynan o'zi: bitta
    foydalanuvchidan bir vaqtning o'zida kelgan bir nechta parallel
    so'rov, avvalgi (Python darajasidagi `if balance < cost`) tekshiruv
    bilan, hammasi bir xil eskirgan balansni ko'rib, hammasi "yetarli"
    deb noto'g'ri qarorga kelardi. Bu test har biri O'ZINING alohida DB
    session'i bilan (`test_wallet.py`dagi eski izohda aytilganidek, bitta
    session/ulanish bilan haqiqiy poyga aks etmaydi) 10 ta parallel
    `reserve_diamond` chaqiradi - balans 100, har biri 30 so'raydi, faqat
    3 tasi (90) muvaffaqiyatli bo'lishi, qolgan 7 tasi
    `InsufficientBalanceError` bilan rad etilishi va balans HECH QACHON
    manfiyga tushmasligi kerak."""
    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async with session_maker() as setup_db:
        user = await _create_user(setup_db, "race@example.com", diamond_balance=100)
        await setup_db.commit()
        user_id = user.id

    async def _attempt_reserve() -> bool:
        async with session_maker() as db:
            user_row = await db.get(User, user_id)
            try:
                await wallet.reserve_diamond(db, user_row, 30)
                return True
            except wallet.InsufficientBalanceError:
                return False

    results = await asyncio.gather(*[_attempt_reserve() for _ in range(10)])
    succeeded = sum(results)

    assert succeeded == 3  # 100 // 30

    async with session_maker() as verify_db:
        final_user = await verify_db.get(User, user_id)
        assert final_user.diamond_balance == 100 - succeeded * 30
        assert final_user.diamond_balance >= 0


# --- Diamond balansi qattiq chegarasi (2026-09-26, foydalanuvchi qarori:
# hech kimda 200dan ko'proq diamond turmasligi kerak) ---


@pytest.mark.anyio
async def test_credit_diamond_never_pushes_balance_above_the_cap(db_session):
    user = await _create_user(db_session, "cap1@example.com", diamond_balance=150)

    await wallet.credit_diamond(db_session, user, wallet.DIAMOND_BALANCE_CAP, "free_get_daily")

    assert user.diamond_balance == wallet.DIAMOND_BALANCE_CAP


@pytest.mark.anyio
async def test_credit_diamond_from_balance_already_at_the_cap_grants_nothing_more(db_session):
    user = await _create_user(db_session, "cap2@example.com", diamond_balance=wallet.DIAMOND_BALANCE_CAP)

    await wallet.credit_diamond(db_session, user, 50, "free_get_daily")

    assert user.diamond_balance == wallet.DIAMOND_BALANCE_CAP


@pytest.mark.anyio
async def test_credit_diamond_below_the_cap_still_credits_the_full_amount(db_session):
    user = await _create_user(db_session, "cap3@example.com", diamond_balance=10)

    await wallet.credit_diamond(db_session, user, 50, "free_get_daily")

    assert user.diamond_balance == 60


@pytest.mark.anyio
async def test_spending_diamond_is_not_affected_by_the_cap(db_session):
    user = await _create_user(db_session, "cap4@example.com", diamond_balance=wallet.DIAMOND_BALANCE_CAP)

    await wallet.apply_atomic_balance_delta(db_session, user, currency="diamond", amount=-50, require_sufficient=True)

    assert user.diamond_balance == wallet.DIAMOND_BALANCE_CAP - 50
