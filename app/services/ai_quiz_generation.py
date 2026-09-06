"""Foydalanuvchi yuklagan hujjat matnidan yoki mavzudan Gemini Interactions
API orqali quiz savollari generatsiya qilish."""

import json
import logging
from dataclasses import dataclass

from app.services.gemini_client import GeminiCallError, call_gemini

logger = logging.getLogger("zukkor.ai_quiz")

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


@dataclass(frozen=True)
class GeneratedQuiz:
    """`generate_questions`/`generate_questions_from_topic` natijasi -
    savollar RO'YXATI HAMDA haqiqiy token sarfi (chaqiruvchi - `ai_quiz.py`
    routeri - shu ikkalasidan Diamond narxini hisoblab, generatsiya
    TUGAGANDAN keyin yechadi - [[ai_cost_architecture]])."""

    questions: list[dict]
    input_tokens: int
    output_tokens: int


async def _call_gemini(prompt: str, *, use_search: bool):
    # Past-darajali so'rov/retry/xato-qayta-ishlash mantig'i umumiy
    # app.services.gemini_client'da yashaydi (savol-moderatsiya kabi boshqa
    # AI-xususiyatlar bilan baham ko'riladi) - bu yerda faqat shu modulga
    # xos QuizGenerationError'ga o'rab qaytariladi.
    try:
        return await call_gemini(prompt, response_schema=_RESPONSE_SCHEMA, use_search=use_search)
    except GeminiCallError as exc:
        raise QuizGenerationError(str(exc)) from exc


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


async def generate_questions(text: str, instruction: str, question_count: int) -> GeneratedQuiz:
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
    result = await _call_gemini(prompt, use_search=False)
    questions = _parse_and_validate(result.text)
    return GeneratedQuiz(questions=questions, input_tokens=result.input_tokens, output_tokens=result.output_tokens)


async def generate_questions_from_topic(topic: str, instruction: str, question_count: int) -> GeneratedQuiz:
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
    result = await _call_gemini(prompt, use_search=False)
    questions = _parse_and_validate(result.text)
    return GeneratedQuiz(questions=questions, input_tokens=result.input_tokens, output_tokens=result.output_tokens)


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
