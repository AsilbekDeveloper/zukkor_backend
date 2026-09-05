"""Foydalanuvchi tizimga (ochiq, global kategoriyalarga) yubormoqchi bo'lgan
yangi savolni Gemini orqali tekshirish - mazmuni/to'g'riligini/mosligini
baholaydi va eng mos faol kategoriyani tanlaydi (yoki foydalanuvchi
tanlovini tasdiqlaydi/rad etadi)."""

import json
import logging

from app.services.gemini_client import GeminiCallError, call_gemini

logger = logging.getLogger("zukkor.ai_quiz")

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_approved": {"type": "boolean"},
        "rejection_reason": {"type": "string"},
        "category_id": {"type": "integer"},
    },
    "required": ["is_approved", "rejection_reason", "category_id"],
}


class QuestionModerationError(Exception):
    """Savolni AI orqali tekshirib bo'lmadi (AI xizmati ishlamayapti yoki
    javobni qayta ishlab bo'lmadi) - bu foydalanuvchining savoli rad
    etilishidan farqli, sof texnik xato (retry qilingandan keyin ham
    Gemini javob bermadi)."""


class ModerationResult:
    __slots__ = ("is_approved", "rejection_reason", "category_id")

    def __init__(self, is_approved: bool, rejection_reason: str | None, category_id: int | None):
        self.is_approved = is_approved
        self.rejection_reason = rejection_reason
        self.category_id = category_id


async def moderate_question(
    question_text: str,
    options: list[str],
    correct_option_index: int,
    requested_category_id: int | None,
    categories: list[tuple[int, str]],
) -> ModerationResult:
    """`categories` - (id, name) juftliklari ro'yxati, FAQAT faol, ochiq
    (global) kategoriyalar bo'lishi kerak - AI faqat shu ro'yxatdan
    tanlaydi (chaqiruvchi tomonidan tayyorlanadi)."""
    correct_option_text = options[correct_option_index]
    options_lines = "\n".join(f"{i + 1}. {option}" for i, option in enumerate(options))
    categories_lines = "\n".join(f"- ID {category_id}: {name}" for category_id, name in categories)

    if requested_category_id is not None:
        requested_line = (
            f"Foydalanuvchi bu savolni ID {requested_category_id} raqamli kategoriyaga taklif qilmoqda - "
            "agar bu mos kelsa shu ID'ni tanlang, mos kelmasa ro'yxatdan to'g'ri kategoriyani tanlang."
        )
    else:
        requested_line = "Foydalanuvchi kategoriya tanlamagan - ro'yxatdan eng mos keladigan kategoriyani siz tanlang."

    prompt = (
        "Siz viktorina ilovasi uchun savollarni tekshiruvchi qat'iy moderatordirsiz. Foydalanuvchi "
        "quyidagi savolni tizimga (BARCHA foydalanuvchilarga ko'rinadigan umumiy savollar bazasiga) "
        "qo'shishni so'ramoqda. Savolni diqqat bilan tekshiring:\n\n"
        f"Savol: {question_text}\n"
        f"Variantlar:\n{options_lines}\n"
        f"Foydalanuvchi \"to'g'ri\" deb belgilagan variant: {correct_option_text}\n\n"
        "Quyidagi holatlarning BIRORTASI to'g'ri bo'lsa rad eting (is_approved=false) va "
        "rejection_reason maydoniga qisqa, tushunarli, o'zbek tilida sababni yozing:\n"
        "- Savol matni tushunarsiz, mantiqsiz, yoki haqiqiy savol emas (masalan tasodifiy matn, spam, reklama).\n"
        "- Belgilangan \"to'g'ri\" javob aslida noto'g'ri yoki bahsli/noaniq.\n"
        "- Variantlardan bir nechtasi aslida to'g'ri bo'lishi mumkin (noaniq savol).\n"
        "- Savol yoki variantlar haqoratli, kamsituvchi, siyosiy/diniy nafrat uyg'otuvchi, yoki "
        "umuman nomaqbul.\n\n"
        f"{requested_line}\n\n"
        f"Mavjud faol kategoriyalar:\n{categories_lines}\n\n"
        "is_approved=true bo'lsa, category_id maydoniga yuqoridagi ro'yxatdan ANIQ bitta ID yozing "
        "(ro'yxatda yo'q ID yozish taqiqlanadi). is_approved=false bo'lsa ham category_id maydoniga "
        "ro'yxatdan istalgan bitta ID yozing (bu holda javobda e'tiborga olinmaydi, shunchaki maydon "
        "bo'sh qolmasin). is_approved=true bo'lsa rejection_reason maydonini bo'sh string (\"\") "
        "qoldiring."
    )

    try:
        raw_text = await call_gemini(prompt, response_schema=_RESPONSE_SCHEMA)
    except GeminiCallError as exc:
        raise QuestionModerationError(str(exc)) from exc

    try:
        data = json.loads(raw_text)
        is_approved = data["is_approved"]
        rejection_reason = data.get("rejection_reason") or None
        category_id = data["category_id"]
    except (KeyError, ValueError, TypeError) as exc:
        logger.exception("Savol moderatsiyasi javobini o'qib bo'lmadi")
        raise QuestionModerationError("AI javobini qayta ishlab bo'lmadi") from exc

    if not isinstance(is_approved, bool) or not isinstance(category_id, int):
        logger.error("AI moderatsiya javobi kutilgan shaklda emas: %r", data)
        raise QuestionModerationError("AI javobi kutilgan shaklda emas")

    valid_category_ids = {cid for cid, _ in categories}
    if is_approved and category_id not in valid_category_ids:
        # AI hech qachon ro'yxatda yo'q ID qaytarmasligi kerak, lekin
        # ko'r-ko'rona ishonib bazaga noto'g'ri category_id yozib
        # qo'ymaslik uchun himoya qatlami sifatida tekshiramiz.
        logger.error("AI mavjud bo'lmagan kategoriya ID qaytardi: %s (mavjud: %s)", category_id, valid_category_ids)
        raise QuestionModerationError("AI yaroqsiz kategoriya tanladi")

    return ModerationResult(
        is_approved=is_approved,
        rejection_reason=rejection_reason if not is_approved else None,
        category_id=category_id if is_approved else None,
    )
