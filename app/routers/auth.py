from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions
from jose import JWTError
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
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
from app.models.duel import Duel, DuelAnswer, DuelInvite, DuelQuestion
from app.models.friend_request import FriendRequest
from app.models.friendship import Friendship
from app.models.lobby_game import LobbyGameResult
from app.models.notification import Notification
from app.models.push_token import PushToken
from app.models.quiz import Answer, QuizSession, SessionQuestion
from app.models.user import RefreshToken, User
from app.services.firebase import get_firebase_app
from app.models.xp_event import XpEvent
from app.schemas.auth import (
    ChangePasswordRequest,
    DeleteAccountRequest,
    GoogleAuthRequest,
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

    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        # Yuqoridagi tekshiruvdan keyin, flush'gacha bo'lgan oraliqda boshqa
        # so'rov shu email bilan ro'yxatdan o'tgan bo'lsa (poyga, masalan
        # ikki marta bosilgan "Ro'yxatdan o'tish") - unique cheklovi
        # buziladi. 500 o'rniga toza 400 qaytaramiz.
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bu email allaqachon ro'yxatdan o'tgan")

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

    # `user.hashed_password` is null for a Google-only account (never set
    # a password) - `verify_password` would crash trying to hash-compare
    # against None, so that case must be rejected before ever calling it.
    if not user or not user.hashed_password or not verify_password(data.password, user.hashed_password):
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
    "/google",
    response_model=TokenResponse,
    summary="Google orqali kirish",
    description="Google ID tokenni tekshiradi, foydalanuvchini topadi yoki yaratadi, access + refresh token qaytaradi.",
)
async def google_auth(data: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    app = get_firebase_app()
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google kirish hozircha sozlanmagan"
        )

    try:
        payload = firebase_auth.verify_id_token(data.id_token, app=app)
    except (ValueError, firebase_exceptions.FirebaseError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google token yaroqsiz")

    if not payload.get("email_verified", False):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google email tasdiqlanmagan")

    google_id = payload["uid"]
    email = payload["email"]

    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if user is None:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is not None:
            user.google_id = google_id
        else:
            user = User(email=email, google_id=google_id, auth_provider="google")
            db.add(user)
            await db.flush()

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


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Parolni o'zgartirish",
)
async def change_password(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.hashed_password or not verify_password(
        data.current_password, current_user.hashed_password
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Joriy parol noto'g'ri")

    current_user.hashed_password = hash_password(data.new_password)
    await db.commit()


DELETED_USER_ID = "00000000-0000-0000-0000-000000000000"


async def _ensure_deleted_user_placeholder(db: AsyncSession) -> None:
    """A finished Duel row is shared with the opponent's own history, so
    deleting it outright would silently erase that duel from the OTHER
    (still-active) user's History screen. Reassigning the departing
    user's side of the row to this fixed placeholder account keeps the
    opponent's entry intact instead - `display_name`/avatar already fall
    back cleanly since it has no real name or avatar set.
    """
    if await db.get(User, DELETED_USER_ID) is not None:
        return
    db.add(
        User(
            id=DELETED_USER_ID,
            email="deleted-user@zukkor.internal",
            username=None,
            hashed_password=None,
            is_active=False,
            first_name="O'chirilgan foydalanuvchi",
            avatar_color=None,
        )
    )


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hisobni o'chirish",
    description="Hisobni va unga bog'liq barcha ma'lumotlarni (do'stlar, o'yin tarixi) butunlay o'chiradi.",
)
async def delete_account(
    data: DeleteAccountRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Google orqali kirgan hisoblarda parol umuman yo'q (hashed_password None)
    # - bunday hisob uchun parol tekshiruvi o'tkazib yuboriladi, chunki
    # tekshirishga hech narsa yo'q; joriy JWT sessiyaning o'zi identifikatsiya
    # sifatida yetarli. Aks holda (email/parol hisobi) parol majburiy.
    if current_user.hashed_password is not None:
        if not data.password or not verify_password(data.password, current_user.hashed_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Parol noto'g'ri")

    user_id = current_user.id

    await db.execute(
        delete(Friendship).where(or_(Friendship.user_id == user_id, Friendship.friend_id == user_id))
    )
    await db.execute(
        delete(FriendRequest).where(
            or_(FriendRequest.from_user_id == user_id, FriendRequest.to_user_id == user_id)
        )
    )

    session_ids_subq = select(QuizSession.id).where(QuizSession.user_id == user_id)
    session_question_ids_subq = select(SessionQuestion.id).where(SessionQuestion.session_id.in_(session_ids_subq))
    await db.execute(delete(Answer).where(Answer.session_question_id.in_(session_question_ids_subq)))
    await db.execute(delete(SessionQuestion).where(SessionQuestion.session_id.in_(session_ids_subq)))
    await db.execute(delete(QuizSession).where(QuizSession.user_id == user_id))

    duel_ids_subq = select(Duel.id).where(or_(Duel.user_a_id == user_id, Duel.user_b_id == user_id))
    duel_question_ids_subq = select(DuelQuestion.id).where(DuelQuestion.duel_id.in_(duel_ids_subq))
    await db.execute(delete(DuelAnswer).where(DuelAnswer.duel_question_id.in_(duel_question_ids_subq)))
    await db.execute(delete(DuelQuestion).where(DuelQuestion.duel_id.in_(duel_ids_subq)))

    own_duels = (
        await db.execute(select(Duel).where(or_(Duel.user_a_id == user_id, Duel.user_b_id == user_id)))
    ).scalars().all()
    if own_duels:
        await _ensure_deleted_user_placeholder(db)
        await db.flush()  # placeholder hisob bazaga yozilishini kafolatlaydi, duelga bog'lashdan oldin
        for duel in own_duels:
            if duel.user_a_id == user_id:
                duel.user_a_id = DELETED_USER_ID
            else:
                duel.user_b_id = DELETED_USER_ID

    await db.execute(
        delete(DuelInvite).where(or_(DuelInvite.from_user_id == user_id, DuelInvite.to_user_id == user_id))
    )

    await db.execute(delete(LobbyGameResult).where(LobbyGameResult.user_id == user_id))

    await db.execute(
        delete(Notification).where(
            or_(Notification.user_id == user_id, Notification.related_user_id == user_id)
        )
    )
    await db.execute(delete(XpEvent).where(XpEvent.user_id == user_id))
    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
    await db.execute(delete(PushToken).where(PushToken.user_id == user_id))

    await db.delete(current_user)
    await db.commit()
