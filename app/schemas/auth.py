from pydantic import BaseModel, EmailStr, Field, field_validator


def _password_strength(v: str) -> str:
    if not any(c.isdigit() for c in v):
        raise ValueError("Parolda kamida 1 ta raqam bo'lishi kerak")
    return v


# bcrypt faqat dastlabki 72 baytni hisobga oladi — undan uzun parol jimgina
# kesiladi, shuning uchun shu chegarada rad etamiz (client ham shu cheklovda).
MAX_PASSWORD_LEN = 72


# users.email ustuni VARCHAR(255) - undan uzun qiymat 400 o'rniga 500 DB
# xatosi berardi.
MAX_EMAIL_LEN = 255


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., max_length=MAX_EMAIL_LEN, examples=["ali@example.com"])
    password: str = Field(..., min_length=6, max_length=MAX_PASSWORD_LEN, examples=["Parol1234"])

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _password_strength(v)


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., max_length=MAX_EMAIL_LEN, examples=["ali@example.com"])
    password: str = Field(..., examples=["Parol1234"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class GoogleAuthRequest(BaseModel):
    id_token: str = Field(..., description="Google ID token")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="Refresh token")


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6, max_length=MAX_PASSWORD_LEN, examples=["YangiParol1234"])

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _password_strength(v)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr = Field(..., max_length=MAX_EMAIL_LEN, examples=["ali@example.com"])


class ResetPasswordRequest(BaseModel):
    email: EmailStr = Field(..., max_length=MAX_EMAIL_LEN, examples=["ali@example.com"])
    code: str = Field(..., min_length=6, max_length=6, examples=["123456"])
    new_password: str = Field(..., min_length=6, max_length=MAX_PASSWORD_LEN, examples=["YangiParol1234"])

    @field_validator("code")
    @classmethod
    def code_numeric(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("Kod faqat raqamlardan iborat bo'lishi kerak")
        return v

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _password_strength(v)


class DeleteAccountRequest(BaseModel):
    # Google-only hisoblarda parol umuman yo'q (hashed_password None) -
    # bunday hisob uchun bu maydon talab qilinmaydi, tekshiruv shunga
    # qarab moslashadi (routers/auth.py'dagi delete_account'ga qarang).
    password: str | None = None


class UserResponse(BaseModel):
    id: str
    email: str
    username: str | None
    is_active: bool
    created_at: str
    first_name: str | None
    last_name: str | None
    avatar_color: str | None
    avatar_image_path: str | None
    direction: str | None
    onboarding_completed: bool
    auth_provider: str

    # Introduction so'rovnomasi javoblari - onboarding'da yig'ilib
    # `PATCH /users/me/profile`ga yozilgan, lekin shu paytgacha hech qanday
    # javobda qaytarilmagan edi (Profile/Edit Profile'da umuman ko'rinmasdi).
    interests: list[str] | None
    study_place: str | None
    quiz_liking: str | None

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, user):
        return cls(
            id=user.id,
            email=user.email,
            username=user.username,
            is_active=user.is_active,
            created_at=user.created_at.isoformat(),
            first_name=user.first_name,
            last_name=user.last_name,
            avatar_color=user.avatar_color,
            avatar_image_path=user.avatar_image_path,
            direction=user.direction,
            onboarding_completed=user.onboarding_completed,
            auth_provider=user.auth_provider,
            interests=user.interests,
            study_place=user.study_place,
            quiz_liking=user.quiz_liking,
        )
