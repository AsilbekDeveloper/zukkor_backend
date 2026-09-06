"""Gemini Interactions API bilan ishlash uchun umumiy past-darajali klient.

Retry/backoff, xato-xabar chiqarish va model javobini matn sifatida ajratib
olish mantig'i - bir nechta AI-xususiyat (quiz generatsiya, savol
moderatsiyasi va h.k.) o'rtasida takrorlanmasin deb shu yerga chiqarilgan.
Har bir chaqiruvchi o'z JSON-schema'sini beradi va `GeminiCallError`ni
o'z domenidagi xatoga (masalan `QuizGenerationError`) o'rab qaytaradi."""

import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger("zukkor.ai_quiz")

# 2026-08-12: eski `generateContent` endpoint (gemini-2.0-flash, keyin
# gemini-2.5-flash) yangi API kalitlar/loyihalar uchun butunlay yopilgan
# ("no longer available to new users") - Google buni yangi Interactions
# API'ga almashtirgan. gemini-3.6-flash - hozirgi barqaror (GA) va yangi
# foydalanuvchilarga ochiq model.
_GEMINI_MODEL = "gemini-3.6-flash"
_INTERACTIONS_API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

# Gemini vaqti-vaqti bilan vaqtinchalik xato qaytaradi (tarmoq uzilishi,
# 429 kvota-limit, yoki 5xx server xatosi) - bunday hollarda darhol
# chaqiruvchiga xato ko'rsatish o'rniga bir necha marta eksponensial
# kutish bilan qayta urinamiz. 400/401/403/404 kabi mijoz xatolari esa
# qayta urinishda ham o'zgarmaydi, shuning uchun ular darhol ko'tariladi.
_MAX_ATTEMPTS = 4  # dastlabki urinish + 3 ta qayta urinish
_RETRY_BASE_DELAY_SECONDS = 1.5
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class GeminiCallError(Exception):
    """Gemini'ga so'rov muvaffaqiyatsiz tugadi (sozlanmagan, tarmoq xatosi,
    yoki javobni o'qib bo'lmadi). Chaqiruvchi buni o'z domenidagi xatoga
    (masalan `QuizGenerationError`) o'rab qaytarishi kerak - shu modul
    qaysi xususiyat chaqirayotganini bilmaydi."""


@dataclass(frozen=True)
class GeminiResponse:
    """`call_gemini`ning to'liq natijasi - matn (schema'ga mos JSON string)
    va Diamond narxlashda ishlatiladigan haqiqiy token sarfi (`usage`,
    Interactions API javobida `steps`ga qo'shni, top-level maydon -
    https://ai.google.dev/api/interactions-api)."""

    text: str
    input_tokens: int
    output_tokens: int


def _extract_gemini_error_message(response: httpx.Response) -> str | None:
    # Gemini xato javobi odatda {"error": {"code":..., "message":..., "status":...}}
    # shaklida keladi - buni foydalanuvchiga ko'rsatilsa, muammoni tezroq
    # aniqlash mumkin (masalan "model not found" yoki kvota tugashi).
    try:
        message = response.json()["error"]["message"]
    except (KeyError, ValueError, TypeError):
        return None
    return str(message)[:300] if message else None


async def call_gemini(prompt: str, *, response_schema: dict, use_search: bool = False) -> GeminiResponse:
    """Gemini Interactions API'ga structured-JSON so'rov yuboradi va
    modelning matn chiqishini (odatda JSON string, `response_schema`ga mos)
    HAMDA haqiqiy token sarfini (`GeminiResponse.input_tokens`/
    `output_tokens` - Diamond narxlash uchun) qaytaradi. Vaqtinchalik
    xatolarda (tarmoq, 429/5xx) eksponensial kutish bilan qayta urinadi."""
    if not settings.GEMINI_API_KEY:
        raise GeminiCallError("AI xizmati hozircha sozlanmagan")

    payload: dict = {
        "model": _GEMINI_MODEL,
        "input": prompt,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": response_schema,
        },
    }
    # Interactions API'da (eski generateContent'dan farqli) qidiruv
    # grounding'i va structured JSON chiqishini bitta so'rovda birga
    # ishlatish mumkin.
    if use_search:
        payload["tools"] = [{"type": "google_search"}]

    response: httpx.Response | None = None
    for attempt in range(_MAX_ATTEMPTS):
        is_last_attempt = attempt == _MAX_ATTEMPTS - 1
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    _INTERACTIONS_API_URL,
                    headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            if is_last_attempt:
                logger.exception("Gemini'ga so'rov yuborishda xatolik")
                raise GeminiCallError("AI xizmatiga ulanib bo'lmadi") from exc
            logger.warning(
                "Gemini'ga ulanishda xatolik (urinish %s/%s), qayta urinilmoqda: %s",
                attempt + 1, _MAX_ATTEMPTS, exc,
            )
            await asyncio.sleep(_RETRY_BASE_DELAY_SECONDS * (2**attempt))
            continue

        if response.status_code in _RETRYABLE_STATUS_CODES and not is_last_attempt:
            logger.warning(
                "Gemini vaqtinchalik xato qaytardi (%s, urinish %s/%s), qayta urinilmoqda",
                response.status_code, attempt + 1, _MAX_ATTEMPTS,
            )
            await asyncio.sleep(_RETRY_BASE_DELAY_SECONDS * (2**attempt))
            continue

        break

    if response.status_code >= 300:
        logger.error("Gemini xato qaytardi: %s %s", response.status_code, response.text)
        detail = _extract_gemini_error_message(response)
        raise GeminiCallError(f"AI so'rovni bajarolmadi ({detail})" if detail else "AI so'rovni bajarolmadi")

    try:
        data = response.json()
        text = next(
            content["text"]
            for step in data["steps"]
            if step.get("type") == "model_output"
            for content in step["content"]
            if content.get("type") == "text"
        )
    except (KeyError, StopIteration, ValueError) as exc:
        logger.exception("Gemini javobini o'qib bo'lmadi")
        raise GeminiCallError("AI javobini qayta ishlab bo'lmadi") from exc

    # `usage` yo'q/noto'liq bo'lsa ham (masalan kelajakda API javobi
    # o'zgarsa) butun so'rov muvaffaqiyatsiz bo'lib qolmasin - 0 bilan
    # davom etamiz (Diamond narxlash bu holda kamida 1ga tushadi, real
    # xarajatni "yo'qotib qo'yish"dan ko'ra unchalik katta emas).
    usage = data.get("usage") or {}
    input_tokens = int(usage.get("total_input_tokens") or 0)
    output_tokens = int(usage.get("total_output_tokens") or 0)

    return GeminiResponse(text=text, input_tokens=input_tokens, output_tokens=output_tokens)
