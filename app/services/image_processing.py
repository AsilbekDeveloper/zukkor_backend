"""Yuklangan avatar rasmini xavfsiz normallashtirish.

Xom foydalanuvchi baytlarini hech qachon to'g'ridan-to'g'ri saqlamaymiz:
Pillow orqali qayta kodlaymiz. Bu (a) faylning haqiqatan ham rasm ekanini
kafolatlaydi, (b) EXIF orientatsiyasini qo'llab, metadatani tozalaydi,
(c) o'lchamni chegaralab, "decompression bomb"ning oldini oladi.
"""

import io

from PIL import Image, ImageOps, UnidentifiedImageError

_MAX_DIMENSION = 512  # avatar uchun yetarli — kattaroq saqlash keraksiz
_JPEG_QUALITY = 85


class InvalidImageError(Exception):
    """Yuklangan fayl yaroqli rasm emas (yoki juda katta / buzuq)."""


def process_avatar(raw: bytes) -> tuple[bytes, str, str]:
    """Xom baytlarni normallashtirilgan JPEG'ga aylantiradi.

    Qaytaradi: (baytlar, kengaytma, content_type).
    Fayl rasm bo'lmasa yoki ochib bo'lmasa [InvalidImageError] tashlaydi.
    """
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)  # kamera orientatsiyasini qo'llash

            # Shaffoflikni oq fon ustiga tekislaymiz — JPEG shaffoflikni
            # qo'llab-quvvatlamaydi.
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                background = Image.new("RGBA", img.size, (255, 255, 255, 255))
                img = Image.alpha_composite(background, img).convert("RGB")
            else:
                img = img.convert("RGB")

            img.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION), Image.LANCZOS)

            out = io.BytesIO()
            img.save(out, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
            return out.getvalue(), "jpg", "image/jpeg"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as exc:
        raise InvalidImageError(str(exc)) from exc
