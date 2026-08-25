"""Foydalanuvchi yuklagan hujjat matnidan yoki mavzudan Gemini Interactions
API orqali quiz savollari generatsiya qilish."""

import json
import logging

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

# Juda uzun hujjat (masalan butun kitob) uchun ham xarajat/vaqtni chegaralash -
# bu miqdor odatiy kitoblarning katta qismini qamrab oladi.
_MAX_SOURCE_TEXT_CHARS = 200_000

# Interactions API standart JSON Schema (kichik harfli type nomlari)
# ishlatadi - eski generateContent'ning ARRAY/OBJECT/STRING kabi
# Gemini-ga xos katta harfli shaklidan farqli.
_RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "question_text": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
            "correct_option_index": {"type": "integer"},
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


async def _call_gemini(prompt: str, *, use_search: bool) -> str:
    if not settings.GEMINI_API_KEY:
        raise QuizGenerationError("AI xizmati hozircha sozlanmagan")

    payload: dict = {
        "model": _GEMINI_MODEL,
        "input": prompt,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": _RESPONSE_SCHEMA,
        },
    }
    # Interactions API'da (eski generateContent'dan farqli) qidiruv
    # grounding'i va structured JSON chiqishini bitta so'rovda birga
    # ishlatish mumkin.
    if use_search:
        payload["tools"] = [{"type": "google_search"}]

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                _INTERACTIONS_API_URL,
                headers={"x-goog-api-key": settings.GEMINI_API_KEY},
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
        raw_text = next(
            content["text"]
            for step in data["steps"]
            if step.get("type") == "model_output"
            for content in step["content"]
            if content.get("type") == "text"
        )
    except (KeyError, StopIteration, ValueError) as exc:
        logger.exception("Gemini javobini o'qib bo'lmadi")
        raise QuizGenerationError("AI javobini qayta ishlab bo'lmadi") from exc

    return raw_text


def _parse_and_validate(raw_text: str) -> list[dict]:
    try:
        raw_questions = json.loads(raw_text)
    except ValueError as exc:
        logger.exception("Gemini javobini o'qib bo'lmadi")
        raise QuizGenerationError("AI javobini qayta ishlab bo'lmadi") from exc

    validated = _validate_questions(raw_questions)
    if not validated:
        raise QuizGenerationError(
            "AI yaroqli savollar tayyorlay olmadi - boshqa hujjat yoki ko'rsatma bilan urinib ko'ring"
        )
    return validated


async def generate_questions(text: str, instruction: str, question_count: int) -> list[dict]:
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
    raw_text = await _call_gemini(prompt, use_search=False)
    return _parse_and_validate(raw_text)


async def generate_questions_from_topic(topic: str, instruction: str, question_count: int) -> list[dict]:
    """Mavzu bo'yicha - hech qanday hujjatsiz - AI'ning o'z bilimidan
    foydalanib savollar tayyorlaydi. `instruction` ixtiyoriy qo'shimcha
    yo'nalish beradi (masalan qiyinchilik darajasi, e'tibor qaratiladigan
    qism) - `topic`dan alohida, chunki `topic` endi quiz nomi sifatida ham
    ishlatiladi va sof mavzu bo'lishi kerak.

    Google qidiruvi (grounding) ATAYLAB ishlatilmaydi - u Gemini'da alohida
    "prepay" balans (haqiqiy pul, Cloud billing'dan mustaqil) talab qiladi.
    Grounding'siz chaqiruv esa odatda bepul kvota doirasida qoladi. Agar
    kelajakda grounding kerak bo'lsa, `_call_gemini(prompt, use_search=True)`
    ga qaytarish yetarli - qolgan struktura (schema, validatsiya) o'zgarmaydi.
    """
    instruction_line = f"Qo'shimcha ko'rsatma: {instruction}\n" if instruction.strip() else ""
    prompt = (
        "Siz aqlli o'quv yordamchisiz. Quyidagi mavzu bo'yicha aniq, "
        "faktik ma'lumotlarga asoslangan test (viktorina) savollari "
        "tayyorlang.\n\n"
        f"Mavzu: {topic}\n"
        f"{instruction_line}"
        f"Savollar soni: aynan {question_count} ta.\n\n"
        "Har bir savol uchun aynan 4 ta javob varianti bering, ulardan faqat bittasi "
        "to'g'ri bo'lsin. Savol va variantlarni mavzu qaysi tilda yozilgan bo'lsa, "
        "o'sha tilda yozing."
    )
    raw_text = await _call_gemini(prompt, use_search=False)
    return _parse_and_validate(raw_text)


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
