import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, get_db
from app.dependencies.auth import get_current_user
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notifications import NotificationEntryOut, NotificationsOut
from app.services.display_name import display_name

router = APIRouter()

logger = logging.getLogger("zukkor.notifications")

_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60  # kuniga bir marta yetarli
_NOTIFICATION_RETENTION_DAYS = 30


async def _cleanup_old_notifications_once() -> None:
    async with AsyncSessionLocal() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(days=_NOTIFICATION_RETENTION_DAYS)
        await db.execute(delete(Notification).where(Notification.created_at < cutoff))
        await db.commit()


async def cleanup_old_notifications_loop() -> None:
    """Fon rejimida - har kuni 30 kundan eski bildirishnomalarni o'chiradi.
    `GET /notifications` allaqachon `limit`ga ega (hech qachon butun
    jadvalni qaytarmaydi), shuning uchun bu tezlik uchun emas - vaqt
    o'tishi bilan jadval abadiy o'sib ketmasligi uchun."""
    while True:
        try:
            await _cleanup_old_notifications_once()
        except Exception:
            logger.exception("Eski bildirishnomalarni tozalashda xatolik")
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)


@router.get("", response_model=NotificationsOut, summary="Bildirishnomalar ro'yxati")
async def list_notifications(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    limit = min(max(limit, 1), 100)

    stmt = (
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    notifications = (await db.execute(stmt)).scalars().all()

    related_user_ids = {n.related_user_id for n in notifications if n.related_user_id is not None}
    related_users = {}
    if related_user_ids:
        users_result = await db.execute(select(User).where(User.id.in_(related_user_ids)))
        related_users = {u.id: u for u in users_result.scalars().all()}

    return NotificationsOut(
        entries=[
            NotificationEntryOut(
                id=n.id,
                kind=n.kind,
                created_at=n.created_at,
                is_read=n.is_read,
                related_user_name=(
                    display_name(related_users[n.related_user_id])
                    if n.related_user_id in related_users
                    else None
                ),
            )
            for n in notifications
        ]
    )


@router.post("/mark-all-read", status_code=status.HTTP_204_NO_CONTENT, summary="Barchasini o'qilgan deb belgilash")
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(Notification)
        .where(Notification.user_id == current_user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    await db.commit()
