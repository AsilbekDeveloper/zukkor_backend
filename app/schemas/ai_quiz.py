from pydantic import BaseModel


class AiQuizOut(BaseModel):
    id: int
    name: str
    question_count: int
    created_at: str
    source: str  # 'ai_document' | 'ai_topic' | 'manual'
    visibility: str  # 'private' | 'friends' | 'public'


class ManualQuestionIn(BaseModel):
    question_text: str
    options: list[str]
    correct_option_index: int


class ManualQuizCreate(BaseModel):
    name: str
    questions: list[ManualQuestionIn]


class VisibilityUpdate(BaseModel):
    visibility: str  # 'private' | 'friends' | 'public'
