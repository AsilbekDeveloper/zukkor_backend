from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., examples=["ali@example.com"])
    username: str = Field(..., min_length=3, max_length=30, examples=["ali_uz"])
    password: str = Field(..., min_length=8, examples=["Parol1234"])

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not all(c.isalnum() or c == "_" for c in v):
            raise ValueError("Username faqat harf, raqam va _ dan iborat bo'lishi kerak")
        return v.lower()

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Parolda kamida 1 ta katta harf bo'lishi kerak")
        if not any(c.isdigit() for c in v):
            raise ValueError("Parolda kamida 1 ta raqam bo'lishi kerak")
        return v


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., examples=["ali@example.com"])
    password: str = Field(..., examples=["Parol1234"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="Refresh token")


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    is_active: bool
    created_at: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, user):
        return cls(
            id=user.id,
            email=user.email,
            username=user.username,
            is_active=user.is_active,
            created_at=user.created_at.isoformat(),
        )
