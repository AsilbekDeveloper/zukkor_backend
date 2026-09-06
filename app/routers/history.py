from datetime import datetime, timedelta, timezone

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
    WeeklyActivityOut,
)
from app.services.display_name import display_name as _display_name
from app.services.streak import TASHKENT_OFFSET

router = APIRouter()


@router.get("/weekly-activity", response_model=WeeklyActivityOut, summary="So'nggi 7 kunlik faollik")
async def get_weekly_activity(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Oxirgi 7 kun (bugundan boshlab orqaga - dushanba-yakshanba emas,
    "rolling" oyna) ichida qaysi kunlarda kamida bitta o'yin (Solo/Duel/
    Lobby, istalgan turi) tugatilganini qaytaradi - Home'dagi "haftalik
    faollik" nuqta qatori uchun.

    Sana MAHALLIY (Toshkent, UTC+5) vaqt bo'yicha hisoblanadi - xuddi
    `streak.py`dagi kabi (`TASHKENT_OFFSET`). Avval bu yerda `func.date()`
    orqali xom UTC sanadan foydalanilardi, bu esa soat 19:00-23:59 UTC
    (Toshkentda yarim tundan keyin, 00:00-04:59) oralig'ida tugagan
    o'yinlarni streak hisobidan BIR KUN OLDINGI kunga belgilardi - shuning
    uchun karta ustidagi belgilar bilan "current_streak" raqami bir-biriga
    mos kelmay qolishi mumkin edi. Endi ikkalasi ham bir xil offsetdan
    foydalanadi.
    """
    now_utc = datetime.now(timezone.utc)
    today_local = (now_utc + TASHKENT_OFFSET).date()
    window_start_local = today_local - timedelta(days=6)

    solo_finished_ats = (
        await db.execute(
            select(QuizSession.finished_at).where(
                QuizSession.user_id == current_user.id, QuizSession.finished_at.is_not(None)
            )
        )
    ).scalars().all()

    duel_finished_ats = (
        await db.execute(
            select(Duel.finished_at).where(
                or_(Duel.user_a_id == current_user.id, Duel.user_b_id == current_user.id),
                Duel.status == "finished",
            )
        )
    ).scalars().all()

    lobby_finished_ats = (
        await db.execute(
            select(LobbyGame.finished_at)
            .select_from(LobbyGameResult)
            .join(LobbyGame, LobbyGame.id == LobbyGameResult.lobby_game_id)
            .where(LobbyGameResult.user_id == current_user.id)
        )
    ).scalars().all()

    played_local_dates = set()
    for raw in (*solo_finished_ats, *duel_finished_ats, *lobby_finished_ats):
        if raw is None:
            continue
        finished_at = raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
        played_local_dates.add((finished_at + TASHKENT_OFFSET).date())

    days = [(window_start_local + timedelta(days=i)) in played_local_dates for i in range(7)]
    return WeeklyActivityOut(days=days)


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
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    # Har bir manbadan yetarlicha (offset+limit+1) qator olamiz - shunda 3 manbani birlashtirib
    # saralab, [offset:offset+limit] kesib olsak ham, has_more to'g'ri hisoblanadi (manbalar
    # orasida taqsimlanishidan qat'iy nazar)
    fetch_count = offset + limit + 1

    solo_entries = await _get_solo_entries(db, current_user.id, fetch_count)
    duel_entries = await _get_duel_entries(db, current_user.id, fetch_count)
    lobby_entries = await _get_lobby_entries(db, current_user.id, fetch_count)

    merged = sorted(solo_entries + duel_entries + lobby_entries, key=lambda e: e.finished_at, reverse=True)

    page = merged[offset : offset + limit]
    has_more = len(merged) > offset + limit

    return HistoryOut(entries=page, has_more=has_more)
