import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.push_token import PushToken
from app.models.user import User
from app.schemas.auth import UserResponse
from app.schemas.notifications import NotificationPreferences
from app.schemas.user import ProfileSetupRequest, PushTokenRequest

router = APIRouter()

MEDIA_ROOT = Path("media/avatars")
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
MAX_AVATAR_SIZE_BYTES = 2 * 1024 * 1024  # 2MB
ALLOWED_CONTENT_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@router.get("/username-available", summary="Username bandligini tekshirish")
async def check_username_available(
    username: str = Query(..., min_length=3, max_length=30),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    normalized = username.lower()
    result = await db.execute(
        select(User).where(User.username == normalized, User.id != current_user.id)
    )
    return {"available": result.scalar_one_or_none() is None}


@router.patch(
    "/me/profile",
    response_model=UserResponse,
    summary="Onboarding — profilni to'ldirish",
    description="3-bosqichli onboarding wizard yakunida chaqiriladi: username, ism/familiya, avatar rangi, yo'nalish saqlanadi.",
)
async def setup_profile(
    data: ProfileSetupRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing_username = await db.execute(
        select(User).where(User.username == data.username, User.id != current_user.id)
    )
    if existing_username.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bu username band")

    current_user.username = data.username
    current_user.first_name = data.first_name
    current_user.last_name = data.last_name
    current_user.avatar_color = data.avatar_color
    current_user.avatar_image_path = None  # rang tanlansa, rasm tozalanadi — ular bir-birini istisno qiladi
    current_user.direction = data.direction
    current_user.onboarding_completed = True

    # Introduction so'rovnomasi - ixtiyoriy, faqat so'rovda kelgan bo'lsa yoziladi (kelmasa mavjud qiymat saqlanadi)
    if data.interests is not None:
        current_user.interests = data.interests
    if data.study_place is not None:
        current_user.study_place = data.study_place
    if data.quiz_liking is not None:
        current_user.quiz_liking = data.quiz_liking

    await db.commit()
    await db.refresh(current_user)

    return UserResponse.from_orm_model(current_user)


@router.post(
    "/me/avatar",
    response_model=UserResponse,
    summary="Avatar rasm yuklash",
    description="multipart/form-data, fayl maydoni nomi 'image'. Muvaffaqiyatli bo'lsa avatar_color null'ga tozalanadi.",
)
async def upload_avatar(
    request: Request,
    image: UploadFile,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if image.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Faqat JPEG, PNG yoki WEBP formatidagi rasm qabul qilinadi",
        )

    contents = await image.read()
    if len(contents) > MAX_AVATAR_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rasm hajmi 2MB dan oshmasligi kerak")

    try:
        Image.open(io.BytesIO(contents)).verify()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Fayl yaroqli rasm emas")

    # eski avatar faylini diskdan o'chirish (agar bor bo'lsa)
    if current_user.avatar_image_path:
        old_filename = current_user.avatar_image_path.rsplit("/", 1)[-1]
        old_path = MEDIA_ROOT / old_filename
        if old_path.is_file():
            old_path.unlink()

    ext = ALLOWED_CONTENT_TYPES[image.content_type]
    filename = f"{uuid.uuid4()}.{ext}"
    (MEDIA_ROOT / filename).write_bytes(contents)

    public_url = f"{str(request.base_url).rstrip('/')}/media/avatars/{filename}"
    current_user.avatar_image_path = public_url
    current_user.avatar_color = None

    await db.commit()
    await db.refresh(current_user)

    return UserResponse.from_orm_model(current_user)


@router.put(
    "/me/push-token",
    status_code=status.HTTP_200_OK,
    summary="Qurilma push tokenini bog'lash",
    description="FCM tokenni joriy foydalanuvchiga bog'laydi. Token boshqa userga bog'langan bo'lsa, undan olib qayta bog'lanadi.",
)
async def set_push_token(
    data: PushTokenRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(PushToken).where(PushToken.token == data.token))
    push_token = result.scalar_one_or_none()

    if push_token is not None:
        push_token.user_id = current_user.id
        push_token.platform = data.platform
    else:
        db.add(PushToken(user_id=current_user.id, token=data.token, platform=data.platform))

    await db.commit()
    return {}


def _notification_preferences_out(user: User) -> NotificationPreferences:
    return NotificationPreferences(
        duel_invites=user.duel_invites,
        streak_reminders=user.streak_reminders,
        leaderboard_updates=user.leaderboard_updates,
        friend_requests=user.friend_requests,
        product_updates=user.product_updates,
    )


@router.get(
    "/me/notification-preferences",
    response_model=NotificationPreferences,
    summary="Bildirishnoma sozlamalarini olish",
)
async def get_notification_preferences(current_user: User = Depends(get_current_user)):
    return _notification_preferences_out(current_user)


@router.patch(
    "/me/notification-preferences",
    response_model=NotificationPreferences,
    summary="Bildirishnoma sozlamalarini yangilash",
    description="Barcha 5 maydon birga yuborilishi kerak (to'liq holat almashtiriladi).",
)
async def update_notification_preferences(
    data: NotificationPreferences,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.duel_invites = data.duel_invites
    current_user.streak_reminders = data.streak_reminders
    current_user.leaderboard_updates = data.leaderboard_updates
    current_user.friend_requests = data.friend_requests
    current_user.product_updates = data.product_updates

    await db.commit()

    return _notification_preferences_out(current_user)
