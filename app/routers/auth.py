from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.dependencies.auth import get_current_user
from app.models.user import RefreshToken, User
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter()


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ro'yxatdan o'tish",
    description="Yangi foydalanuvchi yaratadi va access + refresh token qaytaradi.",
)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing_email = await db.execute(select(User).where(User.email == data.email))
    if existing_email.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bu email allaqachon ro'yxatdan o'tgan")

    existing_username = await db.execute(select(User).where(User.username == data.username))
    if existing_username.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bu username band")

    user = User(
        email=data.email,
        username=data.username,
        hashed_password=hash_password(data.password),
    )
    db.add(user)
    await db.flush()

    access_token = create_access_token({"sub": user.id})
    refresh_token_str = create_refresh_token({"sub": user.id})

    rt_payload = decode_token(refresh_token_str)
    expires_at = datetime.fromtimestamp(rt_payload["exp"], tz=timezone.utc)

    db.add(RefreshToken(token=refresh_token_str, user_id=user.id, expires_at=expires_at))
    await db.commit()

    return TokenResponse(access_token=access_token, refresh_token=refresh_token_str)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Kirish",
    description="Email va parol bilan tizimga kiradi. Access + refresh token qaytaradi.",
)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email yoki parol noto'g'ri")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hisob faol emas")

    access_token = create_access_token({"sub": user.id})
    refresh_token_str = create_refresh_token({"sub": user.id})

    rt_payload = decode_token(refresh_token_str)
    expires_at = datetime.fromtimestamp(rt_payload["exp"], tz=timezone.utc)

    db.add(RefreshToken(token=refresh_token_str, user_id=user.id, expires_at=expires_at))
    await db.commit()

    return TokenResponse(access_token=access_token, refresh_token=refresh_token_str)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Tokenni yangilash",
    description="Muddati o'tmagan refresh token bilan yangi access + refresh token oladi (token rotation).",
)
async def refresh_tokens(data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = decode_token(data.refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Noto'g'ri token turi")
        user_id: str = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token yaroqsiz yoki muddati o'tgan")

    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token == data.refresh_token,
            RefreshToken.is_revoked.is_(False),
        )
    )
    db_token = result.scalar_one_or_none()

    if not db_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token bekor qilingan yoki topilmadi")

    if db_token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token muddati o'tgan")

    # Token rotation — eski token bekor qilinadi
    db_token.is_revoked = True

    new_access = create_access_token({"sub": user_id})
    new_refresh_str = create_refresh_token({"sub": user_id})

    new_payload = decode_token(new_refresh_str)
    new_expires = datetime.fromtimestamp(new_payload["exp"], tz=timezone.utc)

    db.add(RefreshToken(token=new_refresh_str, user_id=user_id, expires_at=new_expires))
    await db.commit()

    return TokenResponse(access_token=new_access, refresh_token=new_refresh_str)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Chiqish",
    description="Refresh tokenni bekor qiladi. Foydalanuvchi tizimdan chiqadi.",
)
async def logout(data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RefreshToken).where(RefreshToken.token == data.refresh_token))
    db_token = result.scalar_one_or_none()
    if db_token and not db_token.is_revoked:
        db_token.is_revoked = True
        await db.commit()


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Joriy foydalanuvchi",
    description="Access token orqali joriy foydalanuvchi ma'lumotlarini qaytaradi.",
)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse.from_orm_model(current_user)
