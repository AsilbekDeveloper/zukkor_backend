from pydantic import BaseModel, Field


class CategoryOut(BaseModel):
    id: int
    name: str
    icon_name: str
    color_key: str
    question_count: int


class QuizStartRequest(BaseModel):
    category_id: int
    question_count: int = Field(..., ge=1, le=50)


class QuestionOut(BaseModel):
    session_question_id: int
    question_text: str
    options: list[str]
    # Ataylab, foydalanuvchi so'rovi bilan qo'shildi (2026-08-26) - avval
    # bu qiymat faqat javob berilgandan keyin yuborilardi (aldashning
    # oldini olish uchun). Endi savol bilan birga kelib, klient
    # to'g'ri/noto'g'rini serverga murojaat qilmasdan bir zumda ko'rsata
    # oladi - buning evaziga to'g'ri javob endi tarmoq orqali oldindan
    # ko'rinadi (aylanma yo'l bilan aldash mumkin bo'lib qoladi).
    correct_option_index: int
    order: int
    total: int
    time_limit_ms: int


class QuizStartResponse(BaseModel):
    session_id: str
    question: QuestionOut


class AnswerRequest(BaseModel):
    session_question_id: int
    selected_option: int | None = None


class QuestionBreakdownOut(BaseModel):
    order: int
    question_text: str
    is_correct: bool


class QuizSummary(BaseModel):
    total_ball: int
    correct_count: int
    total_questions: int
    xp_earned: int
    new_total_xp: int
    breakdown: list[QuestionBreakdownOut] = []


class AnswerResponse(BaseModel):
    correct: bool
    correct_option_index: int
    ball_earned: int
    next_question: QuestionOut | None = None
    session_complete: bool | None = None
    summary: QuizSummary | None = None
