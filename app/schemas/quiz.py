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
    order: int
    total: int
    time_limit_ms: int


class QuizStartResponse(BaseModel):
    session_id: str
    question: QuestionOut


class AnswerRequest(BaseModel):
    session_question_id: int
    selected_option: int | None = None


class QuizSummary(BaseModel):
    total_ball: int
    correct_count: int
    total_questions: int
    xp_earned: int
    new_total_xp: int


class AnswerResponse(BaseModel):
    correct: bool
    correct_option_index: int
    ball_earned: int
    next_question: QuestionOut | None = None
    session_complete: bool | None = None
    summary: QuizSummary | None = None
