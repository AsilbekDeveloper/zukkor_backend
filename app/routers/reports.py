from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.quiz import Question
from app.models.reported_question import ReportedQuestion
from app.models.user import User
from app.schemas.reports import ReportQuestionRequest, ReportQuestionResponse

router = APIRouter()


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

    await db.commit()
    return ReportQuestionResponse()
