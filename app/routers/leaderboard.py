from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.duel import Duel, DuelAnswer
from app.models.friendship import Friendship
from app.models.lobby_game import LobbyGame, LobbyGameResult
from app.models.quiz import Answer, Category, QuizSession, SessionQuestion
from app.models.user import User
from app.models.xp_event import XpEvent
from app.schemas.leaderboard import (
    CategoryLeaderboardOut,
    CategoryRankEntryOut,
    LeaderboardOut,
    PlayerStatsOut,
    RankEntryOut,
)
from app.services.leveling import compute_level
from app.services.streak import TASHKENT_OFFSET

router = APIRouter()

VALID_SCOPES = ("all_time", "weekly", "friends")


def _all_time_ranked_subquery():
    return (
        select(
            User.id,
            User.username,
            User.first_name,
            User.last_name,
            User.avatar_color,
            User.avatar_image_path,
            User.total_xp,
            func.row_number().over(order_by=(User.total_xp.desc(), User.created_at.asc(), User.id.asc())).label("rank"),
        )
        .where(User.is_active.is_(True))
        .subquery()
    )


def _weekly_ranked_subquery():
    week_start = datetime.now(timezone.utc) - timedelta(days=7)
    weekly_xp_subq = (
        select(XpEvent.user_id, func.sum(XpEvent.amount).label("weekly_xp"))
        .where(XpEvent.earned_at >= week_start)
        .group_by(XpEvent.user_id)
        .subquery()
    )
    weekly_total = func.coalesce(weekly_xp_subq.c.weekly_xp, 0)
    return (
        select(
            User.id,
            User.username,
            User.first_name,
            User.last_name,
            User.avatar_color,
            User.avatar_image_path,
            weekly_total.label("total_xp"),
            func.row_number().over(order_by=(weekly_total.desc(), User.created_at.asc(), User.id.asc())).label("rank"),
        )
        .outerjoin(weekly_xp_subq, weekly_xp_subq.c.user_id == User.id)
        .where(User.is_active.is_(True))
        .subquery()
    )


def _friends_ranked_subquery(current_user_id: str):
    friend_ids_subq = select(Friendship.friend_id).where(Friendship.user_id == current_user_id)
    return (
        select(
            User.id,
            User.username,
            User.first_name,
            User.last_name,
            User.avatar_color,
            User.avatar_image_path,
            User.total_xp,
            func.row_number().over(order_by=(User.total_xp.desc(), User.created_at.asc(), User.id.asc())).label("rank"),
        )
        .where(
            User.is_active.is_(True),
            or_(User.id == current_user_id, User.id.in_(friend_ids_subq)),
        )
        .subquery()
    )


def _category_xp_subquery(category_id: int, since: datetime | None):
    """Berilgan kategoriyada har bir foydalanuvchi to'plagan XP'ni uchala
    o'yin rejimidan (yakka, Duel, Xona) yig'ib, UNION ALL orqali birlashtiradi
    - `get_player_stats`dagi g'alaba foizi hisob-kitobi bilan bir xil naqsh
    (uchala manbani ham hisobga olish), faqat umumiy emas, shu kategoriya
    bo'yicha filtrlangan holda."""
    solo = select(QuizSession.user_id.label("uid"), QuizSession.total_xp_earned.label("xp")).where(
        QuizSession.category_id == category_id
    )
    duel_a = select(Duel.user_a_id.label("uid"), Duel.user_a_xp.label("xp")).where(
        Duel.category_id == category_id, Duel.status == "finished"
    )
    duel_b = select(Duel.user_b_id.label("uid"), Duel.user_b_xp.label("xp")).where(
        Duel.category_id == category_id, Duel.status == "finished"
    )
    lobby = (
        select(LobbyGameResult.user_id.label("uid"), LobbyGameResult.xp.label("xp"))
        .select_from(LobbyGameResult)
        .join(LobbyGame, LobbyGame.id == LobbyGameResult.lobby_game_id)
        .where(LobbyGame.category_id == category_id)
    )

    if since is not None:
        solo = solo.where(QuizSession.finished_at >= since)
        duel_a = duel_a.where(Duel.finished_at >= since)
        duel_b = duel_b.where(Duel.finished_at >= since)
        lobby = lobby.where(LobbyGame.finished_at >= since)

    combined = union_all(solo, duel_a, duel_b, lobby).subquery()
    return (
        select(combined.c.uid, func.coalesce(func.sum(combined.c.xp), 0).label("category_xp"))
        .group_by(combined.c.uid)
        .subquery()
    )


def _category_ranked_subquery(category_id: int, since: datetime | None):
    xp_subq = _category_xp_subquery(category_id, since)
    return (
        select(
            User.id,
            User.username,
            User.first_name,
            User.last_name,
            User.avatar_color,
            User.avatar_image_path,
            xp_subq.c.category_xp,
            func.row_number()
            .over(order_by=(xp_subq.c.category_xp.desc(), User.created_at.asc(), User.id.asc()))
            .label("rank"),
        )
        .join(xp_subq, xp_subq.c.uid == User.id)
        .where(User.is_active.is_(True))
        .subquery()
    )


def _category_rank_entry_out(row, current_user_id: str) -> CategoryRankEntryOut:
    return CategoryRankEntryOut(
        user_id=row.id,
        rank=row.rank,
        username=row.username,
        first_name=row.first_name,
        last_name=row.last_name,
        avatar_color=row.avatar_color,
        avatar_image_path=row.avatar_image_path,
        category_xp=row.category_xp,
        is_me=row.id == current_user_id,
    )


def _rank_entry_out(row, current_user_id: str) -> RankEntryOut:
    level, level_title, next_level_xp, _current_level_xp = compute_level(row.total_xp)
    return RankEntryOut(
        user_id=row.id,
        rank=row.rank,
        username=row.username,
        first_name=row.first_name,
        last_name=row.last_name,
        avatar_color=row.avatar_color,
        avatar_image_path=row.avatar_image_path,
        total_xp=row.total_xp,
        level=level,
        level_title=level_title,
        next_level_xp=next_level_xp,
        is_me=row.id == current_user_id,
    )


@router.get("", response_model=LeaderboardOut, summary="Reyting ro'yxati")
async def get_leaderboard(
    limit: int = 20,
    offset: int = 0,
    scope: str = "all_time",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if scope not in VALID_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"scope quyidagilardan biri bo'lishi kerak: {VALID_SCOPES}",
        )

    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    if scope == "weekly":
        ranked = _weekly_ranked_subquery()
    elif scope == "friends":
        ranked = _friends_ranked_subquery(current_user.id)
    else:
        ranked = _all_time_ranked_subquery()

    if scope == "friends":
        # Do'stlar soni tabiiy ravishda cheklangan - butun to'plamni olib, o'zini chiqarib
        # tashlab, keyin offset/limit'ni shu yerda qo'llaymiz
        all_rows = (await db.execute(select(ranked).order_by(ranked.c.rank).limit(1000))).all()
        filtered = [row for row in all_rows if row.id != current_user.id]
        top_rows = filtered[offset : offset + limit]
        has_more = len(filtered) > offset + limit
    else:
        rows = (
            await db.execute(select(ranked).order_by(ranked.c.rank).offset(offset).limit(limit + 1))
        ).all()
        has_more = len(rows) > limit
        top_rows = rows[:limit]

    me_row = (await db.execute(select(ranked).where(ranked.c.id == current_user.id))).first()
    if me_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foydalanuvchi topilmadi")

    return LeaderboardOut(
        entries=[_rank_entry_out(row, current_user.id) for row in top_rows],
        me=_rank_entry_out(me_row, current_user.id),
        has_more=has_more,
    )


@router.get(
    "/category/{category_id}",
    response_model=CategoryLeaderboardOut,
    summary="Kategoriya bo'yicha reyting",
)
async def get_category_leaderboard(
    category_id: int,
    limit: int = 20,
    offset: int = 0,
    scope: str = "all_time",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if scope not in VALID_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"scope quyidagilardan biri bo'lishi kerak: {VALID_SCOPES}",
        )

    category = await db.get(Category, category_id)
    if category is None or not category.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kategoriya topilmadi")

    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    since = datetime.now(timezone.utc) - timedelta(days=7) if scope == "weekly" else None
    ranked = _category_ranked_subquery(category_id, since)

    if scope == "friends":
        friend_ids_subq = select(Friendship.friend_id).where(Friendship.user_id == current_user.id)
        base_stmt = select(ranked).where(
            or_(ranked.c.id == current_user.id, ranked.c.id.in_(friend_ids_subq))
        )
        all_rows = (await db.execute(base_stmt.order_by(ranked.c.rank).limit(1000))).all()
        filtered = [row for row in all_rows if row.id != current_user.id]
        top_rows = filtered[offset : offset + limit]
        has_more = len(filtered) > offset + limit
    else:
        rows = (
            await db.execute(select(ranked).order_by(ranked.c.rank).offset(offset).limit(limit + 1))
        ).all()
        has_more = len(rows) > limit
        top_rows = rows[:limit]

    me_row = (await db.execute(select(ranked).where(ranked.c.id == current_user.id))).first()

    return CategoryLeaderboardOut(
        category_id=category.id,
        category_name=category.name,
        entries=[_category_rank_entry_out(row, current_user.id) for row in top_rows],
        me=_category_rank_entry_out(me_row, current_user.id) if me_row is not None else None,
        has_more=has_more,
    )


@router.get("/{user_id}", response_model=PlayerStatsOut, summary="Bitta o'yinchi statistikasi")
async def get_player_stats(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ranked = _all_time_ranked_subquery()
    row = (await db.execute(select(ranked).where(ranked.c.id == user_id))).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foydalanuvchi topilmadi")

    # "G'alaba foizi" — barcha rejimlarda (yakka, duel, xona) javob berilgan
    # savollarning necha foizi to'g'ri bo'lganini bildiradi, xuddi yonidagi
    # "Jami o'yinlar" (`games_played`) kabi barcha rejimlarni birlashtiradi -
    # faqat yakka viktorina javoblarini hisoblasa, faol duel/xona
    # o'yinchisining foizi haqiqiy natijasiga mos kelmay qolardi.
    solo_stmt = (
        select(
            func.count(Answer.id).label("total"),
            func.count(Answer.id).filter(Answer.is_correct.is_(True)).label("correct"),
        )
        .select_from(Answer)
        .join(SessionQuestion, SessionQuestion.id == Answer.session_question_id)
        .join(QuizSession, QuizSession.id == SessionQuestion.session_id)
        .where(QuizSession.user_id == user_id)
    )
    solo_totals = (await db.execute(solo_stmt)).one()

    duel_stmt = select(
        func.count(DuelAnswer.id).label("total"),
        func.count(DuelAnswer.id).filter(DuelAnswer.is_correct.is_(True)).label("correct"),
    ).where(DuelAnswer.user_id == user_id)
    duel_totals = (await db.execute(duel_stmt)).one()

    lobby_stmt = (
        select(
            func.coalesce(func.sum(LobbyGame.total_questions), 0).label("total"),
            func.coalesce(func.sum(LobbyGameResult.correct), 0).label("correct"),
        )
        .select_from(LobbyGameResult)
        .join(LobbyGame, LobbyGame.id == LobbyGameResult.lobby_game_id)
        .where(LobbyGameResult.user_id == user_id)
    )
    lobby_totals = (await db.execute(lobby_stmt)).one()

    total_answered = solo_totals.total + duel_totals.total + lobby_totals.total
    total_correct = solo_totals.correct + duel_totals.correct + lobby_totals.correct
    win_rate = round(total_correct / total_answered * 100) if total_answered else 0

    # Profil sahifasida ko'rsatish uchun - ikkalasi ham istalgan
    # foydalanuvchi (o'zi yoki boshqa) uchun ochiq ma'lumot: do'stlar soni
    # va u ochiq (public) qilib qo'ygan quizlari soni.
    friends_count = (
        await db.execute(select(func.count()).select_from(Friendship).where(Friendship.user_id == user_id))
    ).scalar_one()
    public_quiz_count = (
        await db.execute(
            select(func.count()).select_from(Category).where(
                Category.owner_user_id == user_id, Category.visibility == "public", Category.is_active.is_(True)
            )
        )
    ).scalar_one()

    user = await db.get(User, user_id)
    level, level_title, next_level_xp, current_level_xp = compute_level(row.total_xp)

    # `rank` yuqorida har doim jonli hisoblanadi (hech qachon saqlanmaydi) -
    # "eng yaxshi qachondir erishilgan o'rin" yutug'i uchun shu yerda
    # opportunistik ravishda yangilanadi (past qiymat = yaxshiroq o'rin,
    # shuning uchun faqat KAMAYSA yoki hali umuman bo'lmasa yangilanadi).
    # Alohida fon-jarayon shart emas - statistika har safar so'ralganda
    # (deyarli har o'yindan keyin, Home/Profil ochilganda) tekshiriladi.
    if user.best_rank_achieved is None or row.rank < user.best_rank_achieved:
        user.best_rank_achieved = row.rank
        await db.commit()

    # `current_streak` faqat o'yin tugagan payt (`update_streak`) yangilanadi -
    # agar foydalanuvchi bir necha kun o'ynamay qo'ysa, ustunda ESKI qiymat
    # saqlanib qoladi ("stale"), chunki uni 0'ga tushiradigan alohida fon-vazifa
    # yo'q. Shu yerda - har safar statistika o'qilganda - streak hali "tirik"mi
    # tekshiramiz: agar oxirgi o'yindan beri (mahalliy vaqt bo'yicha) bir kundan
    # ortiq o'tgan bo'lsa, streak allaqachon uzilgan, shuning uchun 0 qaytaramiz
    # (keyingi o'yinda `update_streak` uni qaytadan 1'dan boshlaydi - bu yerdagi
    # o'zgarish faqat KO'RSATISHGA tegishli, ustunning o'zini o'zgartirmaydi).
    effective_streak = user.current_streak
    if user.last_played_at is None:
        effective_streak = 0
    else:
        last_played_at = user.last_played_at
        if last_played_at.tzinfo is None:
            last_played_at = last_played_at.replace(tzinfo=timezone.utc)
        last_local_date = (last_played_at + TASHKENT_OFFSET).date()
        today_local_date = (datetime.now(timezone.utc) + TASHKENT_OFFSET).date()
        if (today_local_date - last_local_date).days > 1:
            effective_streak = 0

    return PlayerStatsOut(
        user_id=row.id,
        rank=row.rank,
        username=row.username,
        first_name=row.first_name,
        last_name=row.last_name,
        avatar_color=row.avatar_color,
        avatar_image_path=row.avatar_image_path,
        total_xp=row.total_xp,
        level=level,
        level_title=level_title,
        next_level_xp=next_level_xp,
        current_level_xp=current_level_xp,
        current_streak=effective_streak,
        longest_streak=user.longest_streak,
        games_played=user.games_played,
        win_rate_percent=win_rate,
        total_wins=user.total_wins,
        best_rank_achieved=user.best_rank_achieved,
        friends_count=friends_count,
        public_quiz_count=public_quiz_count,
    )
