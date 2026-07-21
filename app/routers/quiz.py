import random
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.quiz import Answer, Category, Question, QuizSession, SessionQuestion
from app.models.user import User
from app.models.xp_event import XpEvent
from app.schemas.quiz import (
    AnswerRequest,
    AnswerResponse,
    QuestionOut,
    QuizStartRequest,
    QuizStartResponse,
    QuizSummary,
)
from app.services.scoring import calculate_ball
from app.services.streak import update_streak

router = APIRouter()


async def _pick_random_question(db: AsyncSession, category_id: int, exclude_question_ids: list[int]):
    stmt = select(Question).where(Question.category_id == category_id, Question.is_active.is_(True))
    if exclude_question_ids:
        stmt = stmt.where(Question.id.notin_(exclude_question_ids))
    stmt = stmt.order_by(func.random()).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _shuffle_options(question: Question) -> list[int]:
    return random.sample(range(len(question.options)), len(question.options))


def _shuffled_options_list(question: Question, option_order: list[int]) -> list[str]:
    return [question.options[i] for i in option_order]


@router.post(
    "/start",
    response_model=QuizStartResponse,
    summary="Solo viktorina sessiyasini boshlash",
)
async def start_quiz(
    data: QuizStartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    category = await db.get(Category, data.category_id)
    if category is None or not category.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kategoriya topilmadi")

    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    stale_result = await db.execute(
        select(QuizSession).where(
            QuizSession.user_id == current_user.id,
            QuizSession.finished_at.is_(None),
            QuizSession.started_at < one_hour_ago,
        )
    )
    for stale_session in stale_result.scalars():
        stale_session.finished_at = datetime.now(timezone.utc)

    count_result = await db.execute(
        select(func.count()).select_from(Question).where(
            Question.category_id == category.id, Question.is_active.is_(True)
        )
    )
    available = count_result.scalar_one()
    if available == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bu kategoriyada savollar yo'q")

    total = min(data.question_count, available)

    first_question = await _pick_random_question(db, category.id, [])
    option_order = _shuffle_options(first_question)

    session = QuizSession(user_id=current_user.id, category_id=category.id)
    db.add(session)
    await db.flush()

    session_question = SessionQuestion(
        session_id=session.id,
        question_id=first_question.id,
        order=1,
        total=total,
        time_limit_ms=15000,
        option_order=option_order,
    )
    db.add(session_question)
    await db.commit()
    await db.refresh(session_question)

    return QuizStartResponse(
        session_id=session.id,
        question=QuestionOut(
            session_question_id=session_question.id,
            question_text=first_question.question_text,
            options=_shuffled_options_list(first_question, option_order),
            order=session_question.order,
            total=session_question.total,
            time_limit_ms=session_question.time_limit_ms,
        ),
    )


@router.post(
    "/{session_id}/answer",
    response_model=AnswerResponse,
    response_model_exclude_none=True,
    summary="Joriy savolga javob berish",
)
async def answer_question(
    session_id: str,
    data: AnswerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(QuizSession, session_id)
    if session is None or session.user_id != current_user.id or session.finished_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sessiya topilmadi")

    session_question = await db.get(SessionQuestion, data.session_question_id)
    if session_question is None or session_question.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Noto'g'ri savol")

    existing_answer = await db.execute(
        select(Answer).where(Answer.session_question_id == session_question.id)
    )
    if existing_answer.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bu savolga allaqachon javob berilgan")

    question = await db.get(Question, session_question.question_id)

    # session_question.option_order — shu savol ko'rsatilganda ishlatilgan aralashtirilgan tartib;
    # to'g'ri javob va tekshiruv shu tartibga mos indeks bo'yicha olinadi (client ko'rgan tartibga mos)
    shuffled_correct_index = session_question.option_order.index(question.correct_option_index)

    now = datetime.now(timezone.utc)
    elapsed_ms = (now - session_question.broadcast_at).total_seconds() * 1000
    is_correct = data.selected_option is not None and data.selected_option == shuffled_correct_index
    ball = calculate_ball(elapsed_ms, session_question.time_limit_ms, is_correct)

    db.add(
        Answer(
            session_question_id=session_question.id,
            selected_option=data.selected_option,
            is_correct=is_correct,
            ball=ball,
            answered_at=now,
        )
    )

    if session_question.order < session_question.total:
        used_result = await db.execute(
            select(SessionQuestion.question_id).where(SessionQuestion.session_id == session_id)
        )
        used_question_ids = [row[0] for row in used_result.all()]

        next_question = await _pick_random_question(db, session.category_id, used_question_ids)
        next_option_order = _shuffle_options(next_question)

        next_session_question = SessionQuestion(
            session_id=session_id,
            question_id=next_question.id,
            order=session_question.order + 1,
            total=session_question.total,
            time_limit_ms=session_question.time_limit_ms,
            option_order=next_option_order,
        )
        db.add(next_session_question)
        await db.commit()
        await db.refresh(next_session_question)

        return AnswerResponse(
            correct=is_correct,
            correct_option_index=shuffled_correct_index,
            ball_earned=ball,
            next_question=QuestionOut(
                session_question_id=next_session_question.id,
                question_text=next_question.question_text,
                options=_shuffled_options_list(next_question, next_option_order),
                order=next_session_question.order,
                total=next_session_question.total,
                time_limit_ms=next_session_question.time_limit_ms,
            ),
        )

    # Oxirgi savol — sessiyani yakunlash
    session.finished_at = now

    all_answers_result = await db.execute(
        select(Answer)
        .join(SessionQuestion, Answer.session_question_id == SessionQuestion.id)
        .where(SessionQuestion.session_id == session_id)
    )
    all_answers = all_answers_result.scalars().all()

    total_ball = sum(a.ball for a in all_answers)
    correct_count = sum(1 for a in all_answers if a.is_correct)
    xp_earned = round(total_ball / 100)

    session.total_ball = total_ball
    session.total_xp_earned = xp_earned

    current_user.total_xp += xp_earned
    current_user.games_played += 1
    update_streak(current_user, now)
    db.add(XpEvent(user_id=current_user.id, amount=xp_earned))

    await db.commit()

    return AnswerResponse(
        correct=is_correct,
        correct_option_index=shuffled_correct_index,
        ball_earned=ball,
        session_complete=True,
        summary=QuizSummary(
            total_ball=total_ball,
            correct_count=correct_count,
            total_questions=session_question.total,
            xp_earned=xp_earned,
            new_total_xp=current_user.total_xp,
        ),
    )
