"""Avatar rasmlarini saqlash — Cloudflare R2 (S3-mos) yoki lokal fayl tizimi.

R2 sozlangan bo'lsa (env o'zgaruvchilari to'ldirilgan) rasmlar bulutga
yuklanadi va hech qachon yo'qolmaydi. Sozlanmagan bo'lsa lokal `media/avatars/`
papkasiga tushib qolinadi — bu faqat dev uchun, chunki Render'ning vaqtinchalik
diski qayta ishga tushganda tozalanadi.
"""

import logging
import uuid
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

from app.core.config import settings

logger = logging.getLogger("zukkor.storage")

_LOCAL_ROOT = Path("media/avatars")
_KEY_PREFIX = "avatars"

_client = None
_client_init_attempted = False


def is_r2_configured() -> bool:
    return bool(
        settings.R2_ACCOUNT_ID
        and settings.R2_ACCESS_KEY_ID
        and settings.R2_SECRET_ACCESS_KEY
        and settings.R2_BUCKET
        and settings.R2_PUBLIC_BASE_URL
    )


def _get_client():
    """R2 S3-mos klientini birinchi chaqiruvda yaratadi (firebase bilan bir xil naqsh)."""
    global _client, _client_init_attempted
    if _client is not None:
        return _client
    if _client_init_attempted:
        return None
    _client_init_attempted = True

    _client = boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=BotoConfig(signature_version="s3v4"),
    )
    return _client


def save_avatar(data: bytes, ext: str, content_type: str, local_base_url: str) -> str:
    """Rasm baytlarini saqlaydi va mutlaq ommaviy URL qaytaradi.

    R2 sozlangan bo'lsa bulutga yozadi (URL sozlamalardagi ommaviy bazadan).
    Aks holda lokal diskka yozadi va URL'ni [local_base_url] (odatda
    so'rovning base_url'i) asosida quradi — klient rasmni yuklay olishi uchun
    mutlaq bo'lishi shart.
    """
    filename = f"{uuid.uuid4()}.{ext}"

    if is_r2_configured():
        client = _get_client()
        key = f"{_KEY_PREFIX}/{filename}"
        client.put_object(
            Bucket=settings.R2_BUCKET,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )
        return f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{key}"

    _LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    (_LOCAL_ROOT / filename).write_bytes(data)
    return f"{local_base_url.rstrip('/')}/media/avatars/{filename}"


def delete_avatar(url: str | None) -> None:
    """Avvalgi avatarni saqlangan joyidan o'chiradi. Best-effort — o'chirish
    muvaffaqiyatsiz bo'lsa (fayl allaqachon yo'q va h.k.) jimgina o'tiladi,
    chunki bu yangi rasm saqlanishini to'smasligi kerak."""
    if not url:
        return

    filename = url.rsplit("/", 1)[-1]
    if not filename:
        return

    try:
        if is_r2_configured():
            _get_client().delete_object(
                Bucket=settings.R2_BUCKET, Key=f"{_KEY_PREFIX}/{filename}"
            )
        else:
            old_path = _LOCAL_ROOT / filename
            if old_path.is_file():
                old_path.unlink()
    except Exception:  # noqa: BLE001 — o'chirish hech qachon so'rovni yiqitmasin
        logger.warning("Eski avatarni o'chirib bo'lmadi: %s", url, exc_info=True)
