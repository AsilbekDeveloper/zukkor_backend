"""Foydalanuvchi yuklagan hujjat matnidan Gemini orqali quiz savollari
generatsiya qilish."""

import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("zukkor.ai_quiz")

_GEMINI_MODEL = "gemini-2.0-flash"
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


async def generate_questions(text: str, instruction: str, question_count: int) -> list[dict]:
    if not settings.GEMINI_API_KEY:
        raise QuizGenerationError("AI xizmati hozircha sozlanmagan")

    truncated = text[:_MAX_SOURCE_TEXT_CHARS]
    prompt = (
        "Siz aqlli o'quv yordamchisiz. Quyidagi hujjat matni asosida test (viktorina) "
        "savollari tayyorlang.\n\n"
        f"Foydalanuvchi ko'rsatmasi: {instruction}\n"
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
        raise QuizGenerationError("AI savollarni tayyorlay olmadi")

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
