import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings

logger = logging.getLogger("zukkor.email")


def _send_via_smtp(to_email: str, subject: str, html_body: str) -> None:
    # smtplib is blocking - this whole function is only ever called via
    # asyncio.to_thread() below, never directly from an async def.
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME
    message["To"] = to_email
    message.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
        server.starttls()
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.sendmail(settings.SMTP_USERNAME, [to_email], message.as_string())


async def send_password_reset_email(to_email: str, code: str) -> bool:
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        logger.warning("SMTP sozlanmagan - parolni tiklash emaili yuborilmadi (to=%s)", to_email)
        return False

    html_body = (
        "<p>Parolni tiklash uchun kodingiz:</p>"
        f"<p style='font-size:28px;font-weight:700;letter-spacing:4px'>{code}</p>"
        "<p>Kod 15 daqiqa amal qiladi. Agar bu so'rovni siz yubormagan bo'lsangiz, "
        "shunchaki e'tiborsiz qoldiring.</p>"
    )

    try:
        # smtplib has no asyncio-native API - offloading to a worker thread
        # keeps this from blocking the event loop (and every other request
        # being served concurrently) for the duration of the SMTP round trip.
        await asyncio.to_thread(_send_via_smtp, to_email, "Zukkor - parolni tiklash kodi", html_body)
    except (smtplib.SMTPException, OSError):
        logger.exception("Gmail SMTP orqali yuborishda xatolik (to=%s)", to_email)
        return False

    return True
