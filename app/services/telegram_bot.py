"""Telegram bot'ning ish mantig'i - webhook orqali kelgan `Update`larni
qayta ishlaydi. Diamond sotib olish hozircha "Free Get" - haqiqiy
to'lov (Payme/Click) ulanmagan, tugma bosilsa Diamond DARHOL, bepul
qo'shiladi - [[ai_cost_architecture]], foydalanuvchining aniq ko'rsatmasi
bilan ("shunchaki free get qilib qo'y, keyinroq Pay'ga almashtiramiz")."""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.telegram_link_code import TelegramLinkCode
from app.models.user import User
from app.services import telegram_client, wallet

logger = logging.getLogger("zukkor.telegram")

_LINK_CODE_TTL = timedelta(minutes=10)

# Narxlash/paketlar hali belgilanmagan (Payme/Click integratsiyasi bilan
# birga keladi) - bular FAQAT "Free Get" bosqichi uchun ko'rsatiladigan,
# osongina o'zgartiriladigan vaqtinchalik son(lar). Haqiqiy to'lov
# ulanganda shu ro'yxat + tugma matni ("Free Get" -> "Sotib olish")
# almashtiriladi, qolgan mantiq (callback_data, credit_diamond) o'zgarmaydi.
DIAMOND_PACKAGES = [
    {"id": "small", "diamonds": 50, "label": "50 \U0001f48e"},
    {"id": "medium", "diamonds": 150, "label": "150 \U0001f48e"},
    {"id": "large", "diamonds": 500, "label": "500 \U0001f48e"},
]


def _generate_numeric_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def _find_user_by_telegram_id(db: AsyncSession, telegram_user_id: int) -> User | None:
    result = await db.execute(select(User).where(User.telegram_user_id == telegram_user_id))
    return result.scalar_one_or_none()


async def _handle_start(db: AsyncSession, telegram_user_id: int, chat_id: int) -> None:
    """`/start` - HAR DOIM shunchaki tanishtiruv (nima uchun bot ekanini
    tushuntiradi), hech qachon o'zi kod generatsiya qilmaydi va hech
    qachon menyu ko'rsatmaydi - bular alohida buyruqlar (`/link`,
    `/diamond`). Bitta buyruq bir ishni qiladi, foydalanuvchi keyingi
    qadamni aniq bilib oladi (2026-09-06, foydalanuvchi ko'rsatmasi
    bilan qayta loyihalandi)."""
    existing_user = await _find_user_by_telegram_id(db, telegram_user_id)
    if existing_user is not None:
        await telegram_client.send_message(
            chat_id,
            "Xush kelibsiz, Zukkor botiga qaytganingizdan xursandmiz! \U0001f44b\n\n"
            "Hisobingiz allaqachon ulangan - Diamond sotib olish uchun /diamond yozing.",
        )
        return

    await telegram_client.send_message(
        chat_id,
        "Assalomu alaykum! Bu - Zukkor viktorina ilovasining rasmiy boti \U0001f44b\n\n"
        "Bu yerda Zukkor ilovasidagi hisobingizga Diamond (test yaratish uchun "
        "ishlatiladigan valyuta) sotib olishingiz mumkin bo'ladi.\n\n"
        "Boshlash uchun hisobingizni ulang: /link",
    )


async def _handle_link_command(db: AsyncSession, telegram_user_id: int, chat_id: int) -> None:
    existing_user = await _find_user_by_telegram_id(db, telegram_user_id)
    if existing_user is not None:
        await telegram_client.send_message(
            chat_id, "Hisobingiz allaqachon ulangan. Diamond sotib olish uchun /diamond yozing.",
        )
        return

    # Kod to'qnashuvi ehtimoli juda kichik (1/1_000_000), lekin baribir
    # tekshiramiz - bir necha marta urinib ko'ramiz.
    for _ in range(5):
        code = _generate_numeric_code()
        exists = (await db.execute(select(TelegramLinkCode).where(TelegramLinkCode.code == code))).scalar_one_or_none()
        if exists is None:
            break
    else:
        await telegram_client.send_message(chat_id, "Xatolik yuz berdi, birozdan keyin qayta urinib ko'ring.")
        return

    db.add(
        TelegramLinkCode(
            code=code,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=chat_id,
            expires_at=datetime.now(timezone.utc) + _LINK_CODE_TTL,
        )
    )
    await db.commit()

    await telegram_client.send_message(
        chat_id,
        "Zukkor hisobingizni ulash uchun quyidagi kodni ilovada kiriting:\n\n"
        f"<b>{code}</b>\n\n"
        "Ilova: Profil → Sozlamalar → Telegram bilan bog'lash.\n"
        "Kod 10 daqiqa amal qiladi.",
    )


async def _send_diamond_menu(chat_id: int, user: User) -> None:
    buttons = [
        [{"text": f"Bepul olish - {pkg['label']}", "callback_data": f"buy:{pkg['id']}"}] for pkg in DIAMOND_PACKAGES
    ]
    await telegram_client.send_message(
        chat_id,
        f"Joriy balansingiz: <b>{user.diamond_balance} \U0001f48e</b>\n\n"
        "Diamond sotib olish hozircha bepul sinov rejimida (to'lov tizimi "
        "tez orada qo'shiladi) - xohlagan paketni bosing:",
        reply_markup={"inline_keyboard": buttons},
    )


async def _handle_diamond_command(db: AsyncSession, telegram_user_id: int, chat_id: int) -> None:
    user = await _find_user_by_telegram_id(db, telegram_user_id)
    if user is None:
        await telegram_client.send_message(
            chat_id,
            "Hisobingiz hali ulanmagan. Ulash uchun /link yozing.",
        )
        return
    await _send_diamond_menu(chat_id, user)


async def _handle_buy_callback(
    db: AsyncSession, telegram_user_id: int, chat_id: int, callback_query_id: str, package_id: str
) -> None:
    package = next((p for p in DIAMOND_PACKAGES if p["id"] == package_id), None)
    if package is None:
        await telegram_client.answer_callback_query(callback_query_id, "Noto'g'ri paket")
        return

    user = await _find_user_by_telegram_id(db, telegram_user_id)
    if user is None:
        await telegram_client.answer_callback_query(callback_query_id, "Avval /link orqali hisobni ulang")
        return

    await wallet.credit_diamond(
        db,
        user,
        package["diamonds"],
        "purchase",
        extra={"package_id": package_id, "channel": "telegram_bot", "payment": "free_get_placeholder"},
    )
    await db.commit()

    await telegram_client.answer_callback_query(callback_query_id, "Qo'shildi!")
    await telegram_client.send_message(
        chat_id,
        f"✅ {package['diamonds']} \U0001f48e qo'shildi!\nJoriy balans: {user.diamond_balance} \U0001f48e",
    )


async def handle_update(db: AsyncSession, update: dict) -> None:
    """`POST /telegram/webhook`dan chaqiriladi - Telegram'ning `Update`
    obyektini (https://core.telegram.org/bots/api#update) qayta ishlaydi.
    Har doim xatosiz qaytishi kerak (Telegram xato javob qaytarsa webhook'ni
    qayta-qayta urinib turadi) - shuning uchun ichkarida keng `except`."""
    try:
        message = update.get("message")
        callback_query = update.get("callback_query")

        if message is not None:
            text = (message.get("text") or "").strip()
            from_user = message.get("from") or {}
            chat = message.get("chat") or {}
            telegram_user_id = from_user.get("id")
            chat_id = chat.get("id")
            if telegram_user_id is None or chat_id is None:
                return

            if text.startswith("/start"):
                await _handle_start(db, telegram_user_id, chat_id)
            elif text.startswith("/link"):
                await _handle_link_command(db, telegram_user_id, chat_id)
            elif text.startswith("/diamond"):
                await _handle_diamond_command(db, telegram_user_id, chat_id)
            else:
                await telegram_client.send_message(
                    chat_id, "Hisobni ulash uchun /link, Diamond sotib olish uchun /diamond yozing."
                )

        elif callback_query is not None:
            from_user = callback_query.get("from") or {}
            message = callback_query.get("message") or {}
            chat = message.get("chat") or {}
            telegram_user_id = from_user.get("id")
            chat_id = chat.get("id")
            callback_query_id = callback_query.get("id")
            data = callback_query.get("data") or ""
            if telegram_user_id is None or chat_id is None or callback_query_id is None:
                return

            if data.startswith("buy:"):
                await _handle_buy_callback(db, telegram_user_id, chat_id, callback_query_id, data.removeprefix("buy:"))
            else:
                await telegram_client.answer_callback_query(callback_query_id)
    except Exception:
        logger.exception("Telegram update'ni qayta ishlashda kutilmagan xatolik")
