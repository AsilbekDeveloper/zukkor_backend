import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import hash_password
from app.models.quiz import Category, Question
from app.models.reported_question import ReportedQuestion
from app.models.user import User
from app.routers.reports import report_question
from app.schemas.reports import ReportQuestionRequest


async def _create_user(db, email: str) -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"))
    db.add(user)
    await db.flush()
    return user


async def _create_question(db, *, owner: User | None = None) -> Question:
    category = Category(
        name="Quiz",
        icon_name="sparkle",
        color_key="coral",
        is_active=True,
        owner_user_id=owner.id if owner else None,
        visibility="public",
    )
    db.add(category)
    await db.flush()
    question = Question(
        category_id=category.id,
        question_text="Savol?",
        options=["a", "b", "c", "d"],
        correct_option_index=0,
        is_active=True,
    )
    db.add(question)
    await db.commit()
    await db.refresh(question)
    return question


@pytest.mark.anyio
async def test_report_question_creates_a_pending_report(db_session):
    user = await _create_user(db_session, "reporter1@example.com")
    question = await _create_question(db_session)

    result = await report_question(
        question.id,
        ReportQuestionRequest(reason="wrong_answer", comment="Bu savolning javobi noto'g'ri"),
        current_user=user,
        db=db_session,
    )
    assert result.reported is True

    rows = (await db_session.execute(select(ReportedQuestion))).scalars().all()
    assert len(rows) == 1
    assert rows[0].question_id == question.id
    assert rows[0].reporter_user_id == user.id
    assert rows[0].reason == "wrong_answer"
    assert rows[0].status == "pending"


@pytest.mark.anyio
async def test_reporting_same_question_twice_updates_instead_of_duplicating(db_session):
    user = await _create_user(db_session, "reporter2@example.com")
    question = await _create_question(db_session)

    await report_question(
        question.id, ReportQuestionRequest(reason="unclear"), current_user=user, db=db_session
    )
    await report_question(
        question.id, ReportQuestionRequest(reason="offensive", comment="Tuzatildi"), current_user=user, db=db_session
    )

    rows = (await db_session.execute(select(ReportedQuestion))).scalars().all()
    assert len(rows) == 1
    assert rows[0].reason == "offensive"
    assert rows[0].comment == "Tuzatildi"


@pytest.mark.anyio
async def test_report_ugc_question_works_the_same_as_official(db_session):
    owner = await _create_user(db_session, "ugcowner@example.com")
    reporter = await _create_user(db_session, "reporter3@example.com")
    question = await _create_question(db_session, owner=owner)

    result = await report_question(
        question.id, ReportQuestionRequest(reason="other", comment="Shubhali"), current_user=reporter, db=db_session
    )
    assert result.reported is True


@pytest.mark.anyio
async def test_report_nonexistent_question_returns_404(db_session):
    user = await _create_user(db_session, "reporter4@example.com")

    with pytest.raises(HTTPException) as exc_info:
        await report_question(
            999999, ReportQuestionRequest(reason="other"), current_user=user, db=db_session
        )
    assert exc_info.value.status_code == 404
