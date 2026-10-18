"""Taklif (referral) tizimi (2026-10-15): mukofot ikkala tomonga, taklif
qilingan foydalanuvchi birinchi SIFATLI o'yinini tugatganda."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import hash_password
from app.models import ai_quiz_job  # noqa: F401 - statistika shu jadvalni ham o'qiydi (fixture jadvallarni oldindan yaratadi)
from app.models.app_config import AppConfig
from app.models.currency_transaction import CurrencyTransaction
from app.models.referral import ReferralClick, ReferralReward
from app.models.quiz import Category
from app.models.user import User
from app.routers import referrals as referrals_router
from app.schemas.referral import ApplyReferralRequest
from app.services import economy_config, referral


def _default(key: str) -> int:
    return economy_config.DEFAULTS[key]


async def _user(db, email: str, **kwargs) -> User:
    user = User(email=email, hashed_password=await hash_password("Parol1234"), **kwargs)
    db.add(user)
    await db.flush()
    return user


async def _category(db, **kwargs) -> Category:
    category = Category(name="Tarix", icon_name="star", color_key="coral", is_active=True, **kwargs)
    db.add(category)
    await db.flush()
    return category


async def _pair(db) -> tuple[User, User]:
    referrer = await _user(db, "referrer@example.com", referral_code="REFCODE1")
    friend = await _user(db, "friend@example.com", referral_code="FRIEND01")
    friend.referred_by_user_id = referrer.id
    await db.commit()
    return referrer, friend


# --- Mukofot ---


@pytest.mark.anyio
async def test_both_sides_get_coins_and_diamonds_after_a_qualifying_game(db_session):
    referrer, friend = await _pair(db_session)
    category = await _category(db_session)
    referrer_diamonds, friend_diamonds = referrer.diamond_balance, friend.diamond_balance

    reward = await referral.award_if_qualified(db_session, friend, category=category, question_count=10)
    await db_session.commit()

    coins = _default(economy_config.REFERRAL_REWARD_COINS)
    diamonds = _default(economy_config.REFERRAL_REWARD_DIAMONDS)
    assert reward is not None
    assert referrer.coin_balance == coins and friend.coin_balance == coins
    assert referrer.diamond_balance == referrer_diamonds + diamonds
    assert friend.diamond_balance == friend_diamonds + diamonds

    saved = await db_session.get(ReferralReward, friend.id)
    assert (saved.referrer_coins, saved.referrer_diamonds) == (coins, diamonds)
    assert (saved.referred_coins, saved.referred_diamonds) == (coins, diamonds)

    reasons = {
        r.reason
        for r in (await db_session.execute(select(CurrencyTransaction))).scalars().all()
    }
    assert {"referral", "referral_welcome"} <= reasons


@pytest.mark.anyio
async def test_reward_is_given_only_once(db_session):
    referrer, friend = await _pair(db_session)
    category = await _category(db_session)

    first = await referral.award_if_qualified(db_session, friend, category=category, question_count=10)
    await db_session.commit()
    second = await referral.award_if_qualified(db_session, friend, category=category, question_count=10)
    await db_session.commit()

    assert first is not None and second is None
    assert referrer.coin_balance == _default(economy_config.REFERRAL_REWARD_COINS)


@pytest.mark.anyio
async def test_a_short_game_does_not_qualify_but_a_later_long_one_does(db_session):
    referrer, friend = await _pair(db_session)
    category = await _category(db_session)

    short = await referral.award_if_qualified(db_session, friend, category=category, question_count=1)
    assert short is None and referrer.coin_balance == 0

    long = await referral.award_if_qualified(db_session, friend, category=category, question_count=5)
    await db_session.commit()
    assert long is not None
    assert referrer.coin_balance == _default(economy_config.REFERRAL_REWARD_COINS)


@pytest.mark.anyio
async def test_user_or_ai_quizzes_do_not_qualify(db_session):
    referrer, friend = await _pair(db_session)
    category = await _category(db_session, owner_user_id=friend.id)

    reward = await referral.award_if_qualified(db_session, friend, category=category, question_count=20)

    assert reward is None
    assert referrer.coin_balance == 0


@pytest.mark.anyio
async def test_a_user_nobody_invited_gets_nothing(db_session):
    loner = await _user(db_session, "loner@example.com")
    category = await _category(db_session)
    await db_session.commit()

    assert await referral.award_if_qualified(db_session, loner, category=category, question_count=10) is None


@pytest.mark.anyio
async def test_diamond_balance_cap_limits_the_credited_amount(db_session):
    referrer, friend = await _pair(db_session)
    referrer.diamond_balance = 150  # chegara 200 - faqat 50 sig'adi
    category = await _category(db_session)

    await referral.award_if_qualified(db_session, friend, category=category, question_count=10)
    await db_session.commit()

    assert referrer.diamond_balance == 200
    saved = await db_session.get(ReferralReward, friend.id)
    assert saved.referrer_diamonds == 50  # haqiqatda qo'shilgan miqdor


@pytest.mark.anyio
async def test_daily_limit_defers_the_reward_to_a_later_game(db_session):
    referrer = await _user(db_session, "limited@example.com", referral_code="LIMITED1")
    first_friend = await _user(db_session, "f1@example.com")
    second_friend = await _user(db_session, "f2@example.com")
    first_friend.referred_by_user_id = second_friend.referred_by_user_id = referrer.id
    db_session.add(AppConfig(key=economy_config.REFERRAL_DAILY_LIMIT, value="1"))
    category = await _category(db_session)
    await db_session.commit()

    one = await referral.award_if_qualified(db_session, first_friend, category=category, question_count=10)
    await db_session.commit()
    two = await referral.award_if_qualified(db_session, second_friend, category=category, question_count=10)

    assert one is not None
    assert two is None  # bugungi limit tugadi - ertaga qayta uriniladi


@pytest.mark.anyio
async def test_rewards_are_unlimited_by_default(db_session):
    referrer = await _user(db_session, "popular@example.com", referral_code="POPULAR1")
    category = await _category(db_session)
    friends = [await _user(db_session, f"p{i}@example.com") for i in range(5)]
    for friend in friends:
        friend.referred_by_user_id = referrer.id
    await db_session.commit()

    for friend in friends:
        assert await referral.award_if_qualified(db_session, friend, category=category, question_count=10)
        await db_session.commit()

    assert referrer.coin_balance == 5 * _default(economy_config.REFERRAL_REWARD_COINS)


# --- Kodni qo'lda kiritish ---


@pytest.mark.anyio
async def test_apply_code_links_a_new_user_to_the_referrer(db_session):
    referrer = await _user(db_session, "ref@example.com", referral_code="HELLO123")
    newbie = await _user(db_session, "newbie@example.com")
    await db_session.commit()

    await referral.apply_code(db_session, newbie, " hello123 ")

    assert newbie.referred_by_user_id == referrer.id


@pytest.mark.anyio
@pytest.mark.parametrize(
    "setup, expected",
    [
        ("unknown", "topilmadi"),
        ("own", "o'zingizning"),
        ("already", "allaqachon"),
        ("old", "7 kun"),
    ],
)
async def test_apply_code_rejections(db_session, setup, expected):
    referrer = await _user(db_session, "r@example.com", referral_code="REAL1234")
    user = await _user(db_session, "u@example.com", referral_code="MINE1234")
    code = "REAL1234"
    if setup == "unknown":
        code = "NOPE9999"
    elif setup == "own":
        code = "MINE1234"
    elif setup == "already":
        user.referred_by_user_id = referrer.id
    elif setup == "old":
        user.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    await db_session.commit()

    with pytest.raises(referral.ApplyCodeError) as error:
        await referral.apply_code(db_session, user, code)
    assert expected in str(error.value).lower()


# --- Endpointlar ---


class _FakeRequest:
    """`_public_base_url` va havola ochilishi uchun minimal so'rov."""

    def __init__(self, user_agent: str = "", base: str = "https://zukkor.test/"):
        self.headers = {"user-agent": user_agent}
        self.base_url = base
        self.url = type("U", (), {"hostname": "zukkor.test"})()


@pytest.mark.anyio
async def test_my_referral_shows_code_link_and_stats(db_session):
    referrer, friend = await _pair(db_session)
    category = await _category(db_session)
    await referral.award_if_qualified(db_session, friend, category=category, question_count=10)
    await db_session.commit()

    out = await referrals_router.get_my_referral(_FakeRequest(), referrer, db_session)

    assert out.code == "REFCODE1"
    assert out.link == "https://zukkor.test/r/REFCODE1"
    assert out.registered_count == 1
    assert out.rewarded_count == 1
    assert out.coins_earned == _default(economy_config.REFERRAL_REWARD_COINS)
    assert out.reward_diamonds == _default(economy_config.REFERRAL_REWARD_DIAMONDS)
    assert out.can_apply_code is True  # o'zi hali hech kimning kodini kiritmagan


@pytest.mark.anyio
async def test_old_users_without_a_code_get_one_on_first_request(db_session):
    user = await _user(db_session, "legacy@example.com")
    await db_session.commit()
    assert user.referral_code is None

    out = await referrals_router.get_my_referral(_FakeRequest(), user, db_session)

    assert out.code and out.code == user.referral_code


@pytest.mark.anyio
async def test_apply_endpoint_maps_errors_to_400(db_session):
    user = await _user(db_session, "x@example.com")
    await db_session.commit()

    with pytest.raises(HTTPException) as error:
        await referrals_router.apply_referral_code.__wrapped__(
            _FakeRequest(), ApplyReferralRequest(code="NOPE9999"), user, db_session
        )
    assert error.value.status_code == 400


@pytest.mark.anyio
async def test_android_link_redirects_to_play_store_with_the_code_and_counts_the_click(db_session):
    await _user(db_session, "owner@example.com", referral_code="SHARE123")
    await db_session.commit()

    response = await referrals_router.open_referral_link.__wrapped__(
        "share123", _FakeRequest("Mozilla/5.0 (Linux; Android 14)"), db_session
    )

    assert response.status_code == 302
    assert "play.google.com/store/apps/details?id=com.zukkor.app" in response.headers["location"]
    assert "referrer=ref%3DSHARE123" in response.headers["location"]
    clicks = (await db_session.execute(select(ReferralClick))).scalars().all()
    assert [(c.code, c.platform) for c in clicks] == [("SHARE123", "android")]


@pytest.mark.anyio
async def test_desktop_link_shows_the_code_page(db_session):
    await _user(db_session, "owner2@example.com", referral_code="SHARE456")
    await db_session.commit()

    response = await referrals_router.open_referral_link.__wrapped__(
        "SHARE456", _FakeRequest("Mozilla/5.0 (Windows NT 10.0)"), db_session
    )

    assert response.status_code == 200
    assert b"SHARE456" in response.body


@pytest.mark.anyio
async def test_unknown_code_is_a_404_page_and_is_not_counted(db_session):
    response = await referrals_router.open_referral_link.__wrapped__(
        "NOPE9999", _FakeRequest("Android"), db_session
    )

    assert response.status_code == 404
    assert (await db_session.execute(select(ReferralClick))).scalars().all() == []


@pytest.mark.anyio
async def test_admin_statistics_page_shows_the_referral_funnel(db_session):
    from app.admin import _render_analytics_page
    from app.services.admin_analytics import compute_analytics

    referrer, friend = await _pair(db_session)
    category = await _category(db_session)
    db_session.add(ReferralClick(code="REFCODE1", platform="android"))
    await referral.award_if_qualified(db_session, friend, category=category, question_count=10)
    await db_session.commit()

    stats = await compute_analytics(db_session)

    assert (stats.referral_clicks, stats.referral_registrations, stats.referral_rewarded) == (1, 1, 1)
    coins = _default(economy_config.REFERRAL_REWARD_COINS)
    assert stats.referral_coins_paid == coins * 2
    page = _render_analytics_page(stats)
    assert "Taklif voronkasi" in page
