import pytest

from app.core.security import hash_password
from app.models.quiz import Category, Question
from app.models.user import User
from app.routers.quiz import answer_question, start_quiz
from app.schemas.quiz import AnswerRequest, QuizStartRequest


async def _create_user(db, email: str) -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"))
    db.add(user)
    await db.flush()
    return user


async def _create_category(db, *, official: bool, owner: User | None = None) -> Category:
    category = Category(
        name="Quiz",
        icon_name="sparkle",
        color_key="coral",
        is_active=True,
        owner_user_id=None if official else owner.id,
        visibility="public",
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


async def _play_one_question(db, user: User, category: Category):
    start = await start_quiz(QuizStartRequest(category_id=category.id, question_count=1), current_user=user, db=db)
    return await answer_question(
        start.session_id,
        AnswerRequest(
            session_question_id=start.question.session_question_id,
            selected_option=start.question.correct_option_index,
        ),
        current_user=user,
        db=db,
    )


@pytest.mark.anyio
async def test_first_correct_answer_on_official_question_awards_xp(db_session):
    user = await _create_user(db_session, "xp1@example.com")
    category = await _create_category(db_session, official=True)

    answer = await _play_one_question(db_session, user, category)
    assert answer.summary.xp_earned > 0
    assert answer.ball_earned > 0


@pytest.mark.anyio
async def test_repeat_encounter_of_same_official_question_never_awards_xp_again(db_session):
    # Same category has only one question, so replaying it (a fresh Solo
    # session each time) necessarily re-serves the exact same question.
    user = await _create_user(db_session, "xp2@example.com")
    category = await _create_category(db_session, official=True)

    first = await _play_one_question(db_session, user, category)
    assert first.summary.xp_earned > 0

    second = await _play_one_question(db_session, user, category)
    assert second.summary.xp_earned == 0
    # The in-game ball (used for breakdown/history) stays unaffected by the
    # XP gate - only the XP/leaderboard layer is capped, per spec.
    assert second.ball_earned > 0
    assert second.summary.total_ball > 0


@pytest.mark.anyio
async def test_ugc_category_never_awards_xp_even_on_first_answer(db_session):
    owner = await _create_user(db_session, "xp3owner@example.com")
    category = await _create_category(db_session, official=False, owner=owner)

    answer = await _play_one_question(db_session, owner, category)
    assert answer.summary.xp_earned == 0
    assert answer.ball_earned > 0
