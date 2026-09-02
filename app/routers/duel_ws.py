import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.duel import DuelInvite
from app.models.friendship import Friendship
from app.models.notification import Notification
from app.models.quiz import Category, Question
from app.models.user import User
from app.services import duel_engine
from app.services.display_name import display_name
from app.services.push import send_push_to_user
from app.services.quiz_access import can_access_category
from app.services.ws_auth import authenticate_ws_connection
from app.services.ws_manager import manager

router = APIRouter()

# Duel taklifi spam-ga qarshi himoya - IP emas, user_id bo'yicha, xotirada
# saqlanadigan hisoblagich (app/admin.py'dagi login-lockout bilan bir xil
# naqsh - bitta backend instansiyasi uchun yetarli).
_MAX_INVITES_PER_MINUTE = 2
_INVITE_WINDOW_SECONDS = 60.0
_DECLINE_COOLDOWN_SECONDS = 5 * 60.0

_invite_timestamps: dict[str, list[float]] = defaultdict(list)
_decline_cooldown_until: dict[tuple[str, str], float] = {}


def _is_invite_rate_limited(from_user_id: str) -> bool:
    cutoff = time.monotonic() - _INVITE_WINDOW_SECONDS
    recent = [t for t in _invite_timestamps[from_user_id] if t > cutoff]
    _invite_timestamps[from_user_id] = recent
    return len(recent) >= _MAX_INVITES_PER_MINUTE


def _record_invite_sent(from_user_id: str) -> None:
    _invite_timestamps[from_user_id].append(time.monotonic())


def _is_in_decline_cooldown(from_user_id: str, to_user_id: str) -> bool:
    until = _decline_cooldown_until.get((from_user_id, to_user_id))
    return until is not None and time.monotonic() < until


def _start_decline_cooldown(from_user_id: str, to_user_id: str) -> None:
    _decline_cooldown_until[(from_user_id, to_user_id)] = time.monotonic() + _DECLINE_COOLDOWN_SECONDS


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

    if (
        not isinstance(client_invite_id, str)
        or not client_invite_id
        or not isinstance(to_user_id, str)
        or not to_user_id
        or not isinstance(category_id, int)
    ):
        await websocket.send_json({"type": "error", "detail": "duel_invite: maydonlar to'liq emas"})
        return

    if _is_invite_rate_limited(user.id):
        await websocket.send_json(
            {
                "type": "error",
                "detail": "Juda ko'p duel taklifi yubordingiz, biroz kutib qayta urinib ko'ring",
                "client_invite_id": client_invite_id,
            }
        )
        return

    if _is_in_decline_cooldown(user.id, to_user_id):
        await websocket.send_json(
            {
                "type": "error",
                "detail": "Bu foydalanuvchi taklifingizni yaqinda rad etdi, birozdan keyin qayta urinib ko'ring",
                "client_invite_id": client_invite_id,
            }
        )
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
        if (
            category is None
            or not category.is_active
            or not await can_access_category(db, user.id, category)
        ):
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
        db.add(Notification(user_id=to_user_id, kind="duel_challenge", related_user_id=user.id))
        await db.commit()
        await db.refresh(invite)
        _record_invite_sent(user.id)

        to_user = await db.get(User, to_user_id)
        if to_user is not None and to_user.duel_invites:
            await send_push_to_user(db, to_user_id, "Duel taklifi", f"{display_name(user)} sizni duelga chaqirdi")

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

    if not isinstance(invite_id, str) or not invite_id or not isinstance(accept, bool):
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

        if not accept:
            _start_decline_cooldown(invite.from_user_id, user.id)

        responder_public = _user_public(user)
        from_user_id = invite.from_user_id
        category_id = invite.category_id
        question_count = invite.question_count

    message = {"type": "duel_invite_accepted" if accept else "duel_invite_declined", "invite_id": invite_id}
    if accept:
        message["by_user"] = responder_public

    delivered = await manager.send_to_user(from_user_id, message)

    # Duel faqat taklif yuboruvchi hozir ulangan bo'lsagina boshlanadi — aks holda hech kim savolni ololmaydi.
    # Ikkala tomon ham hozircha boshqa duelda bo'lmasligi kerak — aks holda klient bitta joriy duel holatini
    # saqlaydi, ikkinchisi uni sezdirmasdan bosib o'tib, birinchi raqib hech narsa tushunmasdan tashlab
    # ketilgan bo'lardi (DuelGameScreen'ning 20 soniyalik "start failed" himoyasi bu holatni ham qamrab oladi).
    already_busy = duel_engine.is_user_in_active_duel(from_user_id) or duel_engine.is_user_in_active_duel(user.id)
    if accept and delivered and not already_busy:
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
async def duel_ws(websocket: WebSocket):
    await websocket.accept()
    user = await authenticate_ws_connection(websocket)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

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
                duel_id = data.get("duel_id")
                if not isinstance(duel_id, str):
                    await websocket.send_json({"type": "error", "detail": "duel_answer: duel_id noto'g'ri"})
                else:
                    await duel_engine.submit_answer(
                        user.id, duel_id, data.get("question_index"), data.get("selected_option")
                    )
            elif msg_type == "duel_leave":
                duel_id = data.get("duel_id")
                if isinstance(duel_id, str):
                    await duel_engine.forfeit_duel(user.id, duel_id)
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            else:
                await websocket.send_json({"type": "error", "detail": f"Noma'lum xabar turi: {msg_type}"})
    except (WebSocketDisconnect, RuntimeError):
        # RuntimeError - Starlette ba'zan ulanish kutilmaganda uzilganda WebSocketDisconnect o'rniga
        # shuni chiqaradi ("WebSocket is not connected") - bu ham oddiy uzilish, xato emas
        pass
    finally:
        manager.disconnect(user.id, websocket)
        # An abrupt disconnect (network drop, app killed) mid-duel must be
        # treated the same as an explicit `duel_leave` - otherwise the
        # opponent is left waiting forever on a side that's gone, with no
        # notification and no way for the duel to ever resolve.
        active_duel_id = duel_engine.get_active_duel_id(user.id)
        if active_duel_id is not None:
            await duel_engine.forfeit_duel(user.id, active_duel_id)
