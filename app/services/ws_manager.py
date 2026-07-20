import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger("zukkor.ws")

SEND_TIMEOUT_SECONDS = 5


class ConnectionManager:
    def __init__(self) -> None:
        self.active: dict[str, set[WebSocket]] = {}

    def connect(self, user_id: str, websocket: WebSocket) -> None:
        self.active.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        conns = self.active.get(user_id)
        if not conns:
            return
        conns.discard(websocket)
        if not conns:
            del self.active[user_id]

    async def send_to_user(self, user_id: str, message: dict) -> bool:
        conns = self.active.get(user_id)
        if not conns:
            logger.info("send_to_user: %s uchun faol ulanish yo'q", user_id)
            return False

        dead: list[WebSocket] = []
        delivered_count = 0
        for ws in list(conns):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=SEND_TIMEOUT_SECONDS)
                delivered_count += 1
            except Exception as e:
                logger.warning(
                    "send_to_user: %s uchun bitta socket'ga yuborib bo'lmadi (%s) - o'lik deb belgilanadi",
                    user_id,
                    e,
                )
                dead.append(ws)

        for ws in dead:
            conns.discard(ws)
        if not conns:
            del self.active[user_id]

        if delivered_count == 0:
            logger.info(
                "send_to_user: %s uchun %d ta ulanish bor edi, lekin hech biriga yetkazilmadi",
                user_id,
                len(dead),
            )

        return delivered_count > 0


manager = ConnectionManager()
