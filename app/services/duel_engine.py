import asyncio
import random
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.duel import Duel, DuelAnswer, DuelQuestion
from app.models.quiz import Category, Question
from app.models.user import User
from app.models.xp_event import XpEvent
from app.services.xp_award import compute_xp_eligible_ball
from app.services.scoring import calculate_ball
from app.services.streak import update_streak
from app.services.ws_manager import manager

DEFAULT_TOTAL_QUESTIONS = 10
QUESTION_TIME_LIMIT_MS = 15000
PRE_GAME_COUNTDOWN_SECONDS = 5
# Har savoldan keyin to'g'ri/noto'g'ri belgisi ekranda ko'rinib turishi
# uchun keyingi savolga o'tishdan oldingi minimal pauza - buni qo'shmasdan
# javob berilgan zahoti keyingi savol darhol translyatsiya qilinib, natija
# bir zumda almashtirilib ketardi. Solo quiz'dagi xuddi shu maqsaddagi
# pauza (900ms) bilan bir xil - uch rejimda ham izchil, imkon qadar tez
# (2026-08-26, foydalanuvchi so'rovi bilan 1.5s'dan tushirildi).
REVEAL_PAUSE_SECONDS = 0.9


class _ActiveDuel:
    def __init__(self, duel_id: str, user_a_id: str, user_b_id: str, category_id: int, total_questions: int):
        self.duel_id = duel_id
        self.user_a_id = user_a_id
        self.user_b_id = user_b_id
        self.category_id = category_id
        self.total_questions = total_questions

        # Duel boshlanishida bir marta tanlab olinadi - ikkala o'yinchi ham
        # aynan bir xil savollarni, bir xil tartibda ko'radi (mustaqil
        # tezlikda o'ynashsa ham natijalar adolatli solishtiriladi).
        # Har bir element: {"duel_question_id", "question_text", "shuffled_options", "correct_option"}
        self.questions: list[dict] = []

        # Har bir user o'zining mustaqil progressiga ega - endi ikkovi ham
        # raqibini kutmasdan o'z tezligida oldinga siljiydi.
        self.user_index: dict[str, int] = {user_a_id: -1, user_b_id: -1}
        self.user_sent_at: dict[str, datetime] = {}
        self.user_finished: dict[str, bool] = {user_a_id: False, user_b_id: False}
        # Javob qayd etilgandan keyin, keyingi savolga o'tishdan oldingi 1.5s
        # reveal-pauza paytida lock bo'shatiladi (aks holda ikkinchi o'yinchi
        # shu payt bloklanib qolardi) - shu oraliqda kelgan takroriy/kechikkan
        # javobni ushlab qolish uchun.
        self.user_pending_advance: set[str] = set()
        self.user_timeout_task: dict[str, asyncio.Task] = {}

        self.lock = asyncio.Lock()
        # Ikkala o'yinchi ham barcha savollarini tugatgach True bo'ladi -
        # `_finish_duel` faqat bir marta chaqirilishini kafolatlaydi.
        self.finished = False


_active_duels: dict[str, _ActiveDuel] = {}
# user_id -> duel_id, kept in lockstep with `_active_duels` — a client only
# ever tracks a single `DuelGameState`, so if a user were let into a second
# concurrent duel it would silently overwrite the first one client-side,
# abandoning whichever opponent isn't in the newest duel with no explanation.
_user_active_duel: dict[str, str] = {}


def is_user_in_active_duel(user_id: str) -> bool:
    return user_id in _user_active_duel


def _other_user_id(state: _ActiveDuel, user_id: str) -> str:
    return state.user_b_id if user_id == state.user_a_id else state.user_a_id


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


async def _pick_question(db, category_id: int, exclude_ids: list[int]) -> Question | None:
    stmt = select(Question).where(Question.category_id == category_id, Question.is_active.is_(True))
    if exclude_ids:
        stmt = stmt.where(Question.id.notin_(exclude_ids))
    stmt = stmt.order_by(func.random()).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def start_duel(category_id: int, user_a_id: str, user_b_id: str, question_count: int | None) -> None:
    total_requested = question_count or DEFAULT_TOTAL_QUESTIONS

    async with AsyncSessionLocal() as db:
        category = await db.get(Category, category_id)

        count_result = await db.execute(
            select(func.count()).select_from(Question).where(
                Question.category_id == category_id, Question.is_active.is_(True)
            )
        )
        available = count_result.scalar_one()
        if available == 0:
            return  # kategoriyada savol yo'q - duel boshlanmaydi

        actual_total = min(total_requested, available)

        duel = Duel(
            category_id=category_id,
            user_a_id=user_a_id,
            user_b_id=user_b_id,
            total_questions=actual_total,
            status="in_progress",
        )
        db.add(duel)
        await db.flush()  # duel.id kerak - DuelQuestion FK uchun

        used_question_ids: list[int] = []
        questions_data: list[dict] = []
        dq_objects: list[DuelQuestion] = []
        for i in range(actual_total):
            question = await _pick_question(db, category_id, used_question_ids)
            used_question_ids.append(question.id)

            option_order = random.sample(range(len(question.options)), len(question.options))
            shuffled_options = [question.options[j] for j in option_order]
            correct_option = option_order.index(question.correct_option_index)

            dq = DuelQuestion(
                duel_id=duel.id,
                question_id=question.id,
                order=i,
                option_order=option_order,
                time_limit_ms=QUESTION_TIME_LIMIT_MS,
            )
            db.add(dq)
            dq_objects.append(dq)
            questions_data.append(
                {
                    "question_text": question.question_text,
                    "shuffled_options": shuffled_options,
                    "correct_option": correct_option,
                }
            )

        await db.flush()  # dq.id larni to'ldirish uchun (autoincrement, FK sifatida keyin kerak)
        for i, dq in enumerate(dq_objects):
            questions_data[i]["duel_question_id"] = dq.id

        await db.commit()

        user_a = await db.get(User, user_a_id)
        user_b = await db.get(User, user_b_id)
        category_summary = await _category_summary(db, category)

    state = _ActiveDuel(duel.id, user_a_id, user_b_id, category_id, actual_total)
    state.questions = questions_data
    _active_duels[duel.id] = state
    _user_active_duel[user_a_id] = duel.id
    _user_active_duel[user_b_id] = duel.id

    await manager.send_to_user(
        user_a_id,
        {
            "type": "duel_started",
            "duel_id": duel.id,
            "category": category_summary,
            "total_questions": actual_total,
            "opponent": _user_public(user_b),
        },
    )
    await manager.send_to_user(
        user_b_id,
        {
            "type": "duel_started",
            "duel_id": duel.id,
            "category": category_summary,
            "total_questions": actual_total,
            "opponent": _user_public(user_a),
        },
    )

    # Ikkala klient ham mos 5 soniyalik "5,4,3,2,1" countdown ko'rsatadi -
    # shu bilan sinxron turish uchun birinchi savol shu qadar kechiktiriladi.
    await asyncio.sleep(PRE_GAME_COUNTDOWN_SECONDS)

    async with state.lock:
        await _send_question_to_user(state, user_a_id, 0)
        await _send_question_to_user(state, user_b_id, 0)


async def _send_question_to_user(state: _ActiveDuel, user_id: str, index: int) -> None:
    state.user_index[user_id] = index
    state.user_sent_at[user_id] = datetime.now(timezone.utc)
    q = state.questions[index]

    await manager.send_to_user(
        user_id,
        {
            "type": "duel_question",
            "duel_id": state.duel_id,
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

    other_id = _other_user_id(state, user_id)
    if not state.user_finished.get(other_id, False):
        await manager.send_to_user(
            other_id,
            {"type": "duel_opponent_progress", "duel_id": state.duel_id, "opponent_question_index": index},
        )

    old_task = state.user_timeout_task.get(user_id)
    if old_task:
        old_task.cancel()
    state.user_timeout_task[user_id] = asyncio.create_task(_user_timeout(state, user_id, index))


async def _user_timeout(state: _ActiveDuel, user_id: str, index: int) -> None:
    try:
        await asyncio.sleep(QUESTION_TIME_LIMIT_MS / 1000)
    except asyncio.CancelledError:
        return
    if state.duel_id not in _active_duels:
        return
    await _process_user_answer(state, user_id, index, None)


async def submit_answer(user_id: str, duel_id: str, question_index, selected_option) -> None:
    state = _active_duels.get(duel_id)
    if state is None or user_id not in (state.user_a_id, state.user_b_id):
        return
    if not isinstance(question_index, int):
        return
    # `selected_option` bazaga Integer ustunga yoziladi - klient noto'g'ri
    # turdagi qiymat (masalan dict/list) yuborsa, tekshirmasdan o'tkazib
    # yuborilsa DB darajasida (asyncpg) tur xatosi bilan connection yiqilardi.
    if selected_option is not None and not isinstance(selected_option, int):
        return
    await _process_user_answer(state, user_id, question_index, selected_option)


async def _process_user_answer(state: _ActiveDuel, user_id: str, question_index: int, selected_option) -> None:
    async with state.lock:
        if state.finished:
            return  # duel allaqachon yakunlangan - kechikkan javob e'tiborsiz
        if state.user_finished.get(user_id):
            return
        if user_id in state.user_pending_advance:
            return  # bu savol uchun javob allaqachon qayd etilgan, reveal-pauza tugashini kutmoqda
        if question_index != state.user_index.get(user_id):
            return  # eskirgan/xato javob - e'tiborga olinmaydi

        state.user_pending_advance.add(user_id)
        await _handle_user_finished_question(state, user_id, question_index, selected_option)

    await asyncio.sleep(REVEAL_PAUSE_SECONDS)

    async with state.lock:
        state.user_pending_advance.discard(user_id)
        if state.finished:
            return

        next_index = question_index + 1
        if next_index < state.total_questions:
            await _send_question_to_user(state, user_id, next_index)
        else:
            state.user_finished[user_id] = True
            other_id = _other_user_id(state, user_id)
            if state.user_finished.get(other_id):
                await _finish_duel(state)
            else:
                await manager.send_to_user(user_id, {"type": "duel_waiting_for_opponent", "duel_id": state.duel_id})


async def _handle_user_finished_question(state: _ActiveDuel, user_id: str, index: int, selected_option) -> None:
    """`state.lock` ostida chaqiriladi - shu bitta userning javobini yozib, shaxsiy natijasini yuboradi."""
    q = state.questions[index]
    sent_at = state.user_sent_at[user_id]
    now = datetime.now(timezone.utc)
    elapsed_ms = round((now - sent_at).total_seconds() * 1000)
    is_correct = selected_option is not None and selected_option == q["correct_option"]

    task = state.user_timeout_task.pop(user_id, None)
    # Agar shu funksiya aynan shu taymer vazifasi ichidan chaqirilgan bo'lsa (vaqt tugab),
    # `task` bu holda joriy ishlayotgan vazifaning o'zi - uni bekor qilish keyingi
    # `await`da o'z-o'zini CancelledError bilan to'xtatib qo'yardi.
    if task is not None and task is not asyncio.current_task():
        task.cancel()

    async with AsyncSessionLocal() as db:
        db.add(
            DuelAnswer(
                duel_question_id=q["duel_question_id"],
                user_id=user_id,
                selected_option=selected_option,
                is_correct=is_correct,
                answered_at=now,
                elapsed_ms=elapsed_ms,
            )
        )
        await db.commit()

    await manager.send_to_user(
        user_id,
        {
            "type": "duel_question_result",
            "duel_id": state.duel_id,
            "question_index": index,
            "correct_option": q["correct_option"],
            "your_selected_option": selected_option,
            "your_correct": is_correct,
        },
    )


async def _finish_duel(state: _ActiveDuel) -> None:
    # `state.lock` ostida (chaqiruvchi orqali) - ikkinchi marta chaqirilishining oldini oladi.
    state.finished = True

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DuelAnswer, DuelQuestion.order, Question.question_text, Question.id)
            .join(DuelQuestion, DuelQuestion.id == DuelAnswer.duel_question_id)
            .join(Question, Question.id == DuelQuestion.question_id)
            .where(DuelQuestion.duel_id == state.duel_id)
            .order_by(DuelQuestion.order)
        )
        all_rows = result.all()
        all_answers = [row[0] for row in all_rows]

        def _breakdown_for(uid: str) -> list[dict]:
            return [
                {"order": order, "question_id": question_id, "question_text": question_text, "is_correct": answer.is_correct}
                for answer, order, question_text, question_id in all_rows
                if answer.user_id == uid
            ]

        def _score_for(uid: str):
            user_answers = [a for a in all_answers if a.user_id == uid]
            correct = sum(1 for a in user_answers if a.is_correct)
            answered_time = sum(a.elapsed_ms or 0 for a in user_answers)
            # javob berilmagan savollar "eng uzun vaqt" (time_limit_ms) sifatida hisoblanadi -
            # aks holda javob bermaslik tie-break'da "eng tezkor javob" kabi mukofotlanib qoladi
            unanswered_count = state.total_questions - len(user_answers)
            total_time_ms = answered_time + unanswered_count * QUESTION_TIME_LIMIT_MS
            return correct, total_time_ms

        def _ball_for(uid: str) -> int:
            user_answers = [a for a in all_answers if a.user_id == uid]
            return sum(
                calculate_ball(a.elapsed_ms or QUESTION_TIME_LIMIT_MS, QUESTION_TIME_LIMIT_MS, a.is_correct)
                for a in user_answers
            )

        a_correct, a_time = _score_for(state.user_a_id)
        b_correct, b_time = _score_for(state.user_b_id)

        if a_correct != b_correct:
            a_result, b_result = ("won", "lost") if a_correct > b_correct else ("lost", "won")
        elif a_time != b_time:
            a_result, b_result = ("won", "lost") if a_time < b_time else ("lost", "won")
        else:
            a_result, b_result = "draw", "draw"

        a_ball, b_ball = _ball_for(state.user_a_id), _ball_for(state.user_b_id)

        category = await db.get(Category, state.category_id)
        is_official = category.owner_user_id is None

        def _per_question_answers(uid: str) -> list[tuple[int, bool, int]]:
            return [
                (
                    question_id,
                    answer.is_correct,
                    calculate_ball(answer.elapsed_ms or QUESTION_TIME_LIMIT_MS, QUESTION_TIME_LIMIT_MS, answer.is_correct),
                )
                for answer, _order, _text, question_id in all_rows
                if answer.user_id == uid
            ]

        # A participant may have deleted their account mid-duel - see the
        # user_a/user_b None-check below. Fetching here (rather than after)
        # so a deleted account never gets a QuestionXpAward row inserted for
        # a user_id that no longer exists (that FK would fail the commit).
        user_a = await db.get(User, state.user_a_id)
        user_b = await db.get(User, state.user_b_id)

        a_xp_ball = (
            await compute_xp_eligible_ball(db, state.user_a_id, is_official, _per_question_answers(state.user_a_id))
            if user_a is not None
            else a_ball
        )
        b_xp_ball = (
            await compute_xp_eligible_ball(db, state.user_b_id, is_official, _per_question_answers(state.user_b_id))
            if user_b is not None
            else b_ball
        )
        a_xp, b_xp = round(a_xp_ball / 100), round(b_xp_ball / 100)

        duel = await db.get(Duel, state.duel_id)
        duel.status = "finished"
        duel.finished_at = datetime.now(timezone.utc)

        duel.user_a_correct = a_correct
        duel.user_a_total_time_ms = a_time
        duel.user_a_ball = a_ball
        duel.user_a_xp = a_xp
        duel.user_a_result = a_result

        duel.user_b_correct = b_correct
        duel.user_b_total_time_ms = b_time
        duel.user_b_ball = b_ball
        duel.user_b_xp = b_xp
        duel.user_b_result = b_result

        # A participant may have deleted their account mid-duel - their side
        # of this very Duel row was already reassigned to the placeholder
        # user, but `state.user_a_id`/`state.user_b_id` here still hold the
        # ORIGINAL id captured when the duel started, so `user_a`/`user_b`
        # (fetched above) are None. Skipping xp/streak/XpEvent for that side
        # (rather than crashing on a None attribute access) keeps the commit
        # - and the still-active opponent's own notification below - from
        # failing too.
        if user_a is not None:
            user_a.total_xp += a_xp
            user_a.games_played += 1
            update_streak(user_a, duel.finished_at)
            db.add(XpEvent(user_id=state.user_a_id, amount=a_xp))

        if user_b is not None:
            user_b.total_xp += b_xp
            user_b.games_played += 1
            update_streak(user_b, duel.finished_at)
            db.add(XpEvent(user_id=state.user_b_id, amount=b_xp))

        await db.commit()

    await manager.send_to_user(
        state.user_a_id,
        {
            "type": "duel_finished",
            "duel_id": state.duel_id,
            "result": a_result,
            "your_score": {"correct": a_correct, "total": state.total_questions, "total_time_ms": a_time},
            "opponent_score": {"correct": b_correct, "total": state.total_questions, "total_time_ms": b_time},
            "xp_earned": a_xp,
            "ball_earned": a_ball,
            "breakdown": _breakdown_for(state.user_a_id),
        },
    )
    await manager.send_to_user(
        state.user_b_id,
        {
            "type": "duel_finished",
            "duel_id": state.duel_id,
            "result": b_result,
            "your_score": {"correct": b_correct, "total": state.total_questions, "total_time_ms": b_time},
            "opponent_score": {"correct": a_correct, "total": state.total_questions, "total_time_ms": a_time},
            "xp_earned": b_xp,
            "ball_earned": b_ball,
            "breakdown": _breakdown_for(state.user_b_id),
        },
    )

    _active_duels.pop(state.duel_id, None)
    _user_active_duel.pop(state.user_a_id, None)
    _user_active_duel.pop(state.user_b_id, None)
