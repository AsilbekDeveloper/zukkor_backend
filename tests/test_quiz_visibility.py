import pytest
from fastapi import HTTPException

from app.core.security import hash_password
from app.models.friendship import Friendship
from app.models.quiz import Category, Question
from app.models.user import User
from app.routers.quiz import start_quiz
from app.schemas.quiz import QuizStartRequest
from app.services.quiz_access import can_access_category


async def _create_user(db, email: str) -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"))
    db.add(user)
    await db.flush()
    return user


async def _create_owned_category(db, owner: User, visibility: str) -> Category:
    category = Category(
        name="Quiz",
        icon_name="sparkle",
        color_key="coral",
        is_active=True,
        owner_user_id=owner.id,
        source="manual",
        visibility=visibility,
    )
    db.add(category)
    await db.flush()
    db.add(
        Question(
            category_id=category.id,
            question_text="Savol?",
            options=["a", "b", "c", "d"],
            correct_option_index=0,
            is_active=True,
        )
    )
    await db.commit()
    await db.refresh(category)
    return category


async def _make_friends(db, user_a: User, user_b: User) -> None:
    db.add(Friendship(user_id=user_a.id, friend_id=user_b.id))
    db.add(Friendship(user_id=user_b.id, friend_id=user_a.id))
    await db.commit()


# --- can_access_category ---


@pytest.mark.anyio
async def test_owner_can_always_access_own_category_regardless_of_visibility(db_session):
    owner = await _create_user(db_session, "owner1@example.com")
    category = await _create_owned_category(db_session, owner, visibility="private")
    assert await can_access_category(db_session, owner.id, category) is True


@pytest.mark.anyio
async def test_stranger_cannot_access_private_category(db_session):
    owner = await _create_user(db_session, "owner2@example.com")
    stranger = await _create_user(db_session, "stranger2@example.com")
    category = await _create_owned_category(db_session, owner, visibility="private")
    assert await can_access_category(db_session, stranger.id, category) is False


@pytest.mark.anyio
async def test_anyone_can_access_public_category(db_session):
    owner = await _create_user(db_session, "owner3@example.com")
    stranger = await _create_user(db_session, "stranger3@example.com")
    category = await _create_owned_category(db_session, owner, visibility="public")
    assert await can_access_category(db_session, stranger.id, category) is True


@pytest.mark.anyio
async def test_friend_can_access_friends_category_but_stranger_cannot(db_session):
    owner = await _create_user(db_session, "owner4@example.com")
    friend = await _create_user(db_session, "friend4@example.com")
    stranger = await _create_user(db_session, "stranger4@example.com")
    await _make_friends(db_session, owner, friend)

    category = await _create_owned_category(db_session, owner, visibility="friends")
    assert await can_access_category(db_session, friend.id, category) is True
    assert await can_access_category(db_session, stranger.id, category) is False


@pytest.mark.anyio
async def test_global_category_accessible_to_everyone(db_session):
    user = await _create_user(db_session, "user5@example.com")
    category = Category(name="Math", icon_name="calculator", color_key="coral", is_active=True)
    db_session.add(category)
    await db_session.commit()
    await db_session.refresh(category)
    assert await can_access_category(db_session, user.id, category) is True


# --- /quiz/start respects visibility ---


@pytest.mark.anyio
async def test_start_quiz_blocks_stranger_on_private_category(db_session):
    owner = await _create_user(db_session, "owner6@example.com")
    stranger = await _create_user(db_session, "stranger6@example.com")
    category = await _create_owned_category(db_session, owner, visibility="private")

    with pytest.raises(HTTPException) as exc_info:
        await start_quiz(
            QuizStartRequest(category_id=category.id, question_count=1), current_user=stranger, db=db_session
        )
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_start_quiz_allows_friend_on_friends_category(db_session):
    owner = await _create_user(db_session, "owner7@example.com")
    friend = await _create_user(db_session, "friend7@example.com")
    await _make_friends(db_session, owner, friend)

    category = await _create_owned_category(db_session, owner, visibility="friends")
    result = await start_quiz(
        QuizStartRequest(category_id=category.id, question_count=1), current_user=friend, db=db_session
    )
    assert result.question is not None


@pytest.mark.anyio
async def test_start_quiz_blocks_non_friend_on_friends_category(db_session):
    owner = await _create_user(db_session, "owner8@example.com")
    stranger = await _create_user(db_session, "stranger8@example.com")
    category = await _create_owned_category(db_session, owner, visibility="friends")

    with pytest.raises(HTTPException) as exc_info:
        await start_quiz(
            QuizStartRequest(category_id=category.id, question_count=1), current_user=stranger, db=db_session
        )
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_start_quiz_allows_anyone_on_public_category(db_session):
    owner = await _create_user(db_session, "owner9@example.com")
    stranger = await _create_user(db_session, "stranger9@example.com")
    category = await _create_owned_category(db_session, owner, visibility="public")

    result = await start_quiz(
        QuizStartRequest(category_id=category.id, question_count=1), current_user=stranger, db=db_session
    )
    assert result.question is not None


@pytest.mark.anyio
async def test_start_quiz_response_points_at_the_real_correct_option(db_session):
    # correct_option_index is sent upfront now (deliberately, so the app
    # can reveal right/wrong instantly on tap) - it must track the
    # SHUFFLED display order, not the raw DB index, or the client would
    # highlight the wrong option as correct.
    owner = await _create_user(db_session, "owner10@example.com")
    category = await _create_owned_category(db_session, owner, visibility="public")

    result = await start_quiz(
        QuizStartRequest(category_id=category.id, question_count=1), current_user=owner, db=db_session
    )
    question = result.question
    assert question.options[question.correct_option_index] == "a"
