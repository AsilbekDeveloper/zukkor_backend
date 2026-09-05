# elapsed_ms is measured server-side from broadcast_at (question sent) to
# the answer request arriving back, so it always includes two network legs
# the client's own on-screen countdown never sees. Without this grace period,
# a correct answer submitted instantly by the player can still score 0 purely
# because of network/cold-start latency, with no error shown to explain it.
_NETWORK_GRACE_MS = 3000


def calculate_ball(elapsed_ms: float, time_limit_ms: int, is_correct: bool) -> int:
    if not is_correct or elapsed_ms > time_limit_ms + _NETWORK_GRACE_MS:
        return 0
    remaining = max(0, time_limit_ms - elapsed_ms)
    return round(1000 * (0.5 + (remaining / time_limit_ms) * 0.5))


# Har bir savol uchun bir xil 15 soniya YETARLI EMAS EDI - uzunroq savol/
# variantlarni o'qib ulgurish uchun ko'proq vaqt kerak. Shuning uchun qattiq
# konstanta o'rniga savol matni + variantlar uzunligiga qarab hisoblaymiz.
# Hech qachon eskisidan (15s) KAM bo'lmaydi - faqat uzunroq savollarga
# qo'shimcha vaqt qo'shiladi, 30s bilan cheklanadi (haddan tashqari uzun
# savol butun o'yinni cho'zib yubormasin).
_BASE_TIME_LIMIT_MS = 15_000
_MAX_TIME_LIMIT_MS = 30_000
# ~22 belgi/soniya o'qish tezligi (fikrlash+bosish uchun ham joy qoldirib) -
# taxminan 45ms/belgi.
_MS_PER_CHAR = 45


def compute_time_limit_ms(question_text: str, options: list[str]) -> int:
    total_chars = len(question_text) + sum(len(option) for option in options)
    extra_ms = total_chars * _MS_PER_CHAR
    return min(_BASE_TIME_LIMIT_MS + extra_ms, _MAX_TIME_LIMIT_MS)
