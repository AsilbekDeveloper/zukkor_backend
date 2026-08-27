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


async def _create_category_with_questions(db, count: int) -> tuple[Category, list[int]]:
    category = Category(name="Quiz", icon_name="sparkle", color_key="coral", is_active=True, visibility="public")
    db.add(category)
    await db.flush()
    question_ids: list[int] = []
    for i in range(count):
        question = Question(
            category_id=category.id,
            question_text=f"Savol {i}?",
            options=["a", "b", "c", "d"],
            correct_option_index=0,
            is_active=True,
        )
        db.add(question)
        await db.flush()
        question_ids.append(question.id)
    await db.commit()
    await db.refresh(category)
    return category, question_ids


async def _play_session(db, user: User, category: Category, question_count: int) -> list[int]:
    """Plays a full Solo session, answering every question (any option -
    correctness doesn't matter here), and returns the served question_id's
    in serve order by cross-referencing question_text against the category's
    known questions (the API itself never returns raw question_id)."""
    start = await start_quiz(
        QuizStartRequest(category_id=category.id, question_count=question_count), current_user=user, db=db
    )
    served_texts = [start.question.question_text]
    session_question_id = start.question.session_question_id
    session_id = start.session_id

    while True:
        answer = await answer_question(
            session_id,
            AnswerRequest(session_question_id=session_question_id, selected_option=0),
            current_user=user,
            db=db,
        )
        if answer.session_complete:
            break
        served_texts.append(answer.next_question.question_text)
        session_question_id = answer.next_question.session_question_id

    return served_texts


@pytest.mark.anyio
async def test_first_session_only_serves_never_seen_questions(db_session):
    user = await _create_user(db_session, "fresh1@example.com")
    category, _ids = await _create_category_with_questions(db_session, count=6)

    served = await _play_session(db_session, user, category, question_count=3)
    assert len(served) == 3
    assert len(set(served)) == 3  # no repeats within the session


@pytest.mark.anyio
async def test_unseen_questions_are_served_before_repeats_across_sessions(db_session):
    user = await _create_user(db_session, "fresh2@example.com")
    category, _ids = await _create_category_with_questions(db_session, count=4)

    first_served = await _play_session(db_session, user, category, question_count=2)
    assert len(first_served) == 2

    # Second session plays ALL 4 questions in the category. The first 2
    # served must be the 2 the user has never seen (not in first_served) -
    # only once those run out should the previously-seen ones reappear.
    second_served = await _play_session(db_session, user, category, question_count=4)
    assert len(second_served) == 4
    assert len(set(second_served)) == 4  # covers every question in the category, no repeats within the session

    first_two_of_second = second_served[:2]
    last_two_of_second = second_served[2:]

    assert set(first_two_of_second).isdisjoint(first_served), (
        "the first questions served in the second session should be the ones never seen before"
    )
    assert set(last_two_of_second) == set(first_served), (
        "once unseen questions run out, the fallback should be exactly the previously-seen ones"
    )


@pytest.mark.anyio
async def test_a_second_user_has_their_own_independent_freshness(db_session):
    # user_a's history must not affect what counts as "unseen" for user_b.
    user_a = await _create_user(db_session, "fresh3a@example.com")
    user_b = await _create_user(db_session, "fresh3b@example.com")
    category, _ids = await _create_category_with_questions(db_session, count=3)

    await _play_session(db_session, user_a, category, question_count=3)
    # user_b should still get a full, valid session of never-seen questions.
    served_b = await _play_session(db_session, user_b, category, question_count=3)
    assert len(served_b) == 3
    assert len(set(served_b)) == 3
