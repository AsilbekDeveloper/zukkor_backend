from pydantic import BaseModel, Field, field_validator

ALLOWED_AVATAR_COLORS = {"a-coral", "a-teal", "a-terra", "a-pink", "a-blue"}


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
