import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("zukkor.email")

RESEND_API_URL = "https://api.resend.com/emails"


async def send_password_reset_email(to_email: str, code: str) -> bool:
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY sozlanmagan - parolni tiklash emaili yuborilmadi (to=%s)", to_email)
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                RESEND_API_URL,
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json={
                    "from": settings.RESEND_FROM_EMAIL,
                    "to": [to_email],
                    "subject": "Zukkor - parolni tiklash kodi",
                    "html": (
                        "<p>Parolni tiklash uchun kodingiz:</p>"
                        f"<p style='font-size:28px;font-weight:700;letter-spacing:4px'>{code}</p>"
                        "<p>Kod 15 daqiqa amal qiladi. Agar bu so'rovni siz yubormagan bo'lsangiz, "
                        "shunchaki e'tiborsiz qoldiring.</p>"
                    ),
                },
            )
    except httpx.HTTPError:
        logger.exception("Resend'ga so'rov yuborishda xatolik (to=%s)", to_email)
        return False

    if response.status_code >= 300:
        logger.error("Resend xato qaytardi: %s %s (to=%s)", response.status_code, response.text, to_email)
        return False

    return True
