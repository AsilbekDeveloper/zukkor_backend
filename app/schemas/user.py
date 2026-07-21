from pydantic import BaseModel, Field, field_validator

ALLOWED_AVATAR_COLORS = {"a-coral", "a-teal", "a-terra", "a-pink", "a-blue"}
ALLOWED_DIRECTIONS = {"student_uni", "student_school", "exam_prep", "casual"}


class PushTokenRequest(BaseModel):
    token: str = Field(..., min_length=1)
    platform: str = Field(..., min_length=1, max_length=20)


class ProfileSetupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=30)
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    avatar_color: str
    direction: str

    # Introduction so'rovnomasi - ixtiyoriy, kelmasa mavjud qiymatga tegilmaydi
    interests: list[str] | None = None
    study_place: str | None = None
    quiz_liking: str | None = None

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not all(c.isalnum() or c == "_" for c in v):
            raise ValueError("Username faqat harf, raqam va _ dan iborat bo'lishi kerak")
        return v.lower()

    @field_validator("avatar_color")
    @classmethod
    def avatar_color_valid(cls, v: str) -> str:
        if v not in ALLOWED_AVATAR_COLORS:
            raise ValueError(f"avatar_color quyidagilardan biri bo'lishi kerak: {ALLOWED_AVATAR_COLORS}")
        return v

    @field_validator("direction")
    @classmethod
    def direction_valid(cls, v: str) -> str:
        if v not in ALLOWED_DIRECTIONS:
            raise ValueError(f"direction quyidagilardan biri bo'lishi kerak: {ALLOWED_DIRECTIONS}")
        return v
