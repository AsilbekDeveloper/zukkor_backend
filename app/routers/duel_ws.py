import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.duel import DuelInvite
from app.models.friendship import Friendship
from app.models.notification import Notification
from app.models.quiz import Category, Question
from app.models.user import User
from app.services import duel_engine
from app.services.ws_auth import authenticate_ws
from app.services.ws_manager import manager

router = APIRouter()


def _user_public(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "avatar_color": user.avatar_color,
        "avatar_image_path": user.avatar_image_path,
    }


async def _category_summary(db, category: Category) -> dict:
    count_result = await db.execute(
        select(func.count()).select_from(Question).where(
            Question.category_id == category.id, Question.is_active.is_(True)
        )
    )
    return {
        "id": category.id,
        "name": category.name,
        "icon_name": category.icon_name,
        "color_key": category.color_key,
        "question_count": count_result.scalar_one(),
    }


async def _deliver_pending_invites(user_id: str) -> None:
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(DuelInvite).where(
                DuelInvite.to_user_id == user_id,
                DuelInvite.status == "pending",
                DuelInvite.expires_at > now,
            )
        )
        invites = result.scalars().all()
        for invite in invites:
            from_user = await db.get(User, invite.from_user_id)
            category = await db.get(Category, invite.category_id)
            await manager.send_to_user(
                user_id,
                {
                    "type": "duel_invite_received",
                    "invite_id": invite.id,
                    "from_user": _user_public(from_user),
                    "category": await _category_summary(db, category),
                    "expires_at": invite.expires_at.isoformat(),
                },
            )


async def _handle_duel_invite(user: User, data: dict, websocket: WebSocket) -> None:
    client_invite_id = data.get("client_invite_id")
    to_user_id = data.get("to_user_id")
    category_id = data.get("category_id")
    question_count = data.get("question_count")  # ixtiyoriy; yuborilmasa duel boshlanganda standart son ishlatiladi

    if not client_invite_id or not to_user_id or not category_id:
        await websocket.send_json({"type": "error", "detail": "duel_invite: maydonlar to'liq emas"})
        return

    if question_count is not None:
        if not isinstance(question_count, int) or question_count < 1:
            await websocket.send_json(
                {"type": "error", "detail": "question_count musbat butun son bo'lishi kerak", "client_invite_id": client_invite_id}
            )
            return
        question_count = min(question_count, 50)

    async with AsyncSessionLocal() as db:
        friendship = await db.execute(
            select(Friendship).where(Friendship.user_id == user.id, Friendship.friend_id == to_user_id)
        )
        if friendship.scalar_one_or_none() is None:
            await websocket.send_json(
                {"type": "error", "detail": "Faqat do'stlarni chaqirish mumkin", "client_invite_id": client_invite_id}
            )
            return

        category = await db.get(Category, category_id)
        if category is None or not category.is_active:
            await websocket.send_json(
                {"type": "error", "detail": "Kategoriya topilmadi", "client_invite_id": client_invite_id}
            )
            return

        now = datetime.now(timezone.utc)
        invite = DuelInvite(
            from_user_id=user.id,
            to_user_id=to_user_id,
            category_id=category_id,
            question_count=question_count,
            status="pending",
            expires_at=now + timedelta(hours=24),
        )
        db.add(invite)
        db.add(Notification(user_id=to_user_id, kind="duel_challenge"))
        await db.commit()
        await db.refresh(invite)

        from_user_public = _user_public(user)
        category_summary = await _category_summary(db, category)
        expires_at_iso = invite.expires_at.isoformat()
        invite_id = invite.id

    await websocket.send_json(
        {"type": "duel_invite_ack", "client_invite_id": client_invite_id, "invite_id": invite_id}
    )

    await manager.send_to_user(
        to_user_id,
        {
            "type": "duel_invite_received",
            "invite_id": invite_id,
            "from_user": from_user_public,
            "category": category_summary,
            "expires_at": expires_at_iso,
        },
    )


async def _handle_duel_invite_respond(user: User, data: dict, websocket: WebSocket) -> None:
    invite_id = data.get("invite_id")
    accept = data.get("accept")

    if not invite_id or not isinstance(accept, bool):
        await websocket.send_json({"type": "error", "detail": "duel_invite_respond: maydonlar to'liq emas"})
        return

    async with AsyncSessionLocal() as db:
        invite = await db.get(DuelInvite, invite_id)
        if invite is None or invite.to_user_id != user.id:
            await websocket.send_json({"type": "error", "detail": "Taklif topilmadi", "invite_id": invite_id})
            return

        now = datetime.now(timezone.utc)
        if invite.status != "pending" or invite.expires_at <= now:
            await websocket.send_json({"type": "error", "detail": "Bu taklif endi faol emas", "invite_id": invite_id})
            return

        invite.status = "accepted" if accept else "declined"
        invite.responded_at = now
        await db.commit()

        responder_public = _user_public(user)
        from_user_id = invite.from_user_id
        category_id = invite.category_id
        question_count = invite.question_count

    message = {"type": "duel_invite_accepted" if accept else "duel_invite_declined", "invite_id": invite_id}
    if accept:
        message["by_user"] = responder_public

    delivered = await manager.send_to_user(from_user_id, message)

    # Duel faqat taklif yuboruvchi hozir ulangan bo'lsagina boshlanadi — aks holda hech kim savolni ololmaydi
    if accept and delivered:
        await duel_engine.start_duel(category_id, from_user_id, user.id, question_count)


async def expire_duel_invites_loop() -> None:
    """Fon rejimida — har 60 soniyada muddati o'tgan 'pending' takliflarni 'expired'ga o'tkazadi va ikkala tomonga xabar beradi."""
    while True:
        await asyncio.sleep(60)
        try:
            async with AsyncSessionLocal() as db:
                now = datetime.now(timezone.utc)
                result = await db.execute(
                    select(DuelInvite).where(DuelInvite.status == "pending", DuelInvite.expires_at <= now)
                )
                expired = result.scalars().all()
                expired_ids_and_users = [(inv.id, inv.from_user_id, inv.to_user_id) for inv in expired]
                for invite in expired:
                    invite.status = "expired"
                await db.commit()

            for invite_id, from_user_id, to_user_id in expired_ids_and_users:
                message = {"type": "duel_invite_expired", "invite_id": invite_id}
                await manager.send_to_user(from_user_id, message)
                await manager.send_to_user(to_user_id, message)
        except Exception:
            pass  # bitta xato butun tsiklni to'xtatmasin


@router.websocket("/duel")
async def duel_ws(websocket: WebSocket, token: str = Query(...)):
    user = await authenticate_ws(token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    manager.connect(user.id, websocket)

    try:
        await _deliver_pending_invites(user.id)

        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "duel_invite":
                await _handle_duel_invite(user, data, websocket)
            elif msg_type == "duel_invite_respond":
                await _handle_duel_invite_respond(user, data, websocket)
            elif msg_type == "duel_answer":
                await duel_engine.submit_answer(
                    user.id, data.get("duel_id"), data.get("question_index"), data.get("selected_option")
                )
            else:
                await websocket.send_json({"type": "error", "detail": f"Noma'lum xabar turi: {msg_type}"})
    except (WebSocketDisconnect, RuntimeError):
        # RuntimeError - Starlette ba'zan ulanish kutilmaganda uzilganda WebSocketDisconnect o'rniga
        # shuni chiqaradi ("WebSocket is not connected") - bu ham oddiy uzilish, xato emas
        pass
    finally:
        manager.disconnect(user.id, websocket)
