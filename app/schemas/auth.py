from pydantic import BaseModel, EmailStr, Field, field_validator


def _password_strength(v: str) -> str:
    if not any(c.isupper() for c in v):
        raise ValueError("Parolda kamida 1 ta katta harf bo'lishi kerak")
    if not any(c.isdigit() for c in v):
        raise ValueError("Parolda kamida 1 ta raqam bo'lishi kerak")
    return v


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., examples=["ali@example.com"])
    password: str = Field(..., min_length=8, examples=["Parol1234"])

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _password_strength(v)


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., examples=["ali@example.com"])
    password: str = Field(..., examples=["Parol1234"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="Refresh token")


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, examples=["YangiParol1234"])

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return _password_strength(v)


class DeleteAccountRequest(BaseModel):
    password: str


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
        )
