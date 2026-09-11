"""Streak-uzilishi haqida eslatma push'i - "Streak eslatmalari" sozlamasi
(`User.streak_reminders`) 2026-09-08'dan beri to'liq ishlagandek ko'rinar
edi (Sozlamalarda haqiqiy tugma, backendda saqlanardi), lekin buni
HAQIQATDA yuboradigan hech narsa yo'q edi - bu fayl shu bo'shliqni to'ldiradi.

Backendda hech qanday scheduler/cron kutubxonasi (APScheduler va h.k.) yo'q,
shuning uchun `notifications.cleanup_old_notifications_loop` bilan bir xil
oddiy asyncio fon-sikli ishlatiladi - farqi shundaki, BU vazifa aniq bir
soat oralig'ida (kechqurun) ishlashi kerak, shuning uchun tez-tez (har 15
daqiqada) tekshiradi, lekin faqat maqsadli soat ichida haqiqiy ish qiladi,
va har bir foydalanuvchiga kuniga faqat bir marta yuborilishini
`User.last_streak_reminder_at` orqali ta'minlaydi.
"""

import asyncio
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.notification import Notification
from app.models.user import User
from app.services.push import send_push_to_user
from app.services.streak import TASHKENT_OFFSET

logger = logging.getLogger("zukkor.streak_reminders")

# Maqsadli soat-oynasini o'tkazib yubormaslik uchun tez-tez tekshiradi -
# `_send_due_streak_reminders` o'zi har chaqiruvda deyarli bepul (soat mos
# kelmasa darhol qaytadi).
_CHECK_INTERVAL_SECONDS = 15 * 60

# Kechqurun 20:00 (Toshkent) - foydalanuvchida kun tugashidan oldin hali
# o'ynash uchun yetarli vaqt qoladi, shu bilan birga "bugun eslataman"
# maqsadiga ham mos.
REMINDER_HOUR_LOCAL = 20


def _local_date(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + TASHKENT_OFFSET).date()


async def send_due_streak_reminders(db: AsyncSession) -> int:
    """Bitta tekshiruv-chaqiruvi - hozir maqsadli soat bo'lmasa darhol 0
    qaytaradi. Yuborilgan eslatmalar sonini qaytaradi (test uchun qulay)."""
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc + TASHKENT_OFFSET
    if now_local.hour != REMINDER_HOUR_LOCAL:
        return 0

    today_local = now_local.date()

    candidates = (
        await db.execute(
            select(User).where(
                User.streak_reminders.is_(True),
                User.current_streak > 0,
                User.last_played_at.is_not(None),
            )
        )
    ).scalars().all()

    sent = 0
    for user in candidates:
        # Seriya aynan "bugun xavf ostida" bo'lishi kerak - kecha o'ynagan
        # (diff==1). 0 bo'lsa bugun allaqachon o'ynagan (xavf yo'q), 1 dan
        # katta bo'lsa seriya allaqachon uzilgan (kech qolingan eslatma
        # chalkashtirib yuboradi, shuning uchun yuborilmaydi).
        if (today_local - _local_date(user.last_played_at)).days != 1:
            continue
        if user.last_streak_reminder_at is not None and _local_date(user.last_streak_reminder_at) == today_local:
            continue  # bugun allaqachon yuborilgan

        try:
            user.last_streak_reminder_at = now_utc
            db.add(Notification(user_id=user.id, kind="streak_reminder"))
            await db.commit()
            await send_push_to_user(
                db,
                user.id,
                "Seriyangiz xavf ostida!",
                f"{user.current_streak} kunlik seriyangizni yo'qotmang — bugun o'ynang!",
                data={"type": "streak_reminder"},
            )
            sent += 1
        except Exception:
            logger.exception("Seriya eslatmasini yuborishda xatolik (user_id=%s)", user.id)
            await db.rollback()

    return sent


async def streak_reminder_loop() -> None:
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await send_due_streak_reminders(db)
        except Exception:
            logger.exception("Seriya eslatmalari siklida xatolik")
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
