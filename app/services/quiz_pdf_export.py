"""Quizni bosma (A4, PDF) test qog'oziga aylantirish - o'qituvchi
sinfda qog'ozda test o'tkazishni xohlasa (2026-09-28). Birinchi sahifa(lar)da
o'quvchiga beriladigan BO'SH test (to'g'ri javob ko'rsatilmagan), oxirida
alohida "Javoblar kaliti" sahifasi - bitta faylda ikkalasi ham bor, lekin
o'qituvchi kalit sahifasini kesib/ko'rsatmay saqlab qolishi mumkin.

Reportlab'ning ichki shriftlari (Helvetica va h.k.) faqat Latin-1'ni
qamrab oladi - o'zbek lotin harflari (oʻ, gʻ) va kirill (ruscha savollar)
ularda TO'G'RI chiqmaydi. Shuning uchun ochiq litsenziyali DejaVu Sans
shrifti bog'lab qo'yilgan (`app/assets/fonts/`, litsenziya shu yerda) -
Kirill+Lotin Extended'ni to'liq qamrab oladi."""

import io
from pathlib import Path

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from app.models.quiz import Question

_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONT_REGULAR = "DejaVuSans"
_FONT_BOLD = "DejaVuSans-Bold"

_fonts_registered = False


def _ensure_fonts_registered() -> None:
    # Modul birinchi marta ishlatilganda BIR MARTA ro'yxatdan o'tkaziladi -
    # `pdfmetrics.registerFont` global registrator, takror chaqirilsa xato
    # bermaydi, lekin har PDF uchun qayta fayldan o'qish shart emas.
    global _fonts_registered
    if _fonts_registered:
        return
    pdfmetrics.registerFont(TTFont(_FONT_REGULAR, str(_FONTS_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(_FONT_BOLD, str(_FONTS_DIR / "DejaVuSans-Bold.ttf")))
    _fonts_registered = True


def _option_letter(index: int) -> str:
    return chr(ord("A") + index)


def build_quiz_pdf(*, quiz_name: str, questions: list[Question]) -> bytes:
    """`questions` DB tartibida (ID bo'yicha) keladi deb kutiladi - eksport
    qilingan test har safar BIR XIL tartibda chiqishi kerak (o'qituvchi bir
    nechta nusxa chop etsa ham savollar mos kelishi uchun), shuning uchun
    `/quiz/start`dagi kabi tasodifiy aralashtirish YO'Q."""
    _ensure_fonts_registered()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        title=quiz_name,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "QuizTitle", parent=styles["Title"], fontName=_FONT_BOLD, fontSize=18, alignment=TA_CENTER,
    )
    meta_style = ParagraphStyle("QuizMeta", parent=styles["Normal"], fontName=_FONT_REGULAR, fontSize=11)
    question_style = ParagraphStyle(
        "QuestionText", parent=styles["Normal"], fontName=_FONT_BOLD, fontSize=12, spaceBefore=10, spaceAfter=4,
    )
    option_style = ParagraphStyle(
        "OptionText", parent=styles["Normal"], fontName=_FONT_REGULAR, fontSize=11, leftIndent=14, spaceAfter=2,
    )
    section_title_style = ParagraphStyle(
        "SectionTitle", parent=styles["Title"], fontName=_FONT_BOLD, fontSize=16, alignment=TA_CENTER,
    )
    answer_style = ParagraphStyle("AnswerLine", parent=styles["Normal"], fontName=_FONT_REGULAR, fontSize=11)

    story = [
        Paragraph(quiz_name, title_style),
        Spacer(1, 0.6 * cm),
        Paragraph("F.I.Sh: _______________________________________", meta_style),
        Spacer(1, 0.2 * cm),
        Paragraph("Sinf/guruh: ____________&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Sana: ____________", meta_style),
        Spacer(1, 0.8 * cm),
    ]

    for i, question in enumerate(questions, start=1):
        story.append(Paragraph(f"{i}. {question.question_text}", question_style))
        for option_index, option_text in enumerate(question.options):
            story.append(Paragraph(f"{_option_letter(option_index)}) {option_text}", option_style))

    story.append(PageBreak())
    story.append(Paragraph("Javoblar kaliti", section_title_style))
    story.append(Spacer(1, 0.6 * cm))
    for i, question in enumerate(questions, start=1):
        letter = _option_letter(question.correct_option_index)
        story.append(Paragraph(f"{i} — {letter}", answer_style))

    doc.build(story)
    return buffer.getvalue()
