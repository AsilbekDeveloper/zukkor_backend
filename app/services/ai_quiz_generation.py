"""Foydalanuvchi yuklagan hujjat matnidan Gemini orqali quiz savollari
generatsiya qilish."""

import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("zukkor.ai_quiz")

# 2026-08-12: gemini-2.0-flash Google tomonidan 2026-06-01'da to'xtatilgan
# (retired) - production'da 502 xatosiga sabab bo'lgan. gemini-2.5-flash'ga
# o'tkazildi - GA'dan beri (2025-06) barqaror, hali deprecation e'lon
# qilinmagan.
_GEMINI_MODEL = "gemini-2.5-flash"
_GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{_GEMINI_MODEL}:generateContent"

# Juda uzun hujjat (masalan butun kitob) uchun ham xarajat/vaqtni chegaralash -
# bu miqdor odatiy kitoblarning katta qismini qamrab oladi.
_MAX_SOURCE_TEXT_CHARS = 200_000

_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "question_text": {"type": "STRING"},
            "options": {"type": "ARRAY", "items": {"type": "STRING"}},
            "correct_option_index": {"type": "INTEGER"},
        },
        "required": ["question_text", "options", "correct_option_index"],
    },
}


class QuizGenerationError(Exception):
    """AI orqali quiz generatsiya qilib bo'lmadi (sozlanmagan, tarmoq xatosi, yoki natija yaroqsiz)."""


def _extract_gemini_error_message(response: httpx.Response) -> str | None:
    # Gemini xato javobi odatda {"error": {"code":..., "message":..., "status":...}}
    # shaklida keladi - buni foydalanuvchiga ko'rsatilsa, muammoni tezroq
    # aniqlash mumkin (masalan "model not found" yoki kvota tugashi).
    try:
        message = response.json()["error"]["message"]
    except (KeyError, ValueError, TypeError):
        return None
    return str(message)[:300] if message else None


async def research_topic(topic: str) -> str:
    """Mavzu bo'yicha Google qidiruvi orqali (grounding) faktik matn yig'ib beradi.

    Gemini API'da qidiruv grounding'i bilan structured JSON chiqishini (response_schema)
    bir so'rovda birga ishlatib bo'lmaydi, shuning uchun bu alohida, oddiy matn
    qaytaradigan bosqich - natijasi keyin generate_questions()'ga hujjat matni
    o'rnida beriladi.
    """
    if not settings.GEMINI_API_KEY:
        raise QuizGenerationError("AI xizmati hozircha sozlanmagan")

    prompt = (
        "Internetdan qidirib, quyidagi mavzu bo'yicha test (viktorina) savollari "
        "tuzish uchun yetarlicha bo'lgan aniq, faktik ma'lumot to'plang va batafsil "
        "matn shaklida yozing (ro'yxat, sana, raqam va faktlarni saqlab qoling). "
        "Mavzu qaysi tilda yozilgan bo'lsa, javobni o'sha tilda yozing.\n\n"
        f"Mavzu: {topic}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                _GEMINI_API_URL,
                params={"key": settings.GEMINI_API_KEY},
                json=payload,
            )
    except httpx.HTTPError as exc:
        logger.exception("Gemini'ga (qidiruv) so'rov yuborishda xatolik")
        raise QuizGenerationError("AI xizmatiga ulanib bo'lmadi") from exc

    if response.status_code >= 300:
        logger.error("Gemini (qidiruv) xato qaytardi: %s %s", response.status_code, response.text)
        detail = _extract_gemini_error_message(response)
        raise QuizGenerationError(
            f"Mavzu bo'yicha ma'lumot topib bo'lmadi ({detail})" if detail else "Mavzu bo'yicha ma'lumot topib bo'lmadi"
        )

    try:
        data = response.json()
        researched_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, ValueError) as exc:
        logger.exception("Gemini (qidiruv) javobini o'qib bo'lmadi")
        raise QuizGenerationError("AI javobini qayta ishlab bo'lmadi") from exc

    if not researched_text.strip():
        raise QuizGenerationError("Mavzu bo'yicha ma'lumot topib bo'lmadi")
    return researched_text


async def generate_questions(text: str, instruction: str, question_count: int) -> list[dict]:
    if not settings.GEMINI_API_KEY:
        raise QuizGenerationError("AI xizmati hozircha sozlanmagan")

    truncated = text[:_MAX_SOURCE_TEXT_CHARS]
    instruction_line = f"Foydalanuvchi ko'rsatmasi: {instruction}\n" if instruction.strip() else ""
    prompt = (
        "Siz aqlli o'quv yordamchisiz. Quyidagi hujjat matni asosida test (viktorina) "
        "savollari tayyorlang.\n\n"
        f"{instruction_line}"
        f"Savollar soni: aynan {question_count} ta.\n\n"
        "Har bir savol uchun aynan 4 ta javob varianti bering, ulardan faqat bittasi "
        "to'g'ri bo'lsin. Savol va variantlarni hujjat matni qaysi tilda bo'lsa, o'sha "
        "tilda yozing. Foydalanuvchi ko'rsatmasida aytilmagan mavzulardan savol "
        "tuzmang.\n\n"
        f"Hujjat matni:\n{truncated}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": _RESPONSE_SCHEMA,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                _GEMINI_API_URL,
                params={"key": settings.GEMINI_API_KEY},
                json=payload,
            )
    except httpx.HTTPError as exc:
        logger.exception("Gemini'ga so'rov yuborishda xatolik")
        raise QuizGenerationError("AI xizmatiga ulanib bo'lmadi") from exc

    if response.status_code >= 300:
        logger.error("Gemini xato qaytardi: %s %s", response.status_code, response.text)
        detail = _extract_gemini_error_message(response)
        raise QuizGenerationError(f"AI savollarni tayyorlay olmadi ({detail})" if detail else "AI savollarni tayyorlay olmadi")

    try:
        data = response.json()
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        raw_questions = json.loads(raw_text)
    except (KeyError, IndexError, ValueError) as exc:
        logger.exception("Gemini javobini o'qib bo'lmadi")
        raise QuizGenerationError("AI javobini qayta ishlab bo'lmadi") from exc

    validated = _validate_questions(raw_questions)
    if not validated:
        raise QuizGenerationError(
            "AI yaroqli savollar tayyorlay olmadi - boshqa hujjat yoki ko'rsatma bilan urinib ko'ring"
        )
    return validated


def _validate_questions(raw_questions) -> list[dict]:
    # Gemini javobiga hech qachon ko'r-ko'rona ishonmaymiz - har bir savol
    # aynan kutilgan shaklda ekanligi bazaga yozishdan oldin tekshiriladi.
    if not isinstance(raw_questions, list):
        return []

    validated: list[dict] = []
    for item in raw_questions:
        if not isinstance(item, dict):
            continue
        question_text = item.get("question_text")
        options = item.get("options")
        correct_index = item.get("correct_option_index")
        if (
            not isinstance(question_text, str)
            or not question_text.strip()
            or not isinstance(options, list)
            or len(options) != 4
            or not all(isinstance(option, str) and option.strip() for option in options)
            or not isinstance(correct_index, int)
            or not (0 <= correct_index < 4)
        ):
            continue
        validated.append(
            {
                "question_text": question_text.strip(),
                "options": [option.strip() for option in options],
                "correct_option_index": correct_index,
            }
        )
    return validated
