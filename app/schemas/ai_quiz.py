from pydantic import BaseModel


class AiQuizOut(BaseModel):
    id: int
    name: str
    question_count: int
    created_at: str
