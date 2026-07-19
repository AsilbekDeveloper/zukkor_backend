from fastapi import APIRouter, Depends, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notifications import NotificationEntryOut, NotificationsOut

router = APIRouter()


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

    return NotificationsOut(
        entries=[
            NotificationEntryOut(id=n.id, kind=n.kind, created_at=n.created_at, is_read=n.is_read)
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
