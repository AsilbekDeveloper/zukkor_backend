from fastapi import WebSocket


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
            return False
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            conns.discard(ws)
        if not conns:
            del self.active[user_id]
        return True


manager = ConnectionManager()
