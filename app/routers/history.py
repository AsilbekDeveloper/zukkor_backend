from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.duel import Duel
from app.models.lobby_game import LobbyGame, LobbyGameResult
from app.models.quiz import Answer, Category, QuizSession, SessionQuestion
from app.models.user import User
from app.schemas.history import (
    HistoryCategoryOut,
    HistoryEntryOut,
    HistoryLobbyOut,
    HistoryOpponentOut,
    HistoryOut,
)

router = APIRouter()


def _display_name(user: User) -> str:
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    if full_name:
        return full_name
    if user.username:
        return user.username
    return "Foydalanuvchi"


async def _get_solo_entries(db: AsyncSession, user_id: str, limit: int) -> list[HistoryEntryOut]:
    total_questions_subq = (
        select(SessionQuestion.session_id, func.count(SessionQuestion.id).label("total_questions"))
        .group_by(SessionQuestion.session_id)
        .subquery()
    )
    correct_count_subq = (
        select(SessionQuestion.session_id, func.count(Answer.id).label("correct_count"))
        .select_from(Answer)
        .join(SessionQuestion, SessionQuestion.id == Answer.session_question_id)
        .where(Answer.is_correct.is_(True))
        .group_by(SessionQuestion.session_id)
        .subquery()
    )

    stmt = (
        select(
            QuizSession.id.label("session_id"),
            QuizSession.finished_at.label("finished_at"),
            QuizSession.total_ball.label("total_ball"),
            QuizSession.total_xp_earned.label("xp_earned"),
            Category.id.label("category_id"),
            Category.name.label("category_name"),
            Category.icon_name.label("icon_name"),
            Category.color_key.label("color_key"),
            func.coalesce(total_questions_subq.c.total_questions, 0).label("total_questions"),
            func.coalesce(correct_count_subq.c.correct_count, 0).label("correct_count"),
        )
        .join(Category, Category.id == QuizSession.category_id)
        .outerjoin(total_questions_subq, total_questions_subq.c.session_id == QuizSession.id)
        .outerjoin(correct_count_subq, correct_count_subq.c.session_id == QuizSession.id)
        .where(QuizSession.user_id == user_id, QuizSession.finished_at.is_not(None))
        .order_by(QuizSession.finished_at.desc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()

    return [
        HistoryEntryOut(
            session_id=row.session_id,
            category=HistoryCategoryOut(
                id=row.category_id, name=row.category_name, icon_name=row.icon_name, color_key=row.color_key
            ),
            finished_at=row.finished_at,
            correct_count=row.correct_count,
            total_questions=row.total_questions,
            total_ball=row.total_ball,
            xp_earned=row.xp_earned,
            game_mode="solo",
        )
        for row in rows
    ]


async def _get_duel_entries(db: AsyncSession, user_id: str, limit: int) -> list[HistoryEntryOut]:
    stmt = (
        select(Duel)
        .where(
            or_(Duel.user_a_id == user_id, Duel.user_b_id == user_id),
            Duel.status == "finished",
        )
        .order_by(Duel.finished_at.desc())
        .limit(limit)
    )
    duels = (await db.execute(stmt)).scalars().all()

    entries = []
    for duel in duels:
        is_a = duel.user_a_id == user_id
        opponent_id = duel.user_b_id if is_a else duel.user_a_id
        my_correct = duel.user_a_correct if is_a else duel.user_b_correct
        my_ball = duel.user_a_ball if is_a else duel.user_b_ball
        my_xp = duel.user_a_xp if is_a else duel.user_b_xp
        my_result = duel.user_a_result if is_a else duel.user_b_result

        category = await db.get(Category, duel.category_id)
        opponent = await db.get(User, opponent_id)

        entries.append(
            HistoryEntryOut(
                session_id=duel.id,
                category=HistoryCategoryOut(
                    id=category.id, name=category.name, icon_name=category.icon_name, color_key=category.color_key
                ),
                finished_at=duel.finished_at,
                correct_count=my_correct,
                total_questions=duel.total_questions,
                total_ball=my_ball,
                xp_earned=my_xp,
                game_mode="duel",
                opponent=HistoryOpponentOut(
                    name=_display_name(opponent),
                    avatar_color=opponent.avatar_color,
                    avatar_image_path=opponent.avatar_image_path,
                ),
                outcome=my_result,
            )
        )
    return entries


async def _get_lobby_entries(db: AsyncSession, user_id: str, limit: int) -> list[HistoryEntryOut]:
    stmt = (
        select(LobbyGameResult, LobbyGame)
        .join(LobbyGame, LobbyGame.id == LobbyGameResult.lobby_game_id)
        .where(LobbyGameResult.user_id == user_id)
        .order_by(LobbyGame.finished_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    entries = []
    for result, game in rows:
        category = await db.get(Category, game.category_id)
        entries.append(
            HistoryEntryOut(
                session_id=game.id,
                category=HistoryCategoryOut(
                    id=category.id, name=category.name, icon_name=category.icon_name, color_key=category.color_key
                ),
                finished_at=game.finished_at,
                correct_count=result.correct,
                total_questions=game.total_questions,
                total_ball=result.ball,
                xp_earned=result.xp,
                game_mode="lobby",
                lobby=HistoryLobbyOut(rank=result.rank, participant_count=game.participant_count),
            )
        )
    return entries


@router.get("", response_model=HistoryOut, summary="O'yin tarixi")
async def get_history(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    limit = min(max(limit, 1), 100)

    solo_entries = await _get_solo_entries(db, current_user.id, limit)
    duel_entries = await _get_duel_entries(db, current_user.id, limit)
    lobby_entries = await _get_lobby_entries(db, current_user.id, limit)

    merged = sorted(solo_entries + duel_entries + lobby_entries, key=lambda e: e.finished_at, reverse=True)[:limit]

    return HistoryOut(entries=merged)
