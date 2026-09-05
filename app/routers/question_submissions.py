from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.limiter import limiter
from app.dependencies.auth import get_current_user
from app.models.question_submission import QuestionSubmission
from app.models.quiz import Category, Question
from app.models.user import User
from app.schemas.ai_quiz import QuestionSubmissionRequest, QuestionSubmissionResponse
from app.services.question_moderation import QuestionModerationError, moderate_question

router = APIRouter()


def _validate_submission_shape(data: QuestionSubmissionRequest) -> str | None:
    """Shakl xatosi bo'lsa foydalanuvchiga ko'rsatiladigan xabarni qaytaradi,
    hammasi joyida bo'lsa None - AI chaqirmasdan oldin, bepul tekshiruv."""
    if not data.question_text.strip():
        return "Savol matni bo'sh bo'lishi mumkin emas"
    if len(data.options) != 4 or not all(isinstance(option, str) and option.strip() for option in data.options):
        return "Aynan 4 ta bo'sh bo'lmagan javob varianti kerak"
    if not (0 <= data.correct_option_index < 4):
        return "To'g'ri javob indeksi 0 dan 3 gacha bo'lishi kerak"
    return None


@router.post(
    "/submit",
    response_model=QuestionSubmissionResponse,
    summary="Tizimga yangi savol qo'shish (AI tekshiruvidan o'tadi)",
)
@limiter.limit("5/minute")
async def submit_question(
    request: Request,
    data: QuestionSubmissionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    shape_error = _validate_submission_shape(data)
    if shape_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=shape_error)

    question_text = data.question_text.strip()
    options = [option.strip() for option in data.options]

    if data.category_id is not None:
        requested_category = await db.get(Category, data.category_id)
        if (
            requested_category is None
            or requested_category.owner_user_id is not None
            or not requested_category.is_active
        ):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kategoriya topilmadi")

    # Dublikat tekshiruvi - AI chaqirmasdan oldin, bepul va tez. Faqat
    # katta-kichik harf va boshi/oxiridagi bo'shliqni e'tiborsiz qoldiradi
    # (to'liq semantik/fuzzy taqqoslash emas - MVP uchun aynan bir xil
    # matnli qayta yuborishlarni ushlash yetarli).
    duplicate_result = await db.execute(
        select(Question.id)
        .where(Question.is_active.is_(True), func.lower(Question.question_text) == question_text.lower())
        .limit(1)
    )
    if duplicate_result.scalar_one_or_none() is not None:
        db.add(
            QuestionSubmission(
                submitter_user_id=current_user.id,
                question_text=question_text,
                options=options,
                correct_option_index=data.correct_option_index,
                requested_category_id=data.category_id,
                status="rejected",
                ai_feedback="Bu savol allaqachon tizimda mavjud",
            )
        )
        await db.commit()
        return QuestionSubmissionResponse(approved=False, rejection_reason="Bu savol allaqachon tizimda mavjud")

    categories_result = await db.execute(
        select(Category.id, Category.name).where(Category.is_active.is_(True), Category.owner_user_id.is_(None))
    )
    categories = [(category_id, name) for category_id, name in categories_result.all()]
    if not categories:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Hozircha faol kategoriya yo'q")

    try:
        result = await moderate_question(
            question_text=question_text,
            options=options,
            correct_option_index=data.correct_option_index,
            requested_category_id=data.category_id,
            categories=categories,
        )
    except QuestionModerationError as exc:
        # Texnik xato ham audit uchun saqlanadi - "pending" holati aynan
        # AI hech qachon javob bermagan hollar uchun (model docstring'iga
        # qarang).
        db.add(
            QuestionSubmission(
                submitter_user_id=current_user.id,
                question_text=question_text,
                options=options,
                correct_option_index=data.correct_option_index,
                requested_category_id=data.category_id,
                status="pending",
                ai_feedback=str(exc),
            )
        )
        await db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    submission = QuestionSubmission(
        submitter_user_id=current_user.id,
        question_text=question_text,
        options=options,
        correct_option_index=data.correct_option_index,
        requested_category_id=data.category_id,
    )

    if not result.is_approved:
        submission.status = "rejected"
        submission.ai_feedback = result.rejection_reason
        db.add(submission)
        await db.commit()
        return QuestionSubmissionResponse(approved=False, rejection_reason=result.rejection_reason)

    new_question = Question(
        category_id=result.category_id,
        question_text=question_text,
        options=options,
        correct_option_index=data.correct_option_index,
        is_active=True,
    )
    db.add(new_question)
    await db.flush()  # new_question.id quyida kerak bo'ladi

    submission.status = "approved"
    submission.resulting_category_id = result.category_id
    submission.resulting_question_id = new_question.id
    db.add(submission)
    await db.commit()

    category_name = next((name for category_id, name in categories if category_id == result.category_id), None)

    return QuestionSubmissionResponse(
        approved=True,
        category_id=result.category_id,
        category_name=category_name,
        question_id=new_question.id,
    )
