from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.question_xp_award import QuestionXpAward


async def compute_xp_eligible_ball(
    db: AsyncSession,
    user_id: str,
    is_official_category: bool,
    per_question_answers: list[tuple[int, bool, int]],
) -> int:
    """`per_question_answers` — bitta o'yinda (Solo sessiya / Duel / Lobby
    round) bitta foydalanuvchining har bir savoliga: (question_id,
    is_correct, ball). UGC/AI kategoriyalar (is_official_category=False)
    hech qachon XP bermaydi. Rasmiy kategoriyada har bir (user, question)
    juftligi faqat birinchi to'g'ri javobida XP'ga hissa qo'shadi - shu
    manzilda [[question_xp_award]] jadvaliga yozib qo'yiladi, keyingi
    safar xuddi shu savol qayta uchraganda (istalgan rejimda) endi hisobga
    olinmaydi.
    """
    if not is_official_category:
        return 0

    eligible_ball = 0
    for question_id, is_correct, ball in per_question_answers:
        if not is_correct:
            continue
        existing = await db.execute(
            select(QuestionXpAward.id).where(
                QuestionXpAward.user_id == user_id, QuestionXpAward.question_id == question_id
            )
        )
        if existing.scalar_one_or_none() is not None:
            continue
        eligible_ball += ball
        db.add(QuestionXpAward(user_id=user_id, question_id=question_id))

    return eligible_ball
