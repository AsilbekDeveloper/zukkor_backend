"""Quizni PDF (bosma test) sifatida eksport qilish (2026-09-28) -
`app/routers/quiz_export.py`: kirish huquqi, Diamond narxlash (2026-09-29
qaroridan buyon QATIY BELGILANGAN, savollar soniga QARAMAYDI) va balans
yetarli bo'lmasa 402 qaytarishni tekshiradi."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import hash_password
from app.models.currency_transaction import CurrencyTransaction
from app.models.quiz import Category, Question
from app.models.user import User
from app.routers.quiz_export import export_quiz_docx, export_quiz_pdf
from app.services import economy_config


async def _create_user(db, email: str, *, diamond_balance: int = 100) -> User:
    user = User(email=email, hashed_password=await hash_password("Parol1234"), diamond_balance=diamond_balance)
    db.add(user)
    await db.flush()
    return user


async def _create_category(
    db, *, owner: User | None = None, visibility: str = "private", question_count: int = 3, name: str = "Tarix"
) -> Category:
    category = Category(
        name=name, icon_name="history", color_key="coral", is_active=True,
        owner_user_id=owner.id if owner else None, visibility=visibility,
    )
    db.add(category)
    await db.flush()
    for i in range(question_count):
        db.add(
            Question(
                category_id=category.id, question_text=f"Savol {i}?",
                options=["a", "b", "c", "d"], correct_option_index=i % 4, is_active=True,
            )
        )
    await db.commit()
    await db.refresh(category)
    return category


def _flat_export_cost() -> int:
    return economy_config.DEFAULTS[economy_config.EXPORT_DIAMOND_COST]


@pytest.mark.anyio
async def test_exporting_a_global_category_produces_a_pdf_and_charges_diamond(db_session):
    user = await _create_user(db_session, "teacher1@example.com", diamond_balance=100)
    category = await _create_category(db_session, owner=None, question_count=5)

    response = await export_quiz_pdf(category.id, current_user=user, db=db_session)

    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF")

    expected_cost = _flat_export_cost()
    await db_session.refresh(user)
    assert user.diamond_balance == 100 - expected_cost

    tx = (
        await db_session.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))
    ).scalar_one()
    assert tx.currency == "diamond"
    assert tx.amount == -expected_cost
    assert tx.reason == "quiz_export"


@pytest.mark.anyio
async def test_export_fails_with_402_when_diamond_balance_is_too_low(db_session):
    user = await _create_user(db_session, "teacher2@example.com", diamond_balance=1)
    category = await _create_category(db_session, owner=None, question_count=5)

    with pytest.raises(HTTPException) as exc_info:
        await export_quiz_pdf(category.id, current_user=user, db=db_session)
    assert exc_info.value.status_code == 402

    await db_session.refresh(user)
    assert user.diamond_balance == 1  # hech narsa yechilmagan

    rows = (await db_session.execute(select(CurrencyTransaction))).scalars().all()
    assert rows == []


@pytest.mark.anyio
async def test_export_cost_does_not_depend_on_question_count(db_session):
    small_user = await _create_user(db_session, "small@example.com", diamond_balance=100)
    big_user = await _create_user(db_session, "big@example.com", diamond_balance=100)
    small_category = await _create_category(db_session, owner=None, question_count=1, name="Kichik")
    big_category = await _create_category(db_session, owner=None, question_count=30, name="Katta")

    await export_quiz_pdf(small_category.id, current_user=small_user, db=db_session)
    await export_quiz_pdf(big_category.id, current_user=big_user, db=db_session)

    await db_session.refresh(small_user)
    await db_session.refresh(big_user)
    assert small_user.diamond_balance == big_user.diamond_balance == 100 - _flat_export_cost()


@pytest.mark.anyio
async def test_owner_can_export_their_own_private_quiz(db_session):
    owner = await _create_user(db_session, "owner1@example.com", diamond_balance=100)
    category = await _create_category(db_session, owner=owner, visibility="private", question_count=2)

    response = await export_quiz_pdf(category.id, current_user=owner, db=db_session)
    assert response.body.startswith(b"%PDF")


@pytest.mark.anyio
async def test_a_stranger_cannot_export_someone_elses_private_quiz(db_session):
    owner = await _create_user(db_session, "owner2@example.com")
    stranger = await _create_user(db_session, "stranger2@example.com", diamond_balance=100)
    category = await _create_category(db_session, owner=owner, visibility="private", question_count=2)

    with pytest.raises(HTTPException) as exc_info:
        await export_quiz_pdf(category.id, current_user=stranger, db=db_session)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_exporting_a_category_with_no_questions_returns_400(db_session):
    user = await _create_user(db_session, "teacher3@example.com", diamond_balance=100)
    category = await _create_category(db_session, owner=None, question_count=0)

    with pytest.raises(HTTPException) as exc_info:
        await export_quiz_pdf(category.id, current_user=user, db=db_session)
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_exporting_a_nonexistent_category_returns_404(db_session):
    user = await _create_user(db_session, "teacher4@example.com", diamond_balance=100)

    with pytest.raises(HTTPException) as exc_info:
        await export_quiz_pdf(999999, current_user=user, db=db_session)
    assert exc_info.value.status_code == 404


# --- DOCX (2026-09-29) - xuddi shu huquq/narxlash mantig'ini ulashadi
# (`_load_exportable_quiz`/`_charge_export`), shuning uchun faqat faylning
# o'zi (Word ZIP signature) va narxlash to'g'riligini tekshiramiz - PDF
# testlarida allaqachon qamrab olingan huquq/xato holatlarini takrorlamaymiz.


@pytest.mark.anyio
async def test_exporting_a_global_category_produces_a_docx_and_charges_diamond(db_session):
    user = await _create_user(db_session, "teacher5@example.com", diamond_balance=100)
    category = await _create_category(db_session, owner=None, question_count=4)

    response = await export_quiz_docx(category.id, current_user=user, db=db_session)

    assert response.media_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    # .docx - ichida ZIP arxiv (PK signature) - Word'ning haqiqiy fayl
    # ekanini kod darajasida tekshirish uchun eng arzon yo'l.
    assert response.body.startswith(b"PK")

    expected_cost = _flat_export_cost()
    await db_session.refresh(user)
    assert user.diamond_balance == 100 - expected_cost


@pytest.mark.anyio
async def test_docx_export_fails_with_402_when_diamond_balance_is_too_low(db_session):
    user = await _create_user(db_session, "teacher6@example.com", diamond_balance=1)
    category = await _create_category(db_session, owner=None, question_count=4)

    with pytest.raises(HTTPException) as exc_info:
        await export_quiz_docx(category.id, current_user=user, db=db_session)
    assert exc_info.value.status_code == 402

    await db_session.refresh(user)
    assert user.diamond_balance == 1
