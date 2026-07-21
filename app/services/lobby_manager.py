import asyncio
import logging
import random
import string
import uuid
from datetime import datetime, timezone

from fastapi import WebSocket
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.lobby_game import LobbyGame, LobbyGameResult
from app.models.quiz import Category, Question
from app.models.user import User
from app.models.xp_event import XpEvent
from app.services.scoring import calculate_ball
from app.services.streak import update_streak

logger = logging.getLogger("zukkor.ws")

MAX_PARTICIPANTS = 20
DEFAULT_TOTAL_QUESTIONS = 10
QUESTION_TIME_LIMIT_MS = 15000
SEND_TIMEOUT_SECONDS = 5


async def _safe_send(websocket: WebSocket, message: dict) -> bool:
    """Osilib qolgan/o'lik ulanish boshqalarga yuborishni bloklamasin deb timeout bilan yuboradi."""
    try:
        await asyncio.wait_for(websocket.send_json(message), timeout=SEND_TIMEOUT_SECONDS)
        return True
    except Exception as e:
        logger.warning("lobby: bitta socket'ga yuborib bo'lmadi (%s)", e)
        return False


class _Participant:
    def __init__(self, participant_id: str, user: User, websocket: WebSocket, is_host: bool):
        self.participant_id = participant_id
        self.user = user
        self.websocket = websocket
        self.is_host = is_host


class _GameState:
    def __init__(self, room_id: str, category_id: int, total_questions: int, participant_user_ids: dict[str, str]):
        self.room_id = room_id
        self.category_id = category_id
        self.total_questions = total_questions
        # o'yin boshlanganda kim ishtirok etgani (keyinroq chiqib ketsa ham standings'da qoladi)
        self.participant_user_ids = dict(participant_user_ids)  # participant_id -> user_id

        self.current_index = -1
        self.current_question_id: int | None = None
        self.current_option_order: list[int] | None = None
        self.current_correct_option: int | None = None
        self.current_broadcast_at: datetime | None = None
        self.used_question_ids: list[int] = []

        # participant_id -> har savol uchun bitta {"elapsed_ms", "is_correct"} yozuvi (Duel'dagi bilan bir xil
        # granularity - ball formulasi har savolni alohida hisoblaydi, keyin jamlanadi)
        self.answers_log: dict[str, list[dict]] = {pid: [] for pid in participant_user_ids}
        # joriy savol uchun kim javob berganini kuzatish
        self.current_answered: set[str] = set()

        self.timeout_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()


class _Room:
    def __init__(self, room_id: str, room_code: str, host_participant_id: str):
        self.room_id = room_id
        self.room_code = room_code
        self.host_participant_id = host_participant_id
        self.participants: dict[str, _Participant] = {}
        self.game: _GameState | None = None


_rooms: dict[str, _Room] = {}
_room_code_index: dict[str, str] = {}


def _generate_room_code() -> str:
    while True:
        code = "".join(random.choices(string.digits, k=6))
        if code not in _room_code_index:
            return code


def _participant_public(p: _Participant) -> dict:
    return {
        "id": p.participant_id,
        "username": p.user.username,
        "first_name": p.user.first_name,
        "last_name": p.user.last_name,
        "avatar_color": p.user.avatar_color,
        "avatar_image_path": p.user.avatar_image_path,
        "is_host": p.is_host,
    }


async def _broadcast(room: _Room, message: dict) -> None:
    for p in list(room.participants.values()):
        await _safe_send(p.websocket, message)


async def _broadcast_room_update(room: _Room) -> None:
    participants_public = [_participant_public(p) for p in room.participants.values()]
    for p in list(room.participants.values()):
        await _safe_send(
            p.websocket,
            {
                "type": "lobby_room_update",
                "room_id": room.room_id,
                "room_code": room.room_code,
                "you_participant_id": p.participant_id,
                "participants": participants_public,
            },
        )


async def create_room(user: User, websocket: WebSocket) -> tuple[str, str]:
    room_id = str(uuid.uuid4())
    room_code = _generate_room_code()
    participant_id = str(uuid.uuid4())

    room = _Room(room_id, room_code, host_participant_id=participant_id)
    room.participants[participant_id] = _Participant(participant_id, user, websocket, is_host=True)

    _rooms[room_id] = room
    _room_code_index[room_code] = room_id

    await _broadcast_room_update(room)
    return room_id, participant_id


async def join_room(room_code: str, user: User, websocket: WebSocket) -> tuple[str, str] | None:
    room_id = _room_code_index.get(room_code)
    room = _rooms.get(room_id) if room_id else None

    if room is None:
        await websocket.send_json({"type": "lobby_join_error", "reason": "not_found"})
        return None

    if len(room.participants) >= MAX_PARTICIPANTS:
        await websocket.send_json({"type": "lobby_join_error", "reason": "room_full"})
        return None

    participant_id = str(uuid.uuid4())
    room.participants[participant_id] = _Participant(participant_id, user, websocket, is_host=False)

    await _broadcast_room_update(room)
    return room.room_id, participant_id


async def leave_room(room_id: str, participant_id: str) -> None:
    room = _rooms.get(room_id)
    if room is None or participant_id not in room.participants:
        return

    participant = room.participants.pop(participant_id)

    if participant.is_host:
        if room.game and room.game.timeout_task:
            room.game.timeout_task.cancel()
        for p in list(room.participants.values()):
            await _safe_send(p.websocket, {"type": "lobby_closed", "room_id": room_id})
        _rooms.pop(room_id, None)
        _room_code_index.pop(room.room_code, None)
    elif room.participants:
        await _broadcast_room_update(room)


# ---------------------------------------------------------------------------
# Phase 2 - sinxron ko'p o'yinchili viktorina
# ---------------------------------------------------------------------------


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


async def _pick_question(db, category_id: int, exclude_ids: list[int]) -> Question | None:
    stmt = select(Question).where(Question.category_id == category_id, Question.is_active.is_(True))
    if exclude_ids:
        stmt = stmt.where(Question.id.notin_(exclude_ids))
    stmt = stmt.order_by(func.random()).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def start_game(
    room_id: str, host_participant_id: str, category_id: int, question_count: int | None, websocket: WebSocket
) -> None:
    room = _rooms.get(room_id)
    if room is None:
        return
    if room.host_participant_id != host_participant_id:
        await websocket.send_json({"type": "error", "detail": "Faqat xost o'yinni boshlashi mumkin"})
        return
    if room.game is not None:
        await websocket.send_json({"type": "error", "detail": "O'yin allaqachon boshlangan"})
        return

    total_requested = question_count or DEFAULT_TOTAL_QUESTIONS

    async with AsyncSessionLocal() as db:
        category = await db.get(Category, category_id)
        if category is None or not category.is_active:
            await websocket.send_json({"type": "error", "detail": "Kategoriya topilmadi"})
            return

        count_result = await db.execute(
            select(func.count()).select_from(Question).where(
                Question.category_id == category_id, Question.is_active.is_(True)
            )
        )
        available = count_result.scalar_one()
        if available == 0:
            await websocket.send_json({"type": "error", "detail": "Bu kategoriyada savollar yo'q"})
            return

        actual_total = min(total_requested, available)
        category_summary = await _category_summary(db, category)

    participant_user_ids = {pid: p.user.id for pid, p in room.participants.items()}
    room.game = _GameState(room_id, category_id, actual_total, participant_user_ids)

    await _broadcast(
        room,
        {
            "type": "lobby_game_started",
            "room_id": room_id,
            "category": category_summary,
            "total_questions": actual_total,
        },
    )

    async with room.game.lock:
        await _advance_to_next_question(room)


async def _advance_to_next_question(room: _Room) -> None:
    game = room.game
    game.current_index += 1
    game.current_answered = set()

    async with AsyncSessionLocal() as db:
        question = await _pick_question(db, game.category_id, game.used_question_ids)
        game.used_question_ids.append(question.id)

    option_order = random.sample(range(len(question.options)), len(question.options))
    shuffled_options = [question.options[i] for i in option_order]

    game.current_question_id = question.id
    game.current_option_order = option_order
    game.current_correct_option = option_order.index(question.correct_option_index)
    game.current_broadcast_at = datetime.now(timezone.utc)

    await _broadcast(
        room,
        {
            "type": "lobby_question",
            "room_id": room.room_id,
            "question_index": game.current_index,
            "question": {
                "text": question.question_text,
                "options": shuffled_options,
                "time_limit_ms": QUESTION_TIME_LIMIT_MS,
            },
        },
    )

    if game.timeout_task:
        game.timeout_task.cancel()
    game.timeout_task = asyncio.create_task(_question_timeout(room, game.current_index))


async def _question_timeout(room: _Room, question_index: int) -> None:
    try:
        await asyncio.sleep(QUESTION_TIME_LIMIT_MS / 1000)
    except asyncio.CancelledError:
        return

    game = room.game
    if game is None:
        return
    async with game.lock:
        if game.current_index != question_index:
            return
        await _resolve_question(room)


async def submit_answer(room_id: str, participant_id: str, question_index, selected_option, websocket: WebSocket) -> None:
    room = _rooms.get(room_id)
    if room is None or room.game is None or participant_id not in room.participants:
        return
    game = room.game
    if not isinstance(question_index, int):
        return

    async with game.lock:
        if question_index != game.current_index:
            return  # eskirgan/xato javob - e'tiborga olinmaydi
        if participant_id in game.current_answered:
            return  # bitta ishtirokchidan bitta javobdan ortig'i e'tiborga olinmaydi

        now = datetime.now(timezone.utc)
        elapsed_ms = round((now - game.current_broadcast_at).total_seconds() * 1000)
        is_correct = selected_option is not None and selected_option == game.current_correct_option

        game.current_answered.add(participant_id)
        game.answers_log[participant_id].append({"elapsed_ms": elapsed_ms, "is_correct": is_correct})

        await websocket.send_json(
            {
                "type": "lobby_question_result",
                "room_id": room_id,
                "question_index": question_index,
                "correct_option": game.current_correct_option,
                "your_selected_option": selected_option,
                "your_correct": is_correct,
            }
        )

        answered_count = len(game.current_answered)
        total_count = len(room.participants)
        await _broadcast(
            room,
            {
                "type": "lobby_answer_progress",
                "room_id": room_id,
                "question_index": question_index,
                "answered_count": answered_count,
                "total_count": total_count,
            },
        )

        if answered_count >= total_count:
            if game.timeout_task:
                game.timeout_task.cancel()
            await _resolve_question(room)


async def _resolve_question(room: _Room) -> None:
    game = room.game

    # Javob bermagan ishtirokchilar (chiqib ketganlar ham) uchun "javobsiz" yozuv qo'shiladi -
    # har kimning log uzunligi = shu paytgacha o'tgan savollar soniga teng bo'lib qoladi
    for pid in game.participant_user_ids:
        if pid not in game.current_answered:
            game.answers_log[pid].append({"elapsed_ms": QUESTION_TIME_LIMIT_MS, "is_correct": False})

    if game.current_index + 1 < game.total_questions:
        await _advance_to_next_question(room)
    else:
        await _finish_game(room)


def _score_for(game: _GameState, participant_id: str) -> tuple[int, int, int]:
    log = game.answers_log[participant_id]
    correct = sum(1 for a in log if a["is_correct"])
    total_time_ms = sum(a["elapsed_ms"] for a in log)
    ball = sum(calculate_ball(a["elapsed_ms"], QUESTION_TIME_LIMIT_MS, a["is_correct"]) for a in log)
    return correct, total_time_ms, ball


async def _finish_game(room: _Room) -> None:
    game = room.game
    now = datetime.now(timezone.utc)

    scores = {pid: _score_for(game, pid) for pid in game.participant_user_ids}

    standings = sorted(
        (
            {"participant_id": pid, "correct": correct, "total": game.total_questions, "total_time_ms": total_time_ms}
            for pid, (correct, total_time_ms, _ball) in scores.items()
        ),
        key=lambda s: (-s["correct"], s["total_time_ms"]),
    )
    ranks = {s["participant_id"]: i + 1 for i, s in enumerate(standings)}
    participant_count = len(game.participant_user_ids)

    async with AsyncSessionLocal() as db:
        lobby_game = LobbyGame(
            category_id=game.category_id,
            total_questions=game.total_questions,
            participant_count=participant_count,
        )
        db.add(lobby_game)
        await db.flush()  # lobby_game.id (server-generated) - keyingi FK yozuvlar uchun kerak

        for participant_id, user_id in game.participant_user_ids.items():
            correct, total_time_ms, ball = scores[participant_id]
            xp = round(ball / 100)

            user = await db.get(User, user_id)
            if user is not None:
                user.total_xp += xp
                user.games_played += 1
                update_streak(user, now)
                db.add(XpEvent(user_id=user_id, amount=xp))

            db.add(
                LobbyGameResult(
                    lobby_game_id=lobby_game.id,
                    user_id=user_id,
                    rank=ranks[participant_id],
                    correct=correct,
                    total_time_ms=total_time_ms,
                    ball=ball,
                    xp=xp,
                )
            )

            participant = room.participants.get(participant_id)
            if participant is not None:
                await _safe_send(
                    participant.websocket,
                    {
                        "type": "lobby_game_finished",
                        "room_id": room.room_id,
                        "standings": standings,
                        "xp_earned": xp,
                        "ball_earned": ball,
                    },
                )

        await db.commit()

    room.game = None
