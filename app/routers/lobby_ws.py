from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.services import lobby_manager
from app.services.ws_auth import authenticate_ws

router = APIRouter()


@router.websocket("/lobby")
async def lobby_ws(websocket: WebSocket, token: str = Query(...)):
    user = await authenticate_ws(token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    current_room_id: str | None = None
    current_participant_id: str | None = None

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "lobby_create":
                if current_room_id is not None:
                    await websocket.send_json(
                        {"type": "error", "detail": "Avval joriy xonadan chiqing"}
                    )
                    continue
                current_room_id, current_participant_id = await lobby_manager.create_room(user, websocket)

            elif msg_type == "lobby_join":
                if current_room_id is not None:
                    await websocket.send_json(
                        {"type": "error", "detail": "Avval joriy xonadan chiqing"}
                    )
                    continue
                room_code = data.get("room_code")
                if not room_code:
                    await websocket.send_json({"type": "error", "detail": "lobby_join: room_code kerak"})
                    continue
                result = await lobby_manager.join_room(room_code, user, websocket)
                if result is not None:
                    current_room_id, current_participant_id = result

            elif msg_type == "lobby_leave":
                if current_room_id and current_participant_id:
                    await lobby_manager.leave_room(current_room_id, current_participant_id)
                    current_room_id, current_participant_id = None, None

            elif msg_type == "lobby_start":
                if not current_room_id or not current_participant_id:
                    await websocket.send_json({"type": "error", "detail": "Siz hech qanday xonada emassiz"})
                    continue
                category_id = data.get("category_id")
                if not category_id:
                    await websocket.send_json({"type": "error", "detail": "lobby_start: category_id kerak"})
                    continue
                await lobby_manager.start_game(
                    current_room_id, current_participant_id, category_id, data.get("question_count"), websocket
                )

            elif msg_type == "lobby_answer":
                if not current_room_id or not current_participant_id:
                    continue
                await lobby_manager.submit_answer(
                    current_room_id,
                    current_participant_id,
                    data.get("question_index"),
                    data.get("selected_option"),
                    websocket,
                )

            else:
                await websocket.send_json({"type": "error", "detail": f"Noma'lum xabar turi: {msg_type}"})
    except (WebSocketDisconnect, RuntimeError):
        # RuntimeError - Starlette ba'zan ulanish kutilmaganda uzilganda WebSocketDisconnect o'rniga
        # shuni chiqaradi ("WebSocket is not connected") - bu ham oddiy uzilish, xato emas
        pass
    finally:
        if current_room_id and current_participant_id:
            await lobby_manager.leave_room(current_room_id, current_participant_id)
