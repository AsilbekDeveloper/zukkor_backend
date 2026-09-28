"""Telegram bot'ning ish mantig'i - webhook orqali kelgan `Update`larni
qayta ishlaydi. Diamond sotib olish hozircha "Free Get" - haqiqiy to'lov
(Payme/Click) ulanmagan, [[ai_cost_architecture]].

2026-09-18, biznes qaror o'zgardi: MVP bosqichida foydalanuvchilar ilovani
(marketing uchun) bemalol sinab ko'ra olishi kerak - shuning uchun "Free
Get" BUTUNLAY o'chirilmaydi, lekin xarajatni jilovlash uchun qat'iy
kunlik limit bilan: foydalanuvchi kuniga FAQAT BIR MARTA, FIKSIRLANGAN
miqdorda (`economy_config.FREE_GET_DIAMOND_AMOUNT`, taxminan 5 ta AI-test
generatsiyasiga yetarli) bepul Diamond olishi mumkin - eski "har bir
paket alohida, cheklovsiz bosiladigan" tugmalar o'rniga BITTA "Bugungi
bepul Diamond" tugmasi. Eski `buy:*` callback'lari (foydalanuvchi
chatida ESKI xabar saqlanib qolgan bo'lishi mumkin) endi hech narsa
kredit qilmaydi, faqat yangi buyruqqa yo'naltiradi."""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.telegram_link_code import TelegramLinkCode
from app.models.user import User
from app.services import economy_config, telegram_client, wallet
from app.services.streak import TASHKENT_OFFSET

logger = logging.getLogger("zukkor.telegram")

_LINK_CODE_TTL = timedelta(minutes=10)

# Kelajakdagi (Payme/Click ulangandan keyingi) haqiqiy narxlash paketlari -
# hozircha faqat "tez orada" sifatida ko'rsatiladi, hech biri hozir sotib
# olinmaydi/bepul berilmaydi (buni "Bugungi bepul Diamond" tugmasi
# almashtiradi, pastga qarang).
DIAMOND_PACKAGES = [
    {"id": "small", "diamonds": 50, "label": "50 \U0001f48e"},
    {"id": "medium", "diamonds": 150, "label": "150 \U0001f48e"},
    {"id": "large", "diamonds": 500, "label": "500 \U0001f48e"},
]

_DAILY_FREE_GET_CALLBACK_DATA = "daily_free_get"


def _generate_numeric_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _tashkent_date(dt: datetime):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + TASHKENT_OFFSET).date()


def _already_claimed_free_diamond_today(user: User) -> bool:
    if user.last_free_diamond_at is None:
        return False
    return _tashkent_date(user.last_free_diamond_at) == _tashkent_date(datetime.now(timezone.utc))


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


async def _send_diamond_menu(db: AsyncSession, chat_id: int, user: User) -> None:
    free_amount = await economy_config.get_int(db, economy_config.FREE_GET_DIAMOND_AMOUNT)
    package_lines = "\n".join(f"• {pkg['label']} — tez orada" for pkg in DIAMOND_PACKAGES)
    await telegram_client.send_message(
        chat_id,
        f"Joriy balansingiz: <b>{user.diamond_balance} \U0001f48e</b>\n\n"
        f"MVP bosqichida kuniga bir marta <b>BEPUL {free_amount} \U0001f48e</b> olishingiz "
        "mumkin (taxminan 5 ta AI-test generatsiyasiga yetarli).\n\n"
        f"<b>Diqqat:</b> test bosqichida balansingizda BIR VAQTNING O'ZIDA maksimum "
        f"<b>{wallet.DIAMOND_BALANCE_CAP} \U0001f48e</b> turishi mumkin - kunlik "
        f"{free_amount}ni ishlatmasangiz ham, ertaga yana bosganingizda balansingiz "
        f"{wallet.DIAMOND_BALANCE_CAP}dan OSHMAYDI (kunlar bo'yicha yig'ilib bormaydi).\n\n"
        f"Pullik paketlar (tez orada):\n{package_lines}",
        reply_markup={
            "inline_keyboard": [
                [{"text": f"Bugungi bepul {free_amount} \U0001f48e ni olish", "callback_data": _DAILY_FREE_GET_CALLBACK_DATA}]
            ]
        },
    )


async def _handle_diamond_command(db: AsyncSession, telegram_user_id: int, chat_id: int) -> None:
    user = await _find_user_by_telegram_id(db, telegram_user_id)
    if user is None:
        await telegram_client.send_message(
            chat_id,
            "Hisobingiz hali ulanmagan. Ulash uchun /link yozing.",
        )
        return
    await _send_diamond_menu(db, chat_id, user)


async def _handle_daily_free_get_callback(
    db: AsyncSession, telegram_user_id: int, chat_id: int, callback_query_id: str
) -> None:
    user = await _find_user_by_telegram_id(db, telegram_user_id)
    if user is None:
        await telegram_client.answer_callback_query(callback_query_id, "Avval /link orqali hisobni ulang")
        return

    if _already_claimed_free_diamond_today(user):
        await telegram_client.answer_callback_query(callback_query_id, "Bugungi limit tugadi")
        await telegram_client.send_message(chat_id, "Bugungi bepul Diamondlarni olib bo'ldingiz. Ertaga yana kiring!")
        return

    amount = await economy_config.get_int(db, economy_config.FREE_GET_DIAMOND_AMOUNT)
    user.last_free_diamond_at = datetime.now(timezone.utc)
    # `credit_diamond` `DIAMOND_BALANCE_CAP` chegarasini qo'llaydi - agar
    # balans allaqachon chegaraga yaqin/teng bo'lsa, HAQIQIY qo'shilgan
    # miqdor so'ralgan `amount`dan KAM (hatto 0) bo'lishi mumkin. Foydalanuvchiga
    # aynan shu HAQIQIY miqdorni ko'rsatamiz - "200 qo'shildi" deb yozib,
    # balans aslida kamaygan/o'zgarmagan holatlarda chalkashtirmaslik uchun
    # (2026-09-28, foydalanuvchi topilmasi).
    credited = await wallet.credit_diamond(
        db, user, amount, "daily_free_get", extra={"channel": "telegram_bot"},
    )
    await db.commit()

    await telegram_client.answer_callback_query(callback_query_id, "Qo'shildi!")
    if credited > 0:
        confirmation = f"✅ {credited} \U0001f48e qo'shildi!"
    else:
        confirmation = "Balansingiz allaqachon maksimal chegarada edi, yangi Diamond qo'shilmadi."
    await telegram_client.send_message(
        chat_id,
        f"{confirmation}\nJoriy balans: {user.diamond_balance} \U0001f48e "
        f"(test bosqichida maksimum {wallet.DIAMOND_BALANCE_CAP} \U0001f48e).",
    )


async def _handle_stale_buy_callback(chat_id: int, callback_query_id: str) -> None:
    """Eski (2026-09-18'dan oldingi) "Bepul olish - X" tugmalari
    foydalanuvchi chatida ESKI xabar sifatida saqlanib qolgan bo'lishi
    mumkin - Telegram eski xabarlardagi tugmalarni avtomatik
    o'chirmaydi. Bunday tugma bosilsa endi HECH NARSA kredit qilmaydi -
    faqat yangi (kunlik limitli) oqimga yo'naltiradi."""
    await telegram_client.answer_callback_query(callback_query_id, "Bu tugma endi faol emas")
    await telegram_client.send_message(chat_id, "Bu tugma endi eskirgan - bepul Diamond olish uchun /diamond yozing.")


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

            if data == _DAILY_FREE_GET_CALLBACK_DATA:
                await _handle_daily_free_get_callback(db, telegram_user_id, chat_id, callback_query_id)
            elif data.startswith("buy:"):
                await _handle_stale_buy_callback(chat_id, callback_query_id)
            else:
                await telegram_client.answer_callback_query(callback_query_id)
    except Exception:
        logger.exception("Telegram update'ni qayta ishlashda kutilmagan xatolik")
