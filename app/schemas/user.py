from pydantic import BaseModel, Field, field_validator

ALLOWED_AVATAR_COLORS = {"a-coral", "a-teal", "a-terra", "a-pink", "a-blue"}
ALLOWED_DIRECTIONS = {"student_uni", "student_school", "exam_prep", "casual"}
ALLOWED_QUIZ_LIKING = {"love_it", "its_ok", "not_really"}

# Erkin matnli "Other" javoblari uchun chegara — DB ustunlari
# (study_place VARCHAR(50)) bilan mos, aks holda uzun qiymat 500 xato beradi.
MAX_STUDY_PLACE_LEN = 50
MAX_INTEREST_LEN = 50
MAX_INTERESTS_COUNT = 30


class PushTokenRequest(BaseModel):
    # push_tokens.token ustuni VARCHAR(255) - undan uzun qiymat 400 o'rniga
    # 500 DB xatosi berardi.
    token: str = Field(..., min_length=1, max_length=255)
    platform: str = Field(..., min_length=1, max_length=20)


class ProfileSetupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=30)
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    avatar_color: str | None = None
    direction: str

    # Introduction so'rovnomasi - ixtiyoriy, kelmasa mavjud qiymatga tegilmaydi.
    # "Other" javoblari erkin matn — DB ustuni chegarasidan oshsa 500 bermasligi
    # uchun bu yerda cheklanadi (quyidagi validatorlarga qarang).
    interests: list[str] | None = None
    study_place: str | None = Field(default=None, max_length=MAX_STUDY_PLACE_LEN)
    quiz_liking: str | None = None

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not all(c.isalnum() or c == "_" for c in v):
            raise ValueError("Username faqat harf, raqam va _ dan iborat bo'lishi kerak")
        return v.lower()

    @field_validator("avatar_color")
    @classmethod
    def avatar_color_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_AVATAR_COLORS:
            raise ValueError(f"avatar_color quyidagilardan biri bo'lishi kerak: {ALLOWED_AVATAR_COLORS}")
        return v

    @field_validator("direction")
    @classmethod
    def direction_valid(cls, v: str) -> str:
        if v not in ALLOWED_DIRECTIONS:
            raise ValueError(f"direction quyidagilardan biri bo'lishi kerak: {ALLOWED_DIRECTIONS}")
        return v

    @field_validator("quiz_liking")
    @classmethod
    def quiz_liking_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_QUIZ_LIKING:
            raise ValueError(f"quiz_liking quyidagilardan biri bo'lishi kerak: {ALLOWED_QUIZ_LIKING}")
        return v

    @field_validator("interests")
    @classmethod
    def interests_valid(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        if len(v) > MAX_INTERESTS_COUNT:
            raise ValueError(f"interests {MAX_INTERESTS_COUNT} tadan oshmasligi kerak")
        # Bo'sh/faqat-probel elementlarni tashlab, qolganlarini tozalab olamiz.
        cleaned = [item.strip() for item in v if item and item.strip()]
        for item in cleaned:
            if len(item) > MAX_INTEREST_LEN:
                raise ValueError(f"har bir qiziqish {MAX_INTEREST_LEN} belgidan oshmasligi kerak")
        return cleaned
