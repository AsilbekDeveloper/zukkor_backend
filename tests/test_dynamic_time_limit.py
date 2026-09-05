import pytest

from app.core.security import hash_password
from app.models.quiz import Category, Question
from app.models.user import User
from app.routers.quiz import answer_question, start_quiz
from app.schemas.quiz import AnswerRequest, QuizStartRequest
from app.services.scoring import compute_time_limit_ms

_SHORT_TEXT = "2x2?"
_SHORT_OPTIONS = ["3", "4", "5", "6"]
_LONG_TEXT = (
    "Quyidagi voqealardan qaysi biri O'zbekiston tarixida eng katta ahamiyatga "
    "ega bo'lib, mamlakat mustaqilligining mustahkamlanishiga xizmat qilgan?"
)
_LONG_OPTIONS = [
    "Birinchi Prezident saylovi",
    "Konstitutsiyaning qabul qilinishi",
    "Milliy valyutaning joriy etilishi",
    "Yuqoridagilarning barchasi",
]


async def _create_user(db) -> User:
    user = User(email="timelimit@example.com", hashed_password=hash_password("Parol1234"))
    db.add(user)
    await db.flush()
    return user


async def _create_category_with_two_very_different_questions(db) -> Category:
    category = Category(
        name="Aralash uzunlik", icon_name="sparkle", color_key="coral", is_active=True, visibility="public"
    )
    db.add(category)
    await db.flush()
    db.add(
        Question(
            category_id=category.id, question_text=_SHORT_TEXT, options=_SHORT_OPTIONS,
            correct_option_index=1, is_active=True,
        )
    )
    db.add(
        Question(
            category_id=category.id, question_text=_LONG_TEXT, options=_LONG_OPTIONS,
            correct_option_index=3, is_active=True,
        )
    )
    await db.commit()
    await db.refresh(category)
    return category


@pytest.mark.anyio
async def test_first_question_time_limit_matches_its_own_content(db_session):
    user = await _create_user(db_session)
    category = await _create_category_with_two_very_different_questions(db_session)

    start = await start_quiz(QuizStartRequest(category_id=category.id, question_count=2), current_user=user, db=db_session)

    expected = compute_time_limit_ms(start.question.question_text, start.question.options)
    assert start.question.time_limit_ms == expected


@pytest.mark.anyio
async def test_second_question_gets_its_OWN_time_limit_not_the_first_questions(db_session):
    # Regression test: the next-question code used to copy
    # session_question.time_limit_ms (the PREVIOUS question's value)
    # instead of computing one for the next question's own content.
    user = await _create_user(db_session)
    category = await _create_category_with_two_very_different_questions(db_session)

    start = await start_quiz(QuizStartRequest(category_id=category.id, question_count=2), current_user=user, db=db_session)
    answer = await answer_question(
        start.session_id,
        AnswerRequest(session_question_id=start.question.session_question_id, selected_option=None),
        current_user=user,
        db=db_session,
    )

    next_question = answer.next_question
    assert next_question is not None
    expected = compute_time_limit_ms(next_question.question_text, next_question.options)
    assert next_question.time_limit_ms == expected

    # The two questions are deliberately very different lengths, so if the
    # bug were still present (copying the first question's limit), this
    # would almost certainly fail - a real, not just theoretical, check.
    assert next_question.time_limit_ms != start.question.time_limit_ms
