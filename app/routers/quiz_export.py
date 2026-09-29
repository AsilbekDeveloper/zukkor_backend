"""Quizni PDF/Word (.docx) sifatida eksport qilish - o'qituvchi sinfda
qog'ozda test o'tkazmoqchi bo'lsa (2026-09-28/29, foydalanuvchi so'rovi).
Diamond bilan to'lanadi - QATIY BELGILANGAN narx (`economy_config.
EXPORT_DIAMOND_COST`, savollar soniga QARAMAYDI - 2026-09-29,
foydalanuvchi qarori bilan savol-boshiga hisobdan qat'iy narxga
o'tkazildi), ikkala format uchun ham bir xil, alohida narx kaliti YO'Q.

Kirish huquqi xuddi o'sha quizni O'YNASH bilan bir xil qoidaga bo'ysunadi
(`quiz_access.can_access_category`) - global kategoriyalar hammaga ochiq,
shaxsiy (AI/qo'lda) quizlar esa egasi/public/friends qoidasiga qarab."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.quiz import Category, Question
from app.models.user import User
from app.services import economy_config, wallet
from app.services.quiz_access import can_access_category
from app.services.quiz_docx_export import build_quiz_docx
from app.services.quiz_pdf_export import build_quiz_pdf

router = APIRouter()


async def _load_exportable_quiz(db: AsyncSession, category_id: int, user_id: str) -> tuple[Category, list[Question]]:
    """Ikkala format (`export_quiz_pdf`/`export_quiz_docx`) uchun umumiy:
    huquq tekshiruvi + faol savollarni ID tartibida olib keladi. Savol
    yo'q bo'lsa 400, kategoriya topilmasa/kira olmasa 404 ko'taradi."""
    category = await db.get(Category, category_id)
    if category is None or not category.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topilmadi")
    if not await can_access_category(db, user_id, category):
        # Mavjudligini yashirish uchun xuddi topilmagandek - boshqa
        # joylarda (masalan `quiz_access`ning o'zi) ishlatilgan konvensiya.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topilmadi")

    questions_result = await db.execute(
        select(Question)
        .where(Question.category_id == category_id, Question.is_active.is_(True))
        .order_by(Question.id)
    )
    questions = list(questions_result.scalars().all())
    if not questions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bu quizda hali savollar yo'q")
    return category, questions


async def _charge_export(db: AsyncSession, current_user: User, category_id: int, question_count: int) -> None:
    total_cost = await economy_config.get_int(db, economy_config.EXPORT_DIAMOND_COST)
    try:
        await wallet.charge_diamond_for_export(
            db, current_user, total_cost, extra={"category_id": category_id, "question_count": question_count},
        )
    except wallet.InsufficientBalanceError:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Diamond balansi yetarli emas: {total_cost} \U0001f48e kerak",
        )
    await db.commit()


@router.get(
    "/{category_id}/export/pdf",
    summary="Quizni PDF (bosma test) sifatida eksport qilish",
    description="Diamond bilan to'lanadi (qat'iy narx, savollar soniga "
    "qaramaydi). Javob - to'g'ridan-to'g'ri PDF fayl (`application/pdf`).",
)
async def export_quiz_pdf(
    category_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    category, questions = await _load_exportable_quiz(db, category_id, current_user.id)

    # Fayl avval (arzon, tarmoq chaqiruvi yo'q) tayyorlanadi, Diamond
    # FAQAT muvaffaqiyatli tayyorlangandan keyin yechiladi - aks holda
    # generatsiya kutilmagan xato bersa, foydalanuvchi hech narsa olmay
    # pul yo'qotardi.
    pdf_bytes = build_quiz_pdf(quiz_name=category.name, questions=questions)
    await _charge_export(db, current_user, category_id, len(questions))

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{category.name}.pdf"'},
    )


@router.get(
    "/{category_id}/export/docx",
    summary="Quizni Word (.docx) sifatida eksport qilish",
    description="Diamond bilan to'lanadi (PDF bilan bir xil qat'iy narx). Javob - "
    "to'g'ridan-to'g'ri .docx fayl.",
)
async def export_quiz_docx(
    category_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    category, questions = await _load_exportable_quiz(db, category_id, current_user.id)

    docx_bytes = build_quiz_docx(quiz_name=category.name, questions=questions)
    await _charge_export(db, current_user, category_id, len(questions))

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{category.name}.docx"'},
    )
