"""Admin "Statistika" sahifasi hisoblaydigan ko'rsatkichlar
(`app/services/admin_analytics.py`) - 2026-09-29, foydalanuvchi so'rovi:
"admin hamma narsani bilib turishi kerak"."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import hash_password
from app.models.ai_quiz_job import AiQuizGenerationJob
from app.models.currency_transaction import CurrencyTransaction
from app.models.quiz import Category
from app.models.user import User
from app.services.admin_analytics import compute_analytics


async def _create_user(db, email: str, **kwargs) -> User:
    user = User(email=email, hashed_password=await hash_password("Parol1234"), **kwargs)
    db.add(user)
    await db.flush()
    return user


@pytest.mark.anyio
async def test_counts_total_and_active_users(db_session):
    await _create_user(db_session, "a@example.com", is_active=True)
    await _create_user(db_session, "b@example.com", is_active=False)
    await db_session.commit()

    stats = await compute_analytics(db_session)

    assert stats.total_users == 2
    assert stats.active_users == 1


@pytest.mark.anyio
async def test_dau_counts_only_users_who_played_today(db_session):
    now = datetime.now(timezone.utc)
    await _create_user(db_session, "today@example.com", last_played_at=now)
    await _create_user(db_session, "old@example.com", last_played_at=now - timedelta(days=10))
    await _create_user(db_session, "never@example.com", last_played_at=None)
    await db_session.commit()

    stats = await compute_analytics(db_session)

    assert stats.dau == 1
    assert stats.wau == 1  # "old" is 10 days ago, outside the 7-day window


@pytest.mark.anyio
async def test_sums_coin_and_diamond_in_circulation(db_session):
    await _create_user(db_session, "rich@example.com", coin_balance=100, diamond_balance=50)
    await _create_user(db_session, "poor@example.com", coin_balance=5, diamond_balance=0)
    await db_session.commit()

    stats = await compute_analytics(db_session)

    assert stats.total_coin_in_circulation == 105
    assert stats.total_diamond_in_circulation == 50


@pytest.mark.anyio
async def test_top_categories_ranked_by_play_count(db_session):
    db_session.add_all(
        [
            Category(name="Kam o'ynalgan", icon_name="a", color_key="coral", is_active=True, play_count=2),
            Category(name="Ko'p o'ynalgan", icon_name="b", color_key="teal", is_active=True, play_count=50),
            # Shaxsiy (owner_user_id bor) kategoriya - global reytingga kirmasligi kerak.
            Category(
                name="Shaxsiy quiz", icon_name="c", color_key="pink", is_active=True,
                play_count=999, owner_user_id="fake-owner-id",
            ),
        ]
    )
    await db_session.commit()

    stats = await compute_analytics(db_session)

    names = [c.name for c in stats.top_categories]
    assert names[0] == "Ko'p o'ynalgan"
    assert "Shaxsiy quiz" not in names


@pytest.mark.anyio
async def test_export_stats_count_only_recent_quiz_export_transactions(db_session):
    user = await _create_user(db_session, "teacher@example.com", coin_balance=0, diamond_balance=0)
    await db_session.commit()

    db_session.add_all(
        [
            CurrencyTransaction(
                user_id=user.id, currency="diamond", amount=-10, reason="quiz_export", balance_after=90,
            ),
            CurrencyTransaction(
                user_id=user.id, currency="diamond", amount=-6, reason="quiz_export", balance_after=84,
                created_at=datetime.now(timezone.utc) - timedelta(days=40),
            ),
            CurrencyTransaction(
                user_id=user.id, currency="coin", amount=-1, reason="question_play", balance_after=0,
            ),
        ]
    )
    await db_session.commit()

    stats = await compute_analytics(db_session)

    assert stats.export_count_30d == 1
    assert stats.export_diamond_spent_30d == 10


@pytest.mark.anyio
async def test_counts_ai_generation_job_outcomes(db_session):
    user = await _create_user(db_session, "aiuser@example.com")
    await db_session.commit()

    db_session.add_all(
        [
            AiQuizGenerationJob(user_id=user.id, status="completed", question_count=10),
            AiQuizGenerationJob(user_id=user.id, status="completed", question_count=5),
            AiQuizGenerationJob(user_id=user.id, status="failed", question_count=10),
            AiQuizGenerationJob(user_id=user.id, status="pending", question_count=10),
        ]
    )
    await db_session.commit()

    stats = await compute_analytics(db_session)

    assert stats.ai_jobs_completed == 2
    assert stats.ai_jobs_failed == 1
