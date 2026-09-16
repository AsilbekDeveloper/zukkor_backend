from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.quiz import Question
from app.models.reported_question import ReportedQuestion
from app.models.user import User
from app.schemas.reports import ReportQuestionRequest, ReportQuestionResponse

router = APIRouter()

# "AI galyutsinatsiyasi" himoyasi #3 (2026-09-19, False Positive: AI
# noto'g'ri tasdiqlagan yomon savol, o'yinchilar tomonidan payqalgan) -
# shuncha TURLI foydalanuvchidan report kelsa, savol admin ko'rib
# chiqishini kutmasdan DARHOL o'yindan olib tashlanadi
# (`ReportedQuestion.uq_reported_question_reporter` bitta foydalanuvchini
# bir savolga faqat bir marta report qilishga cheklaydi, shuning uchun
# oddiy COUNT allaqachon UNIKAL foydalanuvchilar soniga teng).
AUTO_DEACTIVATE_REPORT_THRESHOLD = 3


@router.post(
    "/{question_id}/report",
    response_model=ReportQuestionResponse,
    summary="Savolni muammoli deb belgilash",
)
async def report_question(
    question_id: int,
    data: ReportQuestionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    question = await db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Savol topilmadi")

    existing_result = await db.execute(
        select(ReportedQuestion).where(
            ReportedQuestion.question_id == question_id,
            ReportedQuestion.reporter_user_id == current_user.id,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        # Qayta yuborilsa - eski report yangilanadi (bir xil savol uchun
        # bir nechta qator hosil bo'lmasin, admin panelda spam ko'paymasin).
        # Reporter allaqachon avval ham hisoblangan edi - quyidagi
        # avto-o'chirish hisobiga bu qayta yuborish yangi qo'shimcha
        # qo'shmaydi.
        existing.reason = data.reason
        existing.comment = data.comment
        existing.status = "pending"
    else:
        db.add(
            ReportedQuestion(
                question_id=question_id,
                reporter_user_id=current_user.id,
                reason=data.reason,
                comment=data.comment,
            )
        )
        await db.flush()  # quyidagi hisobga shu YANGI qator ham kirishi uchun

    # Auto-deactivation: agar hali faol bo'lsa va turli foydalanuvchidan
    # kelgan (dismissed qilinmagan) reportlar soni chegaraga yetgan bo'lsa,
    # savol DARHOL o'yindan olib tashlanadi - admin qo'lda ko'rib
    # chiqishini kutmaydi ("Kill Switch" - `app/admin.py`dagi
    # `QuestionAdmin` - keyinroq qo'lda qayta yoqish/butunlay o'chirish
    # uchun ishlatiladi).
    if question.is_active:
        report_count_result = await db.execute(
            select(func.count())
            .select_from(ReportedQuestion)
            .where(ReportedQuestion.question_id == question_id, ReportedQuestion.status != "dismissed")
        )
        if report_count_result.scalar_one() >= AUTO_DEACTIVATE_REPORT_THRESHOLD:
            question.is_active = False

    await db.commit()
    return ReportQuestionResponse()
