"""Taklif (referral) tizimi (2026-10-15, foydalanuvchi qarori).

Qoidalar:
- Foydalanuvchi o'z kodi/havolasini ulashadi; do'sti kod bilan ro'yxatdan
  o'tadi (`User.referred_by_user_id`).
- Mukofot do'st birinchi SIFATLI o'yinini tugatganda ikkala tomonga
  BIR MARTA beriladi: `REFERRAL_REWARD_COINS` Coin + `REFERRAL_REWARD_DIAMONDS`
  Diamond. Sifatli o'yin: rasmiy kategoriya va kamida
  `REFERRAL_MIN_QUESTIONS` savol (1 savollik shaxsiy quiz bilan bonus
  yig'ib bo'lmasin).
- Taklif qilganlar soni bo'yicha cheklov yo'q (`REFERRAL_DAILY_LIMIT = 0`),
  lekin kunlik cheklov admin paneldan yoqiladi.
- Diamond balans chegarasi (`wallet.DIAMOND_BALANCE_CAP`) qo'llanadi.
"""

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.quiz import Category
from app.models.referral import ReferralReward
from app.models.user import User
from app.services import economy_config, wallet
from app.services.display_name import display_name
from app.services.push import send_push_to_user
from app.services.streak import TASHKENT_OFFSET

# Ro'yxatdan o'tgandan keyin kodni qo'lda kiritish mumkin bo'lgan muddat.
APPLY_CODE_WINDOW_DAYS = 7

CODE_PATTERN = re.compile(r"^[A-Z0-9]{4,12}$")


def normalize_code(raw: str) -> str:
    return raw.strip().upper()


async def ensure_referral_code(db: AsyncSession, user: User) -> str:
    """Eski foydalanuvchilarda (kod paydo bo'lishidan oldin ro'yxatdan
    o'tganlar) kod bo'lmasligi mumkin - birinchi so'rovda yaratiladi."""
    if user.referral_code:
        return user.referral_code
    for _ in range(10):
        candidate = wallet.generate_referral_code()
        taken = (await db.execute(select(User.id).where(User.referral_code == candidate))).scalar_one_or_none()
        if taken is None:
            user.referral_code = candidate
            await db.commit()
            return candidate
    raise RuntimeError("Noyob taklif kodi yaratib bo'lmadi")


async def find_referrer(db: AsyncSession, code: str) -> User | None:
    code = normalize_code(code)
    if not CODE_PATTERN.match(code):
        return None
    return (await db.execute(select(User).where(User.referral_code == code))).scalar_one_or_none()


class ApplyCodeError(Exception):
    """Foydalanuvchiga ko'rsatiladigan aniq sabab bilan."""


async def apply_code(db: AsyncSession, user: User, raw_code: str) -> None:
    """Ro'yxatdan o'tgandan KEYIN kodni qo'llash (o'rnatishdan kod
    o'tmagan bo'lsa zaxira yo'l). Faqat hali hech kim taklif qilmagan va
    yaqinda (`APPLY_CODE_WINDOW_DAYS`) ro'yxatdan o'tgan foydalanuvchi
    uchun. Chaqiruvchi COMMIT qiladi."""
    if user.referred_by_user_id is not None:
        raise ApplyCodeError("Siz allaqachon taklif kodini kiritgansiz")
    created_at = user.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created_at > timedelta(days=APPLY_CODE_WINDOW_DAYS):
        raise ApplyCodeError("Taklif kodini faqat ro'yxatdan o'tgandan keyingi 7 kun ichida kiritish mumkin")

    referrer = await find_referrer(db, raw_code)
    if referrer is None:
        raise ApplyCodeError("Taklif kodi topilmadi")
    if referrer.id == user.id:
        raise ApplyCodeError("O'zingizning kodingizni kiritib bo'lmaydi")
    user.referred_by_user_id = referrer.id


def can_apply_code(user: User) -> bool:
    if user.referred_by_user_id is not None:
        return False
    created_at = user.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created_at <= timedelta(days=APPLY_CODE_WINDOW_DAYS)


def _tashkent_day_start_utc(now_utc: datetime) -> datetime:
    local_midnight = (now_utc + TASHKENT_OFFSET).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight - TASHKENT_OFFSET


async def award_if_qualified(
    db: AsyncSession, user: User, *, category: Category, question_count: int
) -> ReferralReward | None:
    """Taklif qilingan `user` hozirgina o'yin tugatdi - agar bu uning birinchi
    SIFATLI o'yini bo'lsa, ikkala tomonga mukofot beradi. Chaqiruvchi
    COMMIT qiladi va muvaffaqiyatli bo'lsa `notify_referrer`ni chaqiradi."""
    if not user.referred_by_user_id:
        return None
    if category.owner_user_id is not None:  # foydalanuvchi/AI quizlari hisoblanmaydi
        return None
    min_questions = await economy_config.get_int(db, economy_config.REFERRAL_MIN_QUESTIONS)
    if question_count < min_questions:
        return None
    if await db.get(ReferralReward, user.id) is not None:
        return None

    referrer = await db.get(User, user.referred_by_user_id)
    if referrer is None:
        return None

    daily_limit = await economy_config.get_int(db, economy_config.REFERRAL_DAILY_LIMIT)
    if daily_limit > 0:
        day_start = _tashkent_day_start_utc(datetime.now(timezone.utc))
        today_count = (
            await db.execute(
                select(func.count())
                .select_from(ReferralReward)
                .where(ReferralReward.referrer_user_id == referrer.id, ReferralReward.created_at >= day_start)
            )
        ).scalar_one()
        if today_count >= daily_limit:
            return None  # keyingi sifatli o'yinda (ertasi kuni) qayta uriniladi

    coins = await economy_config.get_int(db, economy_config.REFERRAL_REWARD_COINS)
    diamonds = await economy_config.get_int(db, economy_config.REFERRAL_REWARD_DIAMONDS)

    async def _credit(target: User, reason: str, other_key: str, other_id: str) -> tuple[int, int]:
        extra = {other_key: other_id}
        got_coins = await wallet.credit_coin(db, target, coins, reason, extra=extra) if coins > 0 else 0
        got_diamonds = await wallet.credit_diamond(db, target, diamonds, reason, extra=extra) if diamonds > 0 else 0
        return got_coins, got_diamonds

    referrer_coins, referrer_diamonds = await _credit(referrer, "referral", "referred_user_id", user.id)
    referred_coins, referred_diamonds = await _credit(user, "referral_welcome", "referrer_user_id", referrer.id)

    reward = ReferralReward(
        referred_user_id=user.id,
        referrer_user_id=referrer.id,
        referrer_coins=referrer_coins,
        referrer_diamonds=referrer_diamonds,
        referred_coins=referred_coins,
        referred_diamonds=referred_diamonds,
    )
    db.add(reward)
    db.add(Notification(user_id=referrer.id, kind="referral_reward", related_user_id=user.id))
    return reward


async def notify_referrer(db: AsyncSession, reward: ReferralReward) -> None:
    """`award_if_qualified` COMMIT qilingandan KEYIN - taklif qilganga push."""
    friend = await db.get(User, reward.referred_user_id)
    name = display_name(friend) if friend is not None else "Do'stingiz"
    await send_push_to_user(
        db,
        reward.referrer_user_id,
        "Taklif mukofoti",
        f"{name} Zukkorda o'ynadi: +{reward.referrer_coins} Coin, +{reward.referrer_diamonds} Diamond",
        data={"type": "referral_reward"},
    )

