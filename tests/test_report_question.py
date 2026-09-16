import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import hash_password
from app.models.quiz import Category, Question
from app.models.reported_question import ReportedQuestion
from app.models.user import User
from app.routers.reports import AUTO_DEACTIVATE_REPORT_THRESHOLD, report_question
from app.schemas.reports import ReportQuestionRequest


async def _create_user(db, email: str) -> User:
    user = User(email=email, hashed_password=await hash_password("Parol1234"))
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


# --- Avto-o'chirish - "AI galyutsinatsiyasi" himoyasi #3 (2026-09-19,
# False Positive: AI noto'g'ri tasdiqlagan yomon savol, o'yinchilar
# tomonidan payqalgan) ---


@pytest.mark.anyio
async def test_question_auto_deactivates_once_the_threshold_is_reached(db_session):
    question = await _create_question(db_session)
    reporters = [await _create_user(db_session, f"auto{i}@example.com") for i in range(AUTO_DEACTIVATE_REPORT_THRESHOLD)]
    await db_session.commit()

    for reporter in reporters:
        await report_question(
            question.id, ReportQuestionRequest(reason="wrong_answer"), current_user=reporter, db=db_session
        )

    await db_session.refresh(question)
    assert question.is_active is False


@pytest.mark.anyio
async def test_question_stays_active_below_the_threshold(db_session):
    question = await _create_question(db_session)
    reporters = [
        await _create_user(db_session, f"below{i}@example.com") for i in range(AUTO_DEACTIVATE_REPORT_THRESHOLD - 1)
    ]
    await db_session.commit()

    for reporter in reporters:
        await report_question(
            question.id, ReportQuestionRequest(reason="wrong_answer"), current_user=reporter, db=db_session
        )

    await db_session.refresh(question)
    assert question.is_active is True


@pytest.mark.anyio
async def test_repeated_reports_from_the_same_user_never_auto_deactivate_alone(db_session):
    # Bitta foydalanuvchi UniqueConstraint tufayli faqat 1 ta qator hosil
    # qiladi (qayta yuborilsa yangilanadi) - shuning uchun necha marta
    # qayta yuborsa ham, YAKKA o'zi hech qachon chegaraga yetkaza olmaydi.
    question = await _create_question(db_session)
    reporter = await _create_user(db_session, "repeat@example.com")
    await db_session.commit()

    for _ in range(AUTO_DEACTIVATE_REPORT_THRESHOLD + 5):
        await report_question(
            question.id, ReportQuestionRequest(reason="unclear"), current_user=reporter, db=db_session
        )

    await db_session.refresh(question)
    assert question.is_active is True
    rows = (await db_session.execute(select(ReportedQuestion))).scalars().all()
    assert len(rows) == 1


@pytest.mark.anyio
async def test_dismissed_reports_do_not_count_toward_auto_deactivation(db_session):
    question = await _create_question(db_session)
    reporters = [await _create_user(db_session, f"dismissed{i}@example.com") for i in range(AUTO_DEACTIVATE_REPORT_THRESHOLD)]
    await db_session.commit()

    for reporter in reporters:
        await report_question(
            question.id, ReportQuestionRequest(reason="wrong_answer"), current_user=reporter, db=db_session
        )
    await db_session.refresh(question)
    assert question.is_active is False

    # Admin qo'lda qayta faollashtirdi (Kill Switch) va reportlardan
    # bittasini "dismissed" deb belgiladi - shundan keyin count chegaradan
    # pastga tushadi, lekin bu test faqat HISOBLASH mantig'ini tekshiradi
    # (avtomatik qayta faollashtirish YO'Q - shuning uchun bu yerda faqat
    # yangi savol bilan pastroq hisobni tekshiramiz).
    question2 = await _create_question(db_session)
    reporters2 = [
        await _create_user(db_session, f"dismissed2_{i}@example.com") for i in range(AUTO_DEACTIVATE_REPORT_THRESHOLD)
    ]
    await db_session.commit()
    for reporter in reporters2[:-1]:
        await report_question(
            question2.id, ReportQuestionRequest(reason="wrong_answer"), current_user=reporter, db=db_session
        )
    # Oxirgi reportni qo'shib, keyin uni "dismissed" deb belgilaymiz -
    # chegaraga rasman yetgan bo'lsa ham, dismissed hisoblanmasligi kerak.
    await report_question(
        question2.id, ReportQuestionRequest(reason="wrong_answer"), current_user=reporters2[-1], db=db_session
    )
    await db_session.refresh(question2)
    assert question2.is_active is False  # bu safar hammasi pending edi, chegaraga yetdi

    # Endi shu oxirgi reportni dismissed qilib, savolni qo'lda qayta
    # faollashtiramiz va tekshiramiz - dismissed report endi hisobga
    # kirmasligi kerak (report_count kamayadi).
    last_report_result = await db_session.execute(
        select(ReportedQuestion).where(
            ReportedQuestion.question_id == question2.id, ReportedQuestion.reporter_user_id == reporters2[-1].id
        )
    )
    last_report = last_report_result.scalar_one()
    last_report.status = "dismissed"
    question2.is_active = True
    await db_session.commit()

    await db_session.refresh(question2)
    assert question2.report_count == AUTO_DEACTIVATE_REPORT_THRESHOLD - 1


@pytest.mark.anyio
async def test_report_count_reflects_the_number_of_unique_reporters(db_session):
    question = await _create_question(db_session)
    reporter_a = await _create_user(db_session, "count_a@example.com")
    reporter_b = await _create_user(db_session, "count_b@example.com")
    await db_session.commit()

    await report_question(
        question.id, ReportQuestionRequest(reason="unclear"), current_user=reporter_a, db=db_session
    )
    await report_question(
        question.id, ReportQuestionRequest(reason="offensive"), current_user=reporter_b, db=db_session
    )

    await db_session.refresh(question)
    assert question.report_count == 2
