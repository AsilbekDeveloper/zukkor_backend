"""Admin panelidagi "Statistika" sahifasi uchun umumlashtirilgan
ko'rsatkichlarni hisoblaydi (2026-09-29, foydalanuvchi so'rovi: "admin
hamma narsani ko'rib tura olishi kerak"). SQLAdmin'ning o'zi faqat xom
jadval ko'rsatadi - bu yerda shu ma'lumotlar birlashtirilib, bir nechta
tushunarli sonlarga aylantiriladi.

Barcha so'rovlar atayin ODDIY (SQLite testlarda ham, Postgres
production'da ham bir xil ishlaydigan) - `CurrencyTransaction.extra`
kabi JSON ustunlar ichiga kirib guruhlash kabi dialektga bog'liq
narsalardan qochiladi."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_quiz_job import AiQuizGenerationJob
from app.models.currency_transaction import CurrencyTransaction
from app.models.quiz import Category
from app.models.user import User
from app.services.streak import TASHKENT_OFFSET


def _tashkent_day_start_utc(now: datetime) -> datetime:
    local_now = now + TASHKENT_OFFSET
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight - TASHKENT_OFFSET


@dataclass
class CategoryStat:
    name: str
    play_count: int


@dataclass
class AdminAnalytics:
    total_users: int
    active_users: int
    dau: int
    wau: int
    total_games_played: int
    total_coin_in_circulation: int
    total_diamond_in_circulation: int
    top_categories: list[CategoryStat]
    export_count_30d: int
    export_diamond_spent_30d: int
    ai_jobs_completed: int
    ai_jobs_failed: int


async def compute_analytics(db: AsyncSession) -> AdminAnalytics:
    now = datetime.now(timezone.utc)
    day_start = _tashkent_day_start_utc(now)
    week_start = day_start - timedelta(days=7)
    month_start = now - timedelta(days=30)

    total_users = (await db.execute(select(func.count(User.id)))).scalar_one()
    active_users = (
        await db.execute(select(func.count(User.id)).where(User.is_active.is_(True)))
    ).scalar_one()
    dau = (
        await db.execute(select(func.count(User.id)).where(User.last_played_at >= day_start))
    ).scalar_one()
    wau = (
        await db.execute(select(func.count(User.id)).where(User.last_played_at >= week_start))
    ).scalar_one()
    total_games_played = (await db.execute(select(func.coalesce(func.sum(User.games_played), 0)))).scalar_one()
    total_coin = (await db.execute(select(func.coalesce(func.sum(User.coin_balance), 0)))).scalar_one()
    total_diamond = (await db.execute(select(func.coalesce(func.sum(User.diamond_balance), 0)))).scalar_one()

    top_categories_result = await db.execute(
        select(Category.name, Category.play_count)
        .where(Category.owner_user_id.is_(None), Category.is_active.is_(True))
        .order_by(Category.play_count.desc())
        .limit(5)
    )
    top_categories = [CategoryStat(name=name, play_count=count) for name, count in top_categories_result.all()]

    export_count = (
        await db.execute(
            select(func.count(CurrencyTransaction.id)).where(
                CurrencyTransaction.reason == "quiz_export", CurrencyTransaction.created_at >= month_start
            )
        )
    ).scalar_one()
    export_diamond_spent = (
        await db.execute(
            select(func.coalesce(func.sum(-CurrencyTransaction.amount), 0)).where(
                CurrencyTransaction.reason == "quiz_export", CurrencyTransaction.created_at >= month_start
            )
        )
    ).scalar_one()

    ai_completed = (
        await db.execute(select(func.count(AiQuizGenerationJob.id)).where(AiQuizGenerationJob.status == "completed"))
    ).scalar_one()
    ai_failed = (
        await db.execute(select(func.count(AiQuizGenerationJob.id)).where(AiQuizGenerationJob.status == "failed"))
    ).scalar_one()

    return AdminAnalytics(
        total_users=total_users,
        active_users=active_users,
        dau=dau,
        wau=wau,
        total_games_played=total_games_played,
        total_coin_in_circulation=total_coin,
        total_diamond_in_circulation=total_diamond,
        top_categories=top_categories,
        export_count_30d=export_count,
        export_diamond_spent_30d=export_diamond_spent,
        ai_jobs_completed=ai_completed,
        ai_jobs_failed=ai_failed,
    )
