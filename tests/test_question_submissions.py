from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import hash_password
from app.models.question_submission import QuestionSubmission
from app.models.quiz import Category, Question
from app.models.user import User
from app.routers.question_submissions import MAX_APPROVED_SUBMISSIONS_PER_DAY, appeal_submission, submit_question
from app.schemas.ai_quiz import QuestionSubmissionRequest
from app.services.question_moderation import ModerationResult, QuestionModerationError
from conftest import make_request

_VALID_OPTIONS = ["3", "4", "5", "6"]


async def _create_user(db, email="submitter@example.com") -> User:
    user = User(email=email, hashed_password=await hash_password("Parol1234"))
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
    # 2026-09-12: Coin sarflash/muallif ulushi shu maydonga tayanadi -
    # tasdiqlangan savol doim o'zini yuborgan userga bog'langan bo'lishi kerak.
    assert created_question.created_by_user_id == user.id

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


# --- Kunlik tasdiqlangan-savol chegarasi (2026-09-13 prod-tayyorlik auditi) ---


async def _add_approved_submission(db, user_id: str, created_at: datetime) -> None:
    db.add(
        QuestionSubmission(
            submitter_user_id=user_id,
            question_text=f"Savol {created_at.isoformat()}",
            options=_VALID_OPTIONS,
            correct_option_index=0,
            status="approved",
            created_at=created_at,
        )
    )


@pytest.mark.anyio
async def test_rejects_submission_once_daily_approved_cap_is_reached(db_session, monkeypatch):
    _never_called(monkeypatch)
    category = await _create_global_category(db_session)
    user = await _create_user(db_session)
    now = datetime.now(timezone.utc)
    for _ in range(MAX_APPROVED_SUBMISSIONS_PER_DAY):
        await _add_approved_submission(db_session, user.id, now)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await submit_question(
            make_request(),
            QuestionSubmissionRequest(
                question_text="Yana bitta savol", options=_VALID_OPTIONS, correct_option_index=0, category_id=category.id
            ),
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 429


@pytest.mark.anyio
async def test_daily_cap_ignores_other_users_and_rejected_submissions(db_session, monkeypatch):
    category = await _create_global_category(db_session)
    _approve(category.id, monkeypatch)
    user = await _create_user(db_session, "capped@example.com")
    other_user = await _create_user(db_session, "other@example.com")
    now = datetime.now(timezone.utc)

    # Boshqa userning tasdiqlangan savollari va shu userning RAD ETILGAN
    # savollari - hech biri limitga qo'shilmasligi kerak.
    for _ in range(MAX_APPROVED_SUBMISSIONS_PER_DAY):
        await _add_approved_submission(db_session, other_user.id, now)
    db_session.add(
        QuestionSubmission(
            submitter_user_id=user.id,
            question_text="Rad etilgan savol",
            options=_VALID_OPTIONS,
            correct_option_index=0,
            status="rejected",
            created_at=now,
        )
    )
    await db_session.commit()

    result = await submit_question(
        make_request(),
        QuestionSubmissionRequest(
            question_text="Yangi savol", options=_VALID_OPTIONS, correct_option_index=0, category_id=category.id
        ),
        current_user=user,
        db=db_session,
    )
    assert result.approved is True


@pytest.mark.anyio
async def test_daily_cap_resets_after_the_tashkent_day_boundary(db_session, monkeypatch):
    category = await _create_global_category(db_session)
    _approve(category.id, monkeypatch)
    user = await _create_user(db_session)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    for _ in range(MAX_APPROVED_SUBMISSIONS_PER_DAY):
        await _add_approved_submission(db_session, user.id, yesterday)
    await db_session.commit()

    result = await submit_question(
        make_request(),
        QuestionSubmissionRequest(
            question_text="Bugungi savol", options=_VALID_OPTIONS, correct_option_index=0, category_id=category.id
        ),
        current_user=user,
        db=db_session,
    )
    assert result.approved is True


# --- E'tiroz bildirish - "AI galyutsinatsiyasi" himoyasi #2 (2026-09-19,
# False Negative: AI noto'g'ri rad etgan yaxshi savol) ---


@pytest.mark.anyio
async def test_appeal_moves_a_rejected_submission_to_pending_manual_review(db_session, monkeypatch):
    category = await _create_global_category(db_session)
    await db_session.commit()
    _reject("Belgilangan javob noto'g'ri", monkeypatch)
    user = await _create_user(db_session)
    submitted = await submit_question(
        make_request(),
        QuestionSubmissionRequest(
            question_text="Yaxshi savol", options=_VALID_OPTIONS, correct_option_index=1, category_id=category.id
        ),
        current_user=user,
        db=db_session,
    )
    assert submitted.approved is False

    result = await appeal_submission(submitted.submission_id, current_user=user, db=db_session)

    assert result.submission_id == submitted.submission_id
    assert result.status == "pending_manual_review"
    submission = await db_session.get(QuestionSubmission, submitted.submission_id)
    assert submission.status == "pending_manual_review"
    assert submission.appealed_at is not None


@pytest.mark.anyio
async def test_appeal_is_owner_only(db_session, monkeypatch):
    category = await _create_global_category(db_session)
    await db_session.commit()
    _reject("Sabab", monkeypatch)
    owner = await _create_user(db_session, "owner3@example.com")
    stranger = await _create_user(db_session, "stranger3@example.com")
    submitted = await submit_question(
        make_request(),
        QuestionSubmissionRequest(
            question_text="Savol", options=_VALID_OPTIONS, correct_option_index=1, category_id=category.id
        ),
        current_user=owner,
        db=db_session,
    )

    with pytest.raises(HTTPException) as exc_info:
        await appeal_submission(submitted.submission_id, current_user=stranger, db=db_session)
    assert exc_info.value.status_code == 404

    submission = await db_session.get(QuestionSubmission, submitted.submission_id)
    assert submission.status == "rejected"  # unchanged


@pytest.mark.anyio
async def test_appeal_of_unknown_submission_returns_404(db_session):
    user = await _create_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await appeal_submission(999999, current_user=user, db=db_session)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_appeal_rejects_an_already_approved_submission(db_session, monkeypatch):
    category = await _create_global_category(db_session)
    await db_session.commit()
    _approve(category.id, monkeypatch)
    user = await _create_user(db_session)
    submitted = await submit_question(
        make_request(),
        QuestionSubmissionRequest(
            question_text="Savol", options=_VALID_OPTIONS, correct_option_index=1, category_id=category.id
        ),
        current_user=user,
        db=db_session,
    )
    assert submitted.approved is True

    with pytest.raises(HTTPException) as exc_info:
        await appeal_submission(submitted.submission_id, current_user=user, db=db_session)
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_appeal_cannot_be_repeated_once_already_pending_manual_review(db_session, monkeypatch):
    category = await _create_global_category(db_session)
    await db_session.commit()
    _reject("Sabab", monkeypatch)
    user = await _create_user(db_session)
    submitted = await submit_question(
        make_request(),
        QuestionSubmissionRequest(
            question_text="Savol", options=_VALID_OPTIONS, correct_option_index=1, category_id=category.id
        ),
        current_user=user,
        db=db_session,
    )
    await appeal_submission(submitted.submission_id, current_user=user, db=db_session)

    with pytest.raises(HTTPException) as exc_info:
        await appeal_submission(submitted.submission_id, current_user=user, db=db_session)
    assert exc_info.value.status_code == 400
