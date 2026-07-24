"""Avatar rasmlarini saqlash — Cloudinary yoki lokal fayl tizimi.

Cloudinary sozlangan bo'lsa (CLOUDINARY_URL to'ldirilgan) rasmlar bulutga
yuklanadi va hech qachon yo'qolmaydi. Sozlanmagan bo'lsa lokal
`media/avatars/` papkasiga tushib qolinadi — bu faqat dev uchun, chunki
Render'ning vaqtinchalik diski qayta ishga tushganda tozalanadi.
"""

import io
import logging
import uuid
from pathlib import Path

import cloudinary
import cloudinary.uploader

from app.core.config import settings

logger = logging.getLogger("zukkor.storage")

_LOCAL_ROOT = Path("media/avatars")
_CLOUDINARY_FOLDER = "zukkor/avatars"

_configured = False


def is_cloudinary_configured() -> bool:
    return bool(settings.CLOUDINARY_URL)


def _ensure_configured() -> None:
    """Cloudinary SDK'sini birinchi chaqiruvda sozlaydi (firebase bilan bir xil naqsh)."""
    global _configured
    if _configured:
        return
    cloudinary.config(cloudinary_url=settings.CLOUDINARY_URL, secure=True)
    _configured = True


def save_avatar(data: bytes, ext: str, content_type: str, local_base_url: str) -> str:
    """Rasm baytlarini saqlaydi va mutlaq ommaviy URL qaytaradi.

    Cloudinary sozlangan bo'lsa bulutga yuklaydi (CDN URL qaytadi). Aks holda
    lokal diskka yozadi va URL'ni [local_base_url] (odatda so'rovning
    base_url'i) asosida quradi — klient rasmni yuklay olishi uchun mutlaq
    bo'lishi shart.
    """
    if is_cloudinary_configured():
        _ensure_configured()
        result = cloudinary.uploader.upload(
            io.BytesIO(data),
            folder=_CLOUDINARY_FOLDER,
            public_id=str(uuid.uuid4()),
            resource_type="image",
            overwrite=True,
        )
        return result["secure_url"]

    filename = f"{uuid.uuid4()}.{ext}"
    _LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    (_LOCAL_ROOT / filename).write_bytes(data)
    return f"{local_base_url.rstrip('/')}/media/avatars/{filename}"


def delete_avatar(url: str | None) -> None:
    """Avvalgi avatarni saqlangan joyidan o'chiradi. Best-effort — o'chirish
    muvaffaqiyatsiz bo'lsa (fayl allaqachon yo'q va h.k.) jimgina o'tiladi,
    chunki bu yangi rasm saqlanishini to'smasligi kerak."""
    if not url:
        return

    try:
        if is_cloudinary_configured():
            public_id = _cloudinary_public_id(url)
            if public_id:
                _ensure_configured()
                cloudinary.uploader.destroy(public_id, resource_type="image", invalidate=True)
        else:
            filename = url.rsplit("/", 1)[-1]
            if filename:
                old_path = _LOCAL_ROOT / filename
                if old_path.is_file():
                    old_path.unlink()
    except Exception:  # noqa: BLE001 — o'chirish hech qachon so'rovni yiqitmasin
        logger.warning("Eski avatarni o'chirib bo'lmadi: %s", url, exc_info=True)


def _cloudinary_public_id(url: str) -> str | None:
    """Cloudinary secure_url'idan public_id'ni ajratib oladi.

    Masalan `https://res.cloudinary.com/<cloud>/image/upload/v123/zukkor/avatars/abc.jpg`
    dan `zukkor/avatars/abc` (versiya prefiksi va kengaytmasiz).
    """
    marker = "/upload/"
    idx = url.find(marker)
    if idx == -1:
        return None
    tail = url[idx + len(marker):]  # "v123/zukkor/avatars/abc.jpg"
    parts = tail.split("/")
    if parts and parts[0].startswith("v") and parts[0][1:].isdigit():
        parts = parts[1:]  # versiya prefiksini olib tashlash
    path = "/".join(parts)
    return path.rsplit(".", 1)[0] or None  # kengaytmani olib tashlash
