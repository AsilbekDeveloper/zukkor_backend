from pydantic import BaseModel


class AiQuizOut(BaseModel):
    id: int
    name: str
    question_count: int
    created_at: str
    source: str  # 'ai_document' | 'ai_topic' | 'manual'
    visibility: str  # 'private' | 'friends' | 'public'
    topic_category_id: int | None = None
    # Denormalized so the client doesn't need a second round-trip just to
    # show a category chip - same idea as DiscoverQuizOut.owner_username.
    topic_category_name: str | None = None


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
    topic_category_id: int | None = None


class VisibilityUpdate(BaseModel):
    visibility: str  # 'private' | 'friends' | 'public'


class TopicUpdate(BaseModel):
    # None - mavzu tayinlashni bekor qilish (Discover'da hech qaysi
    # filtrga tushmaydigan holatga qaytarish).
    topic_category_id: int | None = None
