"""Foydalanuvchi yaratgan (AI yoki qo'lda) shaxsiy quizlarga kim kira
olishini aniqlaydigan yagona qoida - Solo start, Duel taklifi va Lobby
o'yin boshlash shu yerdan foydalanadi, mantiq takrorlanmasin deb."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.friendship import Friendship
from app.models.quiz import Category


async def is_friend(db: AsyncSession, user_id: str, other_id: str) -> bool:
    # `Friendship` har doim ikkala yo'nalishda ham yaratiladi
    # (app.routers.friends._create_mutual_friendship), shuning uchun
    # bitta yo'nalishni tekshirish yetarli.
    result = await db.execute(
        select(Friendship).where(Friendship.user_id == user_id, Friendship.friend_id == other_id)
    )
    return result.scalar_one_or_none() is not None


async def can_access_category(db: AsyncSession, user_id: str, category: Category) -> bool:
    if category.owner_user_id is None:
        return True  # global kategoriya - hammaga ochiq
    if category.owner_user_id == user_id:
        return True  # egasi - visibility'dan qat'iy nazar har doim ishlata oladi
    if category.visibility == "public":
        return True
    if category.visibility == "friends":
        return await is_friend(db, user_id, category.owner_user_id)
    return False  # 'private'
