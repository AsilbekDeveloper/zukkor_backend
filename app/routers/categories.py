from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.quiz import Category, Question
from app.schemas.quiz import CategoryOut

router = APIRouter()


@router.get("", response_model=list[CategoryOut], summary="Faol kategoriyalar ro'yxati")
async def list_categories(db: AsyncSession = Depends(get_db)):
    question_count_subq = (
        select(Question.category_id, func.count(Question.id).label("cnt"))
        .where(Question.is_active.is_(True))
        .group_by(Question.category_id)
        .subquery()
    )

    stmt = (
        select(Category, func.coalesce(question_count_subq.c.cnt, 0))
        .outerjoin(question_count_subq, question_count_subq.c.category_id == Category.id)
        # AI orqali generatsiya qilingan shaxsiy kategoriyalar (owner_user_id
        # bor) bu yerga chiqmasligi kerak - ular faqat egasiga
        # GET /ai-quiz orqali ko'rinadi, aks holda hamma foydalanuvchining
        # shaxsiy quiz kontenti umumiy ro'yxatga sizib chiqardi.
        .where(Category.is_active.is_(True), Category.owner_user_id.is_(None))
        .order_by(Category.sort_order)
    )

    result = await db.execute(stmt)

    return [
        CategoryOut(
            id=category.id,
            name=category.name,
            icon_name=category.icon_name,
            color_key=category.color_key,
            question_count=question_count,
        )
        for category, question_count in result.all()
    ]
