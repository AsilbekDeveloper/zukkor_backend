from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.telegram_link_code import TelegramLinkCode
from app.models.user import User
from app.schemas.telegram import TelegramLinkOut, TelegramLinkRequest
from app.services import telegram_bot

router = APIRouter()


@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Telegram bot webhook",
    description="Telegram Bot API'dan `Update` obyektlarini qabul qiladi - "
    "autentifikatsiyasiz (Telegram'ning o'zi chaqiradi). Har doim 200 "
    "qaytaradi (ichkarida xato bo'lsa ham) - aks holda Telegram webhook'ni "
    "qayta-qayta urinib, keraksiz yukni oshiradi.",
)
async def telegram_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    update = await request.json()
    await telegram_bot.handle_update(db, update)
    return {"ok": True}


@router.post(
    "/link",
    response_model=TelegramLinkOut,
    summary="Telegram hisobini ulash",
    description="Bot bergan 6 xonali kodni tasdiqlab, joriy Zukkor "
    "hisobini o'sha Telegram hisobiga bog'laydi.",
)
async def link_telegram_account(
    data: TelegramLinkRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(TelegramLinkCode).where(TelegramLinkCode.code == data.code))
    link_code = result.scalar_one_or_none()

    if link_code is None or link_code.is_used:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kod yaroqsiz yoki muddati o'tgan")

    # SQLite (testlarda) `DateTime(timezone=True)` ustunini tzinfo'siz
    # qaytarishi mumkin - solishtirishdan oldin UTC deb belgilaymiz, aks
    # holda aware/naive solishtirish `TypeError` beradi (avval shu klass
    # xatolik boshqa joyda ham uchragan - `app/services/streak.py`).
    expires_at = link_code.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kod yaroqsiz yoki muddati o'tgan")

    # Bu Telegram hisobi ilgari boshqa Zukkor akkauntiga ulangan bo'lishi
    # mumkin (masalan foydalanuvchi xato akkauntga ulagan, yoki hisobini
    # almashtirmoqchi) - kodni faqat o'sha Telegram chatida ko'rgan kishi
    # bilishi mumkinligi allaqachon egalikni tasdiqlagani uchun, eski
    # bog'lanish shunchaki uzib, yangisiga qayta bog'laymiz.
    old_owner_result = await db.execute(select(User).where(User.telegram_user_id == link_code.telegram_user_id))
    old_owner = old_owner_result.scalar_one_or_none()
    if old_owner is not None and old_owner.id != current_user.id:
        old_owner.telegram_user_id = None
        # SQLAlchemy ikkala UPDATE'ni (eskisini bo'shatish + yangisiga
        # tayinlash) bitta `executemany` partiyasida birlashtirib yuborishi
        # mumkin - shu partiya ichida qatorlar tartibi kafolatlanmagani
        # uchun ba'zan avval YANGI qator yozilib, ESKISI hali 999'ni
        # ushlab turgan holatda UNIQUE cheklovga tegib qoladi. Shu yerda
        # aniq `flush()` bilan bo'shatishni DARHOL yozib qo'yish (keyingi
        # tayinlashdan OLDIN) bu poyga holatini yo'q qiladi.
        await db.flush()

    current_user.telegram_user_id = link_code.telegram_user_id
    link_code.is_used = True
    await db.commit()

    return TelegramLinkOut(diamond_balance=current_user.diamond_balance, coin_balance=current_user.coin_balance)
