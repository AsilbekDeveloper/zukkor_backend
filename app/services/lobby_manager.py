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
from app.services.quiz_access import can_access_category
from app.services.scoring import calculate_ball
from app.services.streak import update_streak

logger = logging.getLogger("zukkor.ws")

MAX_PARTICIPANTS = 20
DEFAULT_TOTAL_QUESTIONS = 10
QUESTION_TIME_LIMIT_MS = 15000
SEND_TIMEOUT_SECONDS = 5
PRE_GAME_COUNTDOWN_SECONDS = 5
# Har savoldan keyin to'g'ri/noto'g'ri belgisi ekranda ko'rinib turishi
# uchun keyingi savolga o'tishdan oldingi minimal pauza - buni qo'shmasdan
# javob berilgan zahoti keyingi savol darhol translyatsiya qilinib, natija
# bir zumda almashtirilib ketardi.
REVEAL_PAUSE_SECONDS = 1.5


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

        # O'yin boshlanishida bir marta tanlab olinadi - barcha ishtirokchilar
        # aynan bir xil savollarni, bir xil tartibda ko'radi (mustaqil
        # tezlikda o'ynashsa ham natijalar adolatli solishtiriladi).
        # Har bir element: {"question_text", "shuffled_options", "correct_option"}
        self.questions: list[dict] = []

        # Har bir ishtirokchi o'zining mustaqil progressiga ega - endi hammasi
        # raqiblarini kutmasdan o'z tezligida oldinga siljiydi.
        self.participant_index: dict[str, int] = {pid: -1 for pid in participant_user_ids}
        self.participant_sent_at: dict[str, datetime] = {}
        self.participant_finished: dict[str, bool] = {pid: False for pid in participant_user_ids}
        # Javob qayd etilgandan keyin, keyingi savolga o'tishdan oldingi 1.5s
        # reveal-pauza paytida lock bo'shatiladi (aks holda boshqalar shu payt
        # bloklanib qolardi) - shu oraliqda kelgan takroriy/kechikkan javobni
        # ushlab qolish uchun.
        self.participant_pending_advance: set[str] = set()
        self.participant_timeout_task: dict[str, asyncio.Task] = {}

        # participant_id -> har savol uchun bitta {"elapsed_ms", "is_correct"} yozuvi
        self.answers_log: dict[str, list[dict]] = {pid: [] for pid in participant_user_ids}

        self.lock = asyncio.Lock()
        # Barcha ishtirokchilar barcha savollarini tugatgach True bo'ladi -
        # `_finish_game` faqat bir marta chaqirilishini kafolatlaydi.
        self.finished = False


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

    if room.game is not None:
        await websocket.send_json({"type": "lobby_join_error", "reason": "already_started"})
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
        if room.game is not None:
            for task in room.game.participant_timeout_task.values():
                task.cancel()
        for p in list(room.participants.values()):
            await _safe_send(p.websocket, {"type": "lobby_closed", "room_id": room_id})
        _rooms.pop(room_id, None)
        _room_code_index.pop(room.room_code, None)
    elif room.participants:
        await _broadcast_room_update(room)


# ---------------------------------------------------------------------------
# Phase 2 - mustaqil tezlikdagi ko'p o'yinchili viktorina
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

    host_user_id = room.participants[host_participant_id].user.id

    async with AsyncSessionLocal() as db:
        category = await db.get(Category, category_id)
        if (
            category is None
            or not category.is_active
            or not await can_access_category(db, host_user_id, category)
        ):
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

        used_question_ids: list[int] = []
        questions_data: list[dict] = []
        for _ in range(actual_total):
            question = await _pick_question(db, category_id, used_question_ids)
            used_question_ids.append(question.id)

            option_order = random.sample(range(len(question.options)), len(question.options))
            shuffled_options = [question.options[j] for j in option_order]
            correct_option = option_order.index(question.correct_option_index)

            questions_data.append(
                {
                    "question_text": question.question_text,
                    "shuffled_options": shuffled_options,
                    "correct_option": correct_option,
                }
            )

    participant_user_ids = {pid: p.user.id for pid, p in room.participants.items()}
    room.game = _GameState(room_id, category_id, actual_total, participant_user_ids)
    room.game.questions = questions_data

    await _broadcast(
        room,
        {
            "type": "lobby_game_started",
            "room_id": room_id,
            "category": category_summary,
            "total_questions": actual_total,
        },
    )

    # Har bir klient mos 5 soniyalik "5,4,3,2,1" countdown ko'rsatadi - shu
    # bilan sinxron turish uchun birinchi savol shu qadar kechiktiriladi.
    await asyncio.sleep(PRE_GAME_COUNTDOWN_SECONDS)

    async with room.game.lock:
        for pid in list(room.game.participant_user_ids):
            await _send_question_to_participant(room, pid, 0)


async def _send_question_to_participant(room: _Room, participant_id: str, index: int) -> None:
    game = room.game
    game.participant_index[participant_id] = index
    game.participant_sent_at[participant_id] = datetime.now(timezone.utc)
    q = game.questions[index]

    participant = room.participants.get(participant_id)
    if participant is not None:
        await _safe_send(
            participant.websocket,
            {
                "type": "lobby_question",
                "room_id": room.room_id,
                "question_index": index,
                "question": {
                    "text": q["question_text"],
                    "options": q["shuffled_options"],
                    # Ataylab (2026-08-26) - klient tanlangan zahoti to'g'ri/
                    # noto'g'rini serverga murojaat qilmasdan ko'rsatishi uchun.
                    "correct_option": q["correct_option"],
                    "time_limit_ms": QUESTION_TIME_LIMIT_MS,
                },
            },
        )

    old_task = game.participant_timeout_task.get(participant_id)
    if old_task:
        old_task.cancel()
    game.participant_timeout_task[participant_id] = asyncio.create_task(
        _participant_timeout(room, participant_id, index)
    )


async def _participant_timeout(room: _Room, participant_id: str, index: int) -> None:
    try:
        await asyncio.sleep(QUESTION_TIME_LIMIT_MS / 1000)
    except asyncio.CancelledError:
        return
    if room.game is None:
        return
    await _process_participant_answer(room, participant_id, index, None)


async def submit_answer(room_id: str, participant_id: str, question_index, selected_option) -> None:
    room = _rooms.get(room_id)
    if room is None or room.game is None or participant_id not in room.participants:
        return
    if not isinstance(question_index, int):
        return
    # `selected_option` bazaga Integer ustunga yoziladi - klient noto'g'ri
    # turdagi qiymat (masalan dict/list) yuborsa, tekshirmasdan o'tkazib
    # yuborilsa DB darajasida (asyncpg) tur xatosi bilan connection yiqilardi.
    if selected_option is not None and not isinstance(selected_option, int):
        return
    await _process_participant_answer(room, participant_id, question_index, selected_option)


async def _process_participant_answer(
    room: _Room, participant_id: str, question_index: int, selected_option
) -> None:
    game = room.game
    if game is None:
        return

    async with game.lock:
        if game.finished:
            return  # o'yin allaqachon yakunlangan - kechikkan javob e'tiborsiz
        if game.participant_finished.get(participant_id):
            return
        if participant_id in game.participant_pending_advance:
            return  # bu savol uchun javob allaqachon qayd etilgan, reveal-pauza tugashini kutmoqda
        if question_index != game.participant_index.get(participant_id):
            return  # eskirgan/xato javob - e'tiborga olinmaydi

        game.participant_pending_advance.add(participant_id)
        await _handle_participant_finished_question(room, participant_id, question_index, selected_option)

    await asyncio.sleep(REVEAL_PAUSE_SECONDS)

    async with game.lock:
        game.participant_pending_advance.discard(participant_id)
        if game.finished:
            return

        next_index = question_index + 1
        if next_index < game.total_questions:
            await _send_question_to_participant(room, participant_id, next_index)
        else:
            game.participant_finished[participant_id] = True
            participant = room.participants.get(participant_id)
            if participant is not None:
                await _safe_send(participant.websocket, {"type": "lobby_waiting_for_others", "room_id": room.room_id})

            if all(game.participant_finished.get(pid, False) for pid in game.participant_user_ids):
                await _finish_game(room)


async def _handle_participant_finished_question(
    room: _Room, participant_id: str, index: int, selected_option
) -> None:
    """`game.lock` ostida chaqiriladi - shu bitta ishtirokchining javobini yozib, shaxsiy natijasini yuboradi."""
    game = room.game
    q = game.questions[index]
    sent_at = game.participant_sent_at[participant_id]
    now = datetime.now(timezone.utc)
    elapsed_ms = round((now - sent_at).total_seconds() * 1000)
    is_correct = selected_option is not None and selected_option == q["correct_option"]

    task = game.participant_timeout_task.pop(participant_id, None)
    # Agar shu funksiya aynan shu taymer vazifasi ichidan chaqirilgan bo'lsa (vaqt tugab),
    # `task` bu holda joriy ishlayotgan vazifaning o'zi - uni bekor qilish keyingi
    # `await`da o'z-o'zini CancelledError bilan to'xtatib qo'yardi.
    if task is not None and task is not asyncio.current_task():
        task.cancel()

    game.answers_log[participant_id].append({"elapsed_ms": elapsed_ms, "is_correct": is_correct})

    participant = room.participants.get(participant_id)
    if participant is not None:
        await _safe_send(
            participant.websocket,
            {
                "type": "lobby_question_result",
                "room_id": room.room_id,
                "question_index": index,
                "correct_option": q["correct_option"],
                "your_selected_option": selected_option,
                "your_correct": is_correct,
            },
        )


def _score_for(game: _GameState, participant_id: str) -> tuple[int, int, int]:
    log = game.answers_log[participant_id]
    correct = sum(1 for a in log if a["is_correct"])
    total_time_ms = sum(a["elapsed_ms"] for a in log)
    ball = sum(calculate_ball(a["elapsed_ms"], QUESTION_TIME_LIMIT_MS, a["is_correct"]) for a in log)
    return correct, total_time_ms, ball


async def _finish_game(room: _Room) -> None:
    game = room.game
    # `game.lock` ostida (chaqiruvchi orqali) - ikkinchi marta chaqirilishining oldini oladi.
    game.finished = True
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

    def _breakdown_for(pid: str) -> list[dict]:
        return [
            {
                "order": i,
                "question_text": game.questions[i]["question_text"],
                "is_correct": game.answers_log[pid][i]["is_correct"],
            }
            for i in range(game.total_questions)
        ]

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
            if user is None:
                # Account was deleted mid-game - `LobbyGameResult.user_id`
                # is a hard FK, so inserting a row for it would raise an
                # IntegrityError at commit time and roll back every OTHER
                # participant's XP/result too. Skip this participant
                # entirely rather than let one deleted account cost
                # everyone else their earned XP for the round.
                continue

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
                        "breakdown": _breakdown_for(participant_id),
                    },
                )

        await db.commit()

    room.game = None
