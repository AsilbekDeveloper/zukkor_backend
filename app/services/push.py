import logging

from firebase_admin import exceptions as firebase_exceptions
from firebase_admin import messaging
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push_token import PushToken
from app.services.firebase import get_firebase_app

logger = logging.getLogger("zukkor.push")


async def send_push_to_user(db: AsyncSession, user_id: str, title: str, body: str) -> None:
    app = get_firebase_app()
    if app is None:
        return

    tokens = (await db.execute(select(PushToken.token).where(PushToken.user_id == user_id))).scalars().all()
    if not tokens:
        return

    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        tokens=list(tokens),
    )
    try:
        response = messaging.send_each_for_multicast(message, app=app)
    except firebase_exceptions.FirebaseError:
        logger.exception("Push yuborishda xatolik (user_id=%s)", user_id)
        return

    dead_tokens = [
        tokens[i]
        for i, r in enumerate(response.responses)
        if not r.success and isinstance(r.exception, messaging.UnregisteredError)
    ]
    if dead_tokens:
        await db.execute(delete(PushToken).where(PushToken.token.in_(dead_tokens)))
        await db.commit()
