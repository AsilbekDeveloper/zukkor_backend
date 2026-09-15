import jwt
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.security import decode_token


def _rate_limit_key(request: Request) -> str:
    """Foydalanuvchi autentifikatsiya qilingan bo'lsa ANIQ SHU
    foydalanuvchining ID'si bo'yicha, aks holda (login/register kabi hali
    hisobi yo'q so'rovlar uchun) IP bo'yicha cheklaydi.

    2026-09-16, xavfsizlik auditi: faqat IP bo'yicha cheklash VPN/proksi
    bilan aylanib o'tiladi - hujumchi bir nechta IP orqali chiqsa, har
    biri o'zining alohida limitiga ega bo'lib, bitta akkauntdan
    umumiy limitsiz so'rov yuborish mumkin bo'lib qolardi. Endi haqiqiy
    JWT'si bor har qanday so'rov, IP'idan qat'iy nazar, BITTA umumiy
    "user:<id>" hisoblagichiga tushadi.

    Tokenni bu yerda `decode_token` bilan o'qish shunchaki limit kalitini
    tanlash uchun - yaroqsiz/eskirgan token xato TASHLAMAYDI (shunchaki
    IP'ga tushiriladi), haqiqiy autentifikatsiya tekshiruvi baribir
    `app.dependencies.auth.get_current_user`da, alohida, to'liq bo'ladi."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        try:
            payload = decode_token(token)
            user_id = payload.get("sub")
            if payload.get("type") == "access" and user_id:
                return f"user:{user_id}"
        except jwt.PyJWTError:
            pass
    return f"ip:{get_remote_address(request)}"


# Default: 100/minute. AI-generatsiya kabi qimmat endpointlar o'z
# dekoratorida qattiqroq limit belgilaydi (masalan `@limiter.limit("5/minute")`,
# `app/routers/ai_quiz.py`).
limiter = Limiter(key_func=_rate_limit_key, default_limits=["100/minute"])
