from pydantic import BaseModel


class AiQuizOut(BaseModel):
    id: int
    name: str
    question_count: int
    created_at: str
    source: str  # 'ai_document' | 'ai_topic' | 'manual'
    visibility: str  # 'private' | 'friends' | 'public'


class DiscoverQuizOut(AiQuizOut):
    """Same shape as [AiQuizOut] plus who made it - needed once a listing
    (Discover feed/search) can mix quizzes from many different owners,
    unlike "my quizzes" or "this one user's quizzes" where the owner is
    already implied by which endpoint you called."""

    owner_user_id: str
    owner_username: str | None


class ManualQuestionIn(BaseModel):
    question_text: str
    options: list[str]
    correct_option_index: int


class ManualQuizCreate(BaseModel):
    name: str
    questions: list[ManualQuestionIn]


class VisibilityUpdate(BaseModel):
    visibility: str  # 'private' | 'friends' | 'public'
