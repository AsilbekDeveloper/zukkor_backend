import asyncio
import random
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.duel import Duel, DuelAnswer, DuelQuestion
from app.models.quiz import Category, Question
from app.models.user import User
from app.models.xp_event import XpEvent
from app.services.scoring import calculate_ball
from app.services.streak import update_streak
from app.services.ws_manager import manager

DEFAULT_TOTAL_QUESTIONS = 10
QUESTION_TIME_LIMIT_MS = 15000


class _ActiveDuel:
    def __init__(self, duel_id: str, user_a_id: str, user_b_id: str, category_id: int, total_questions: int):
        self.duel_id = duel_id
        self.user_a_id = user_a_id
        self.user_b_id = user_b_id
        self.category_id = category_id
        self.total_questions = total_questions
        self.current_index = -1
        self.current_duel_question_id: int | None = None
        self.current_correct_option: int | None = None
        self.current_broadcast_at: datetime | None = None
        self.answered_user_ids: set[str] = set()
        self.used_question_ids: list[int] = []
        self.timeout_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()


_active_duels: dict[str, _ActiveDuel] = {}


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
        await db.commit()
        await db.refresh(duel)

        user_a = await db.get(User, user_a_id)
        user_b = await db.get(User, user_b_id)
        category_summary = await _category_summary(db, category)

    state = _ActiveDuel(duel.id, user_a_id, user_b_id, category_id, actual_total)
    _active_duels[duel.id] = state

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

    async with state.lock:
        await _advance_to_next_question(state)


async def _advance_to_next_question(state: _ActiveDuel) -> None:
    state.current_index += 1
    state.answered_user_ids = set()

    async with AsyncSessionLocal() as db:
        question = await _pick_question(db, state.category_id, state.used_question_ids)
        state.used_question_ids.append(question.id)

        option_order = random.sample(range(len(question.options)), len(question.options))
        shuffled_options = [question.options[i] for i in option_order]
        correct_option = option_order.index(question.correct_option_index)

        dq = DuelQuestion(
            duel_id=state.duel_id,
            question_id=question.id,
            order=state.current_index,
            option_order=option_order,
            time_limit_ms=QUESTION_TIME_LIMIT_MS,
        )
        db.add(dq)
        await db.commit()
        await db.refresh(dq)

    state.current_duel_question_id = dq.id
    state.current_correct_option = correct_option
    state.current_broadcast_at = dq.broadcast_at

    message = {
        "type": "duel_question",
        "duel_id": state.duel_id,
        "question_index": state.current_index,
        "question": {
            "text": question.question_text,
            "options": shuffled_options,
            "time_limit_ms": QUESTION_TIME_LIMIT_MS,
        },
    }
    await manager.send_to_user(state.user_a_id, message)
    await manager.send_to_user(state.user_b_id, message)

    if state.timeout_task:
        state.timeout_task.cancel()
    state.timeout_task = asyncio.create_task(_question_timeout(state, state.current_index))


async def _question_timeout(state: _ActiveDuel, question_index: int) -> None:
    try:
        await asyncio.sleep(QUESTION_TIME_LIMIT_MS / 1000)
    except asyncio.CancelledError:
        return

    async with state.lock:
        if state.current_index != question_index or state.duel_id not in _active_duels:
            return
        await _resolve_question(state)


async def submit_answer(user_id: str, duel_id: str, question_index, selected_option) -> None:
    state = _active_duels.get(duel_id)
    if state is None or user_id not in (state.user_a_id, state.user_b_id):
        return
    if not isinstance(question_index, int):
        return

    async with state.lock:
        if question_index != state.current_index:
            return  # eskirgan/xato javob - e'tiborga olinmaydi
        if user_id in state.answered_user_ids:
            return  # bitta o'yinchidan bitta javobdan ortig'i e'tiborga olinmaydi

        now = datetime.now(timezone.utc)
        elapsed_ms = round((now - state.current_broadcast_at).total_seconds() * 1000)
        is_correct = selected_option is not None and selected_option == state.current_correct_option

        async with AsyncSessionLocal() as db:
            db.add(
                DuelAnswer(
                    duel_question_id=state.current_duel_question_id,
                    user_id=user_id,
                    selected_option=selected_option,
                    is_correct=is_correct,
                    answered_at=now,
                    elapsed_ms=elapsed_ms,
                )
            )
            await db.commit()

        state.answered_user_ids.add(user_id)

        other_user_id = state.user_b_id if user_id == state.user_a_id else state.user_a_id
        if other_user_id not in state.answered_user_ids:
            await manager.send_to_user(
                other_user_id,
                {"type": "duel_opponent_answered", "duel_id": duel_id, "question_index": question_index},
            )
        else:
            if state.timeout_task:
                state.timeout_task.cancel()
            await _resolve_question(state)


async def _resolve_question(state: _ActiveDuel) -> None:
    duel_question_id = state.current_duel_question_id
    correct_option = state.current_correct_option

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(DuelAnswer).where(DuelAnswer.duel_question_id == duel_question_id))
        answers_by_user = {a.user_id: a for a in result.scalars().all()}

    def _get(uid: str):
        a = answers_by_user.get(uid)
        if a is None:
            return None, False
        return a.selected_option, a.is_correct

    a_selected, a_correct = _get(state.user_a_id)
    b_selected, b_correct = _get(state.user_b_id)

    await manager.send_to_user(
        state.user_a_id,
        {
            "type": "duel_question_result",
            "duel_id": state.duel_id,
            "question_index": state.current_index,
            "correct_option": correct_option,
            "your_selected_option": a_selected,
            "your_correct": a_correct,
            "opponent_selected_option": b_selected,
            "opponent_correct": b_correct,
        },
    )
    await manager.send_to_user(
        state.user_b_id,
        {
            "type": "duel_question_result",
            "duel_id": state.duel_id,
            "question_index": state.current_index,
            "correct_option": correct_option,
            "your_selected_option": b_selected,
            "your_correct": b_correct,
            "opponent_selected_option": a_selected,
            "opponent_correct": a_correct,
        },
    )

    if state.current_index + 1 < state.total_questions:
        await _advance_to_next_question(state)
    else:
        await _finish_duel(state)


async def _finish_duel(state: _ActiveDuel) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DuelAnswer)
            .join(DuelQuestion, DuelQuestion.id == DuelAnswer.duel_question_id)
            .where(DuelQuestion.duel_id == state.duel_id)
        )
        all_answers = result.scalars().all()

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
        a_xp, b_xp = round(a_ball / 100), round(b_ball / 100)

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

        user_a = await db.get(User, state.user_a_id)
        user_b = await db.get(User, state.user_b_id)
        user_a.total_xp += a_xp
        user_a.games_played += 1
        update_streak(user_a, duel.finished_at)
        user_b.total_xp += b_xp
        user_b.games_played += 1
        update_streak(user_b, duel.finished_at)

        db.add(XpEvent(user_id=state.user_a_id, amount=a_xp))
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
        },
    )

    _active_duels.pop(state.duel_id, None)
