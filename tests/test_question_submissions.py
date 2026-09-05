import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import hash_password
from app.models.question_submission import QuestionSubmission
from app.models.quiz import Category, Question
from app.models.user import User
from app.routers.question_submissions import submit_question
from app.schemas.ai_quiz import QuestionSubmissionRequest
from app.services.question_moderation import ModerationResult, QuestionModerationError
from conftest import make_request

_VALID_OPTIONS = ["3", "4", "5", "6"]


async def _create_user(db, email="submitter@example.com") -> User:
    user = User(email=email, hashed_password=hash_password("Parol1234"))
    db.add(user)
    await db.flush()
    return user


async def _create_global_category(db, name="Matematika") -> Category:
    category = Category(name=name, icon_name="sparkle", color_key="coral", is_active=True, visibility="public")
    db.add(category)
    await db.flush()
    return category


def _approve(category_id: int, monkeypatch):
    async def _fake_moderate(**kwargs):
        return ModerationResult(is_approved=True, rejection_reason=None, category_id=category_id)

    monkeypatch.setattr("app.routers.question_submissions.moderate_question", _fake_moderate)


def _reject(reason: str, monkeypatch):
    async def _fake_moderate(**kwargs):
        return ModerationResult(is_approved=False, rejection_reason=reason, category_id=None)

    monkeypatch.setattr("app.routers.question_submissions.moderate_question", _fake_moderate)


def _blow_up(monkeypatch):
    async def _fake_moderate(**kwargs):
        raise QuestionModerationError("AI xizmatiga ulanib bo'lmadi")

    monkeypatch.setattr("app.routers.question_submissions.moderate_question", _fake_moderate)


def _never_called(monkeypatch):
    # Dublikat/shakl xatosi kabi hollarda AI umuman chaqirilmasligini
    # isbotlash uchun - chaqirilsa test darhol yiqiladi.
    async def _fake_moderate(**kwargs):
        raise AssertionError("moderate_question chaqirilmasligi kerak edi")

    monkeypatch.setattr("app.routers.question_submissions.moderate_question", _fake_moderate)


@pytest.mark.anyio
async def test_rejects_malformed_shape_without_calling_ai(db_session, monkeypatch):
    _never_called(monkeypatch)
    user = await _create_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await submit_question(
            make_request(),
            QuestionSubmissionRequest(question_text="2x2?", options=["3", "4", "5"], correct_option_index=1),
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_rejects_out_of_range_correct_index_without_calling_ai(db_session, monkeypatch):
    _never_called(monkeypatch)
    user = await _create_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await submit_question(
            make_request(),
            QuestionSubmissionRequest(question_text="2x2?", options=_VALID_OPTIONS, correct_option_index=9),
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_rejects_nonexistent_category_id(db_session, monkeypatch):
    _never_called(monkeypatch)
    user = await _create_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await submit_question(
            make_request(),
            QuestionSubmissionRequest(question_text="2x2?", options=_VALID_OPTIONS, correct_option_index=1, category_id=999),
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_rejects_a_private_users_category_id(db_session, monkeypatch):
    # owner_user_id borligi - bu global emas, shaxsiy AI/qo'lda quiz -
    # foydalanuvchi u yerga savol qo'sha olmasligi kerak.
    _never_called(monkeypatch)
    owner = await _create_user(db_session, "owner@example.com")
    private_category = Category(
        name="Shaxsiy quiz", icon_name="star", color_key="coral", is_active=True, owner_user_id=owner.id
    )
    db_session.add(private_category)
    await db_session.flush()
    submitter = await _create_user(db_session, "submitter2@example.com")

    with pytest.raises(HTTPException) as exc_info:
        await submit_question(
            make_request(),
            QuestionSubmissionRequest(
                question_text="2x2?", options=_VALID_OPTIONS, correct_option_index=1, category_id=private_category.id
            ),
            current_user=submitter,
            db=db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_rejects_exact_duplicate_question_without_calling_ai(db_session, monkeypatch):
    _never_called(monkeypatch)
    category = await _create_global_category(db_session)
    db_session.add(
        Question(category_id=category.id, question_text="2x2 nechaga teng?", options=_VALID_OPTIONS, correct_option_index=1)
    )
    await db_session.commit()
    user = await _create_user(db_session)

    # Katta-kichik harf va bo'shliq farq qilsa ham dublikat sifatida topilishi kerak.
    result = await submit_question(
        make_request(),
        QuestionSubmissionRequest(question_text="  2X2 NECHAGA TENG?  ", options=_VALID_OPTIONS, correct_option_index=1),
        current_user=user,
        db=db_session,
    )
    assert result.approved is False
    assert result.rejection_reason is not None

    submissions = (await db_session.execute(select(QuestionSubmission))).scalars().all()
    assert len(submissions) == 1
    assert submissions[0].status == "rejected"


@pytest.mark.anyio
async def test_approves_and_creates_active_question_in_requested_category(db_session, monkeypatch):
    category = await _create_global_category(db_session)
    await db_session.commit()
    _approve(category.id, monkeypatch)
    user = await _create_user(db_session)

    result = await submit_question(
        make_request(),
        QuestionSubmissionRequest(
            question_text="Yer sayyorasi Quyoshdan nechinchi?",
            options=_VALID_OPTIONS,
            correct_option_index=2,
            category_id=category.id,
        ),
        current_user=user,
        db=db_session,
    )

    assert result.approved is True
    assert result.category_id == category.id
    assert result.category_name == category.name
    assert result.question_id is not None

    created_question = await db_session.get(Question, result.question_id)
    assert created_question is not None
    assert created_question.is_active is True
    assert created_question.category_id == category.id

    submission = (await db_session.execute(select(QuestionSubmission))).scalar_one()
    assert submission.status == "approved"
    assert submission.resulting_question_id == result.question_id
    assert submission.resulting_category_id == category.id


@pytest.mark.anyio
async def test_ai_can_reassign_to_a_different_category_than_requested(db_session, monkeypatch):
    requested_category = await _create_global_category(db_session, "Tarix")
    better_category = await _create_global_category(db_session, "Geografiya")
    await db_session.commit()
    _approve(better_category.id, monkeypatch)
    user = await _create_user(db_session)

    result = await submit_question(
        make_request(),
        QuestionSubmissionRequest(
            question_text="Amazonka daryosi qaysi qit'ada joylashgan?",
            options=_VALID_OPTIONS,
            correct_option_index=0,
            category_id=requested_category.id,
        ),
        current_user=user,
        db=db_session,
    )

    assert result.approved is True
    assert result.category_id == better_category.id

    submission = (await db_session.execute(select(QuestionSubmission))).scalar_one()
    assert submission.requested_category_id == requested_category.id
    assert submission.resulting_category_id == better_category.id


@pytest.mark.anyio
async def test_ai_rejection_creates_no_question(db_session, monkeypatch):
    category = await _create_global_category(db_session)
    await db_session.commit()
    _reject("Belgilangan javob noto'g'ri", monkeypatch)
    user = await _create_user(db_session)

    result = await submit_question(
        make_request(),
        QuestionSubmissionRequest(
            question_text="2 + 2 nechaga teng?", options=_VALID_OPTIONS, correct_option_index=3
        ),
        current_user=user,
        db=db_session,
    )

    assert result.approved is False
    assert result.rejection_reason == "Belgilangan javob noto'g'ri"

    questions = (await db_session.execute(select(Question))).scalars().all()
    assert questions == []

    submission = (await db_session.execute(select(QuestionSubmission))).scalar_one()
    assert submission.status == "rejected"
    assert submission.ai_feedback == "Belgilangan javob noto'g'ri"


@pytest.mark.anyio
async def test_ai_service_failure_returns_502_and_logs_a_pending_submission(db_session, monkeypatch):
    category = await _create_global_category(db_session)
    await db_session.commit()
    _blow_up(monkeypatch)
    user = await _create_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await submit_question(
            make_request(),
            QuestionSubmissionRequest(question_text="Savol matni", options=_VALID_OPTIONS, correct_option_index=1),
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 502

    submission = (await db_session.execute(select(QuestionSubmission))).scalar_one()
    assert submission.status == "pending"
    assert submission.ai_feedback == "AI xizmatiga ulanib bo'lmadi"


@pytest.mark.anyio
async def test_rejects_when_no_active_global_categories_exist(db_session, monkeypatch):
    _never_called(monkeypatch)
    user = await _create_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await submit_question(
            make_request(),
            QuestionSubmissionRequest(question_text="Savol matni", options=_VALID_OPTIONS, correct_option_index=1),
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 503
