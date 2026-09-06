"""Telegram Bot API bilan ishlash uchun past-darajali klient - og'ir SDK
(masalan aiogram) o'rniga to'g'ridan-to'g'ri HTTP so'rovlar, xuddi
`gemini_client.py`dagi kabi (bu loyihada tashqi AI/bot xizmatlariga
shunday yondashish qabul qilingan - kichik, sinov qilinadigan yuza)."""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("zukkor.telegram")

_API_BASE = "https://api.telegram.org/bot{token}/{method}"


async def _call(method: str, payload: dict) -> None:
    if not settings.TELEGRAM_BOT_TOKEN:
        return
    url = _API_BASE.format(token=settings.TELEGRAM_BOT_TOKEN, method=method)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=payload)
        if response.status_code >= 300:
            logger.error("Telegram API xato qaytardi (%s): %s %s", method, response.status_code, response.text)
    except httpx.HTTPError:
        logger.exception("Telegram API'ga ulanishda xatolik (%s)", method)


async def send_message(chat_id: int, text: str, *, reply_markup: dict | None = None) -> None:
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    await _call("sendMessage", payload)


async def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    """Tugma bosilganda Telegram mijozidagi "yuklanmoqda" aylanishini
    to'xtatadi - bosilgan tugma tashqarida hech qanday effekt bo'lmasa
    ham, HAR DOIM chaqirilishi kerak (aks holda foydalanuvchi tugma
    "osilib qoldi" deb o'ylashi mumkin)."""
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    await _call("answerCallbackQuery", payload)
