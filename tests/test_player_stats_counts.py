"""`friends_count`/`public_quiz_count` on `GET /leaderboard/{user_id}`
(2026-09-12) - Profile/Player Detail screens show these directly, no
separate endpoint needed."""

import pytest

from app.core.security import hash_password
from app.models.friendship import Friendship
from app.models.quiz import Category
from app.models.user import User
from app.routers.leaderboard import get_player_stats


async def _create_user(db, email: str, **kwargs) -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"), **kwargs)
    db.add(user)
    await db.flush()
    return user


@pytest.mark.anyio
async def test_friends_count_defaults_to_zero(db_session):
    user = await _create_user(db_session, "lonely@example.com")
    await db_session.commit()

    result = await get_player_stats(user.id, db=db_session, current_user=user)

    assert result.friends_count == 0


@pytest.mark.anyio
async def test_friends_count_matches_the_users_own_friendship_rows(db_session):
    user = await _create_user(db_session, "a@example.com")
    friend_one = await _create_user(db_session, "b@example.com")
    friend_two = await _create_user(db_session, "c@example.com")
    # A real accepted friendship is stored as a row in EACH direction (see
    # `_create_mutual_friendship`) - only `user_id == user.id` rows should
    # count towards their own total, not the reverse-direction rows that
    # belong to their friends' own counts.
    db_session.add_all(
        [
            Friendship(user_id=user.id, friend_id=friend_one.id),
            Friendship(user_id=friend_one.id, friend_id=user.id),
            Friendship(user_id=user.id, friend_id=friend_two.id),
            Friendship(user_id=friend_two.id, friend_id=user.id),
        ]
    )
    await db_session.commit()

    result = await get_player_stats(user.id, db=db_session, current_user=user)

    assert result.friends_count == 2


@pytest.mark.anyio
async def test_public_quiz_count_defaults_to_zero(db_session):
    user = await _create_user(db_session, "a@example.com")
    await db_session.commit()

    result = await get_player_stats(user.id, db=db_session, current_user=user)

    assert result.public_quiz_count == 0


@pytest.mark.anyio
async def test_public_quiz_count_only_counts_this_users_active_public_quizzes(db_session):
    user = await _create_user(db_session, "a@example.com")
    other = await _create_user(db_session, "b@example.com")

    def _quiz(owner_id, visibility, is_active=True):
        return Category(
            name="Q",
            icon_name="sparkle",
            color_key="coral",
            is_active=is_active,
            owner_user_id=owner_id,
            source="manual",
            visibility=visibility,
        )

    db_session.add_all(
        [
            _quiz(user.id, "public"),
            _quiz(user.id, "public"),
            _quiz(user.id, "private"),  # not public - excluded
            _quiz(user.id, "public", is_active=False),  # deleted - excluded
            _quiz(other.id, "public"),  # someone else's - excluded
        ]
    )
    await db_session.commit()

    result = await get_player_stats(user.id, db=db_session, current_user=user)

    assert result.public_quiz_count == 2
