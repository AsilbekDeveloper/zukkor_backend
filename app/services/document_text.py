"""Yuklangan hujjatdan (PDF/Word/matn) xom matnni ajratib olish — AI orqali
quiz generatsiya qilishning birinchi bosqichi."""

import io

from docx import Document as DocxDocument
from pypdf import PdfReader

_SUPPORTED_EXTENSIONS = {"pdf", "docx", "txt"}


class UnsupportedDocumentError(Exception):
    """Fayl turi qo'llab-quvvatlanmaydi yoki undan matn chiqarib bo'lmadi."""


def extract_text(filename: str, raw: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _SUPPORTED_EXTENSIONS:
        raise UnsupportedDocumentError(
            "Qo'llab-quvvatlanmaydigan fayl turi - faqat PDF, Word (.docx) yoki matn (.txt) fayllar qabul qilinadi"
        )

    if ext == "pdf":
        return _extract_pdf(raw)
    if ext == "docx":
        return _extract_docx(raw)
    return _extract_txt(raw)


def _extract_pdf(raw: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 - ishonchsiz fayl, aniq xatolik turi kutubxonaga bog'liq
        raise UnsupportedDocumentError("PDF fayldan matn o'qib bo'lmadi") from exc


def _extract_docx(raw: bytes) -> str:
    try:
        document = DocxDocument(io.BytesIO(raw))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    except Exception as exc:  # noqa: BLE001 - ishonchsiz fayl, aniq xatolik turi kutubxonaga bog'liq
        raise UnsupportedDocumentError("Word fayldan matn o'qib bo'lmadi") from exc


def _extract_txt(raw: bytes) -> str:
    for encoding in ("utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnsupportedDocumentError("Matn faylini o'qib bo'lmadi")
