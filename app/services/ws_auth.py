import asyncio

import jwt
from fastapi import WebSocket

from app.core.database import AsyncSessionLocal
from app.core.security import decode_token
from app.models.user import User

# Ulanish accept qilingandan keyin, klient birinchi "auth" xabarini shuncha
# vaqt ichida yubormasa, ulanish yopiladi - aks holda hech qachon
# autentifikatsiya qilinmagan bo'sh ulanishlar cheksiz ochiq qolishi mumkin.
AUTH_MESSAGE_TIMEOUT_SECONDS = 10


async def authenticate_ws(token: str) -> User | None:
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        user_id = payload.get("sub")
    except jwt.PyJWTError:
        return None

    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            return None
        return user


async def authenticate_ws_connection(websocket: WebSocket) -> User | None:
    """`websocket.accept()`dan keyin chaqiriladi - token endi ulanish
    manzilida (`?token=...`) emas, ulanish ochilgach birinchi xabar
    sifatida (`{"type": "auth", "token": "..."}`) kutiladi. Sabab: URL'dagi
    token Render kabi platformalarning access log'iga to'liq so'rov manzili
    bilan birga yozilib qolishi mumkin edi - shu birinchi-xabar usuli
    tokenni hech qanday HTTP/WS so'rov manzilida ko'rsatmaydi.
    """
    try:
        data = await asyncio.wait_for(websocket.receive_json(), timeout=AUTH_MESSAGE_TIMEOUT_SECONDS)
    except Exception:
        return None

    if not isinstance(data, dict) or data.get("type") != "auth":
        return None
    token = data.get("token")
    if not isinstance(token, str):
        return None

    return await authenticate_ws(token)
