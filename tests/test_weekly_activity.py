"""GET /history/weekly-activity - 7 booleans (oldest first, today last)
telling Home's weekly-activity dots which of the last 7 days had at
least one completed game, across Solo/Duel/Lobby."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import hash_password
from app.models.duel import Duel
from app.models.lobby_game import LobbyGame, LobbyGameResult
from app.models.quiz import Category, QuizSession
from app.models.user import User
from app.routers.history import get_weekly_activity


async def _create_user(db, email="user@example.com") -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"))
    db.add(user)
    await db.flush()
    return user


async def _create_category(db) -> Category:
    category = Category(name="Tarix", icon_name="history", color_key="coral", is_active=True)
    db.add(category)
    await db.flush()
    return category


@pytest.mark.anyio
async def test_no_games_returns_all_false(db_session):
    user = await _create_user(db_session)
    await db_session.commit()

    result = await get_weekly_activity(current_user=user, db=db_session)

    assert result.days == [False] * 7


@pytest.mark.anyio
async def test_solo_game_today_marks_the_last_day(db_session):
    user = await _create_user(db_session)
    category = await _create_category(db_session)
    db_session.add(
        QuizSession(user_id=user.id, category_id=category.id, finished_at=datetime.now(timezone.utc))
    )
    await db_session.commit()

    result = await get_weekly_activity(current_user=user, db=db_session)

    assert result.days[-1] is True
    assert result.days[:-1] == [False] * 6


@pytest.mark.anyio
async def test_game_3_days_ago_marks_the_correct_slot(db_session):
    user = await _create_user(db_session)
    category = await _create_category(db_session)
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
    db_session.add(QuizSession(user_id=user.id, category_id=category.id, finished_at=three_days_ago))
    await db_session.commit()

    result = await get_weekly_activity(current_user=user, db=db_session)

    # days[0] = 6 days ago ... days[6] = today, so 3 days ago is index 3.
    assert result.days[3] is True
    assert sum(result.days) == 1


@pytest.mark.anyio
async def test_game_8_days_ago_falls_outside_the_window(db_session):
    user = await _create_user(db_session)
    category = await _create_category(db_session)
    too_old = datetime.now(timezone.utc) - timedelta(days=8)
    db_session.add(QuizSession(user_id=user.id, category_id=category.id, finished_at=too_old))
    await db_session.commit()

    result = await get_weekly_activity(current_user=user, db=db_session)

    assert result.days == [False] * 7


@pytest.mark.anyio
async def test_finished_duel_today_counts(db_session):
    user = await _create_user(db_session, "a@example.com")
    opponent = await _create_user(db_session, "b@example.com")
    category = await _create_category(db_session)
    db_session.add(
        Duel(
            category_id=category.id,
            user_a_id=user.id,
            user_b_id=opponent.id,
            total_questions=5,
            status="finished",
            finished_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    result = await get_weekly_activity(current_user=user, db=db_session)

    assert result.days[-1] is True


@pytest.mark.anyio
async def test_finished_lobby_game_today_counts(db_session):
    user = await _create_user(db_session)
    category = await _create_category(db_session)
    game = LobbyGame(
        category_id=category.id,
        total_questions=5,
        participant_count=1,
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(game)
    await db_session.flush()
    db_session.add(
        LobbyGameResult(
            lobby_game_id=game.id, user_id=user.id, rank=1, correct=5, total_time_ms=1000, ball=1000, xp=10
        )
    )
    await db_session.commit()

    result = await get_weekly_activity(current_user=user, db=db_session)

    assert result.days[-1] is True
