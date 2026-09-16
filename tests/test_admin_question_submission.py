"""`QuestionSubmissionAdmin.on_model_change` (`app/admin.py`) - "AI
galyutsinatsiyasi" himoyasi #2ning admin tarafi (2026-09-19): admin
`'pending_manual_review'` holatidagi taklifni `'approved'`ga o'zgartirsa,
haqiqiy `Question` qatori AVTOMATIK yaratilishi kerak - xuddi AI o'zi
tasdiqlagandek."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.admin as admin_module
from app.admin import QuestionAdmin, QuestionSubmissionAdmin
from app.core.database import Base
from app.core.security import hash_password
from app.models.question_submission import QuestionSubmission
from app.models.quiz import Category, Question
from app.models.user import User

_VALID_OPTIONS = ["3", "4", "5", "6"]


# --- "Kill Switch" (2026-09-19): admin savolni bevosita tahrirlay/o'chira
# olishi shart - AI (moderatsiya yoki generatsiya) noto'g'ri tasdiqlagan
# savolni qo'lda tuzatish/olib tashlash uchun. ---


def test_question_admin_allows_editing_and_deleting():
    assert QuestionAdmin.can_edit is True
    assert QuestionAdmin.can_delete is True


def test_question_admin_shows_the_report_count_column():
    # Oddiy `in` ishlatib bo'lmaydi - ro'yxatdagi ba'zi elementlar
    # (masalan `Question.category`, relationship) `==` orqali
    # `column_property`ga solishtirilganda xato ko'taradi, `False`
    # qaytarish o'rniga.
    assert any(col is Question.report_count for col in QuestionAdmin.column_list)


@pytest.fixture(autouse=True)
async def _isolated_engine(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(admin_module, "AsyncSessionLocal", session_maker)
    yield session_maker
    await engine.dispose()


async def _seed(session_maker, *, status: str = "pending_manual_review"):
    async with session_maker() as db:
        user = User(email="submitter@example.com", hashed_password=await hash_password("Parol1234"))
        category = Category(name="Test", icon_name="star", color_key="coral", is_active=True)
        db.add_all([user, category])
        await db.flush()
        submission = QuestionSubmission(
            submitter_user_id=user.id,
            question_text="2+2 nechaga teng?",
            options=_VALID_OPTIONS,
            correct_option_index=1,
            status=status,
        )
        db.add(submission)
        await db.commit()
        await db.refresh(submission)
        return user, category, submission


def _base_form_data(**overrides) -> dict:
    data = {
        "question_text": "2+2 nechaga teng?",
        "option_1": "3", "option_2": "4", "option_3": "5", "option_4": "6",
        "correct_option": "1",
    }
    data.update(overrides)
    return data


@pytest.mark.anyio
async def test_approving_an_appealed_submission_creates_a_live_question(_isolated_engine):
    user, category, submission = await _seed(_isolated_engine)
    admin_view = QuestionSubmissionAdmin()

    data = _base_form_data(status="approved", resulting_category=category)
    await admin_view.on_model_change(data, submission, False, None)

    assert data["resulting_question_id"] is not None
    assert data["options"] == _VALID_OPTIONS
    assert data["correct_option_index"] == 1

    async with _isolated_engine() as db:
        question = await db.get(Question, data["resulting_question_id"])
        assert question is not None
        assert question.is_active is True
        assert question.category_id == category.id
        assert question.question_text == "2+2 nechaga teng?"
        assert question.created_by_user_id == user.id


@pytest.mark.anyio
async def test_approving_without_a_category_raises(_isolated_engine):
    _user, _category, submission = await _seed(_isolated_engine)
    admin_view = QuestionSubmissionAdmin()

    data = _base_form_data(status="approved", resulting_category=None)

    with pytest.raises(ValueError):
        await admin_view.on_model_change(data, submission, False, None)

    async with _isolated_engine() as db:
        assert (await db.execute(select(Question))).scalars().all() == []


@pytest.mark.anyio
async def test_rejecting_an_appealed_submission_creates_no_question(_isolated_engine):
    _user, category, submission = await _seed(_isolated_engine)
    admin_view = QuestionSubmissionAdmin()

    data = _base_form_data(status="rejected", resulting_category=category)
    await admin_view.on_model_change(data, submission, False, None)

    assert "resulting_question_id" not in data
    async with _isolated_engine() as db:
        assert (await db.execute(select(Question))).scalars().all() == []


@pytest.mark.anyio
async def test_re_saving_an_already_approved_submission_does_not_duplicate_the_question(_isolated_engine):
    user, category, submission = await _seed(_isolated_engine, status="approved")
    async with _isolated_engine() as db:
        existing_question = Question(
            category_id=category.id,
            question_text=submission.question_text,
            options=_VALID_OPTIONS,
            correct_option_index=1,
            is_active=True,
            created_by_user_id=user.id,
        )
        db.add(existing_question)
        await db.flush()
        submission.resulting_question_id = existing_question.id
        submission.resulting_category_id = category.id
        db.add(submission)
        await db.commit()
        await db.refresh(submission)

    admin_view = QuestionSubmissionAdmin()
    # Admin submission matnini biroz tuzatib qayta saqlaydi, status hali
    # ham "approved" - buni ikkinchi marta "yangi tasdiqlash" deb hisoblab,
    # takroriy Question yaratmasligi kerak.
    data = _base_form_data(status="approved", resulting_category=category, question_text="Tuzatilgan matn")
    await admin_view.on_model_change(data, submission, False, None)

    assert "resulting_question_id" not in data
    async with _isolated_engine() as db:
        questions = (await db.execute(select(Question))).scalars().all()
        assert len(questions) == 1
