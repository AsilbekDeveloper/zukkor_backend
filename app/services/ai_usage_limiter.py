"""Gemini API uchun global kunlik "circuit breaker" (2026-09-16, xavfsizlik
auditi asosida qo'shildi).

Bu individual foydalanuvchi/IP limitlaridan (Diamond balansi, slowapi rate
limit) MUSTAQIL, yuqori (butun tizim bo'yicha) chegara: agar bir kunda
(Toshkent mahalliy kuni) `settings.MAX_DAILY_GEMINI_CALLS`dan ko'p Gemini
chaqiruvi bo'lsa, YANGI so'rovlar Gemini'ni umuman chaqirmasdan
`DailyLimitExceeded` bilan rad etiladi - masalan minglab soxta akkaunt
orqali (har biri o'z bepul Diamond balansi bilan) yoki narx-hisoblash
xatosi bo'lsa ham, kunlik umumiy zarar shu bilan yuqoridan cheklangan
bo'lib qoladi.

Hisoblagich HAQIQIY Gemini chaqiruvi (yoki chaqirishga qat'iy niyat -
Diamond band qilingandan KEYIN, lekin Gemini'ning o'zi chaqirilishidan
OLDIN) sonini hisoblaydi, endpoint so'rovlari sonini emas - shuning uchun
diamondi yetarli bo'lmagan yoki boshqa sabab bilan darhol rad etilgan
so'rovlar bu hisoblagichga ta'sir qilmaydi (`app/routers/ai_quiz.py`dagi
chaqiruv tartibiga qarang).
"""

from datetime import datetime, timezone

from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_usage import DailyAiUsage
from app.services.streak import TASHKENT_OFFSET


class DailyLimitExceeded(Exception):
    """Bugungi Gemini chaqiruvlari soni `settings.MAX_DAILY_GEMINI_CALLS`ga yetdi."""


def _tashkent_today() -> str:
    return (datetime.now(timezone.utc) + TASHKENT_OFFSET).date().isoformat()


async def _increment_and_get_count(db: AsyncSession, today: str) -> int:
    """Bugungi hisoblagichni ATOMIK ravishda 1taga oshiradi va yangi
    qiymatni qaytaradi. Postgres/SQLite dialektiga bog'liq bo'lmagan yo'l
    (dialektga xos `ON CONFLICT DO UPDATE` o'rniga) ishlatiladi - testlar
    SQLite'da, production Postgres'da ishlaydi:

    1. Avval oddiy `UPDATE ... RETURNING` bilan urinadi (kundalik qator
       odatda ALLAQACHON mavjud - kunning birinchi so'rovidan keyin).
    2. Qator hali yo'q bo'lsa (kunning birinchi so'rovi), yangi qator
       qo'shadi.
    3. Ikkala (1)-so'rov bir vaqtda "kunning birinchi so'rovi" bo'lib
       qolsa (poyga) - ikkinchisi `IntegrityError` (primary key
       to'qnashuvi) oladi, buni tutib qayta (1)-UPDATE'ni bajaradi -
       bu safar qator allaqachon mavjud."""
    stmt = (
        sql_update(DailyAiUsage)
        .where(DailyAiUsage.usage_date == today)
        .values(gemini_call_count=DailyAiUsage.gemini_call_count + 1)
        .returning(DailyAiUsage.gemini_call_count)
    )
    result = await db.execute(stmt)
    row = result.first()
    if row is not None:
        return row[0]

    db.add(DailyAiUsage(usage_date=today, gemini_call_count=1))
    try:
        await db.flush()
        return 1
    except IntegrityError:
        await db.rollback()
        result = await db.execute(stmt)
        row = result.first()
        return row[0] if row is not None else 1


async def reserve_daily_gemini_call(db: AsyncSession) -> int:
    """Bitta Gemini chaqiruvi uchun "joy band qiladi" - bugungi
    hisoblagichni oshiradi va COMMIT qiladi. Shu bilan kunlik chegaradan
    oshib ketsa, `DailyLimitExceeded` ko'taradi - chaqiruvchi buni 503'ga
    aylantiradi, Gemini bu holatda UMUMAN chaqirilmaydi.

    Chegaradan oshgan holatda ATAYLAB `db.rollback()` chaqirilmaydi -
    sababi ikkita: (1) chaqiruvchi session'i shu bilan tugamaydi (masalan
    `app/routers/ai_quiz.py` shu xatodan keyin band qilingan Diamond'ni
    qaytaradi va COMMIT qiladi - rollback shu sessiyadagi BOSHQA ORM
    obyektlarini ham "eskirgan" deb belgilab, keyingi maydon o'qishda
    kutilmagan qo'shimcha SELECT'ga olib kelardi), (2) to'g'rilik uchun
    shart emas - hisoblagichning bu urinishi commit qilinmasa ham,
    HAR BIR keyingi urinish o'zining yangi +1'ini qo'shishda davom etadi,
    demak chegaradan oshgach barcha keyingi so'rovlar baribir bloklangan
    bo'lib qoladi."""
    today = _tashkent_today()
    count = await _increment_and_get_count(db, today)
    if count > settings.MAX_DAILY_GEMINI_CALLS:
        raise DailyLimitExceeded(f"kunlik Gemini chaqiruvlari chegarasi: {settings.MAX_DAILY_GEMINI_CALLS}")
    await db.commit()
    return count
