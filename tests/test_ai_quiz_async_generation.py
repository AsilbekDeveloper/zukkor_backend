import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.security import hash_password
from app.models.ai_quiz_job import AiQuizGenerationJob
from app.models.currency_transaction import CurrencyTransaction
from app.models.quiz import Category, Question
from app.models.user import User
from app.routers import ai_quiz
from app.routers.ai_quiz import generate_ai_quiz_async, get_generation_job
from app.services import ai_usage_limiter, wallet
from app.services.ai_quiz_generation import GeneratedQuiz, QuizGenerationError
from conftest import make_request

FAKE_QUESTIONS = [
    {"question_text": "1+1 nechaga teng?", "options": ["1", "2", "3", "4"], "correct_option_index": 1},
]


class _FakePushRecorder:
    def __init__(self):
        self.calls: list[tuple[str, str, str, dict | None]] = []

    async def __call__(self, db, user_id, title, body, data=None):
        self.calls.append((user_id, title, body, data))


@pytest.fixture
async def _isolated_session_maker(monkeypatch):
    # _run_generation_job writes via the module-level AsyncSessionLocal -
    # point that at an isolated in-memory SQLite engine (same pattern as
    # test_duel_forfeit.py / test_notification_cleanup.py).
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ai_quiz, "AsyncSessionLocal", session_maker)
    yield session_maker
    await engine.dispose()


@pytest.fixture
def _fake_push(monkeypatch):
    recorder = _FakePushRecorder()
    monkeypatch.setattr(ai_quiz, "send_push_to_user", recorder)
    return recorder


async def _create_user(db, email="user@example.com") -> User:
    user = User(email=email, hashed_password=await hash_password("Parol1234"), diamond_balance=100_000)
    db.add(user)
    await db.flush()
    return user


async def _run_scheduled_tasks(background_tasks: BackgroundTasks) -> None:
    for task in background_tasks.tasks:
        await task()


@pytest.mark.anyio
async def test_topic_job_starts_pending_then_completes(_isolated_session_maker, _fake_push, monkeypatch):
    async def _fake_generate(topic, instruction, count):
        return GeneratedQuiz(questions=FAKE_QUESTIONS, input_tokens=100, output_tokens=100)

    monkeypatch.setattr(ai_quiz, "generate_questions_from_topic", _fake_generate)
    background_tasks = BackgroundTasks()

    # Endpoint's own `db` and the background task's AsyncSessionLocal must
    # point at the SAME database, or the job the endpoint writes is
    # invisible to the background task that's supposed to finish it.
    async with _isolated_session_maker() as db:
        user = await _create_user(db)
        await db.commit()

        started = await generate_ai_quiz_async(
            make_request(),
            background_tasks,
            file=None,
            instruction=None,
            topic="Matematika",
            question_count=1,
            topic_category_id=None,
            current_user=user,
            db=db,
        )

        job = await db.get(AiQuizGenerationJob, started.job_id)
        assert job is not None
        assert job.status == "pending"

    await _run_scheduled_tasks(background_tasks)

    async with _isolated_session_maker() as bg_db:
        finished_job = await bg_db.get(AiQuizGenerationJob, started.job_id)
        assert finished_job.status == "completed"
        assert finished_job.category_id is not None
        category = await bg_db.get(Category, finished_job.category_id)
        assert category.owner_user_id == user.id
        questions = (
            await bg_db.execute(select(Question).where(Question.category_id == category.id))
        ).scalars().all()
        assert len(questions) == 1

        # 2026-09-16 xavfsizlik auditi: taxminiy narx OLDINDAN band
        # qilingan, generatsiya tugagach haqiqiy narxga moslashtirildi -
        # yakunda faqat bitta "ai_generation" ledger yozuvi bor va u
        # HAQIQIY (band qilingandan farqli bo'lishi mumkin) narxni
        # ko'rsatadi.
        expected_cost = wallet.diamond_cost_from_tokens(input_tokens=100, output_tokens=100)
        refreshed_user = await bg_db.get(User, user.id)
        assert refreshed_user.diamond_balance == 100_000 - expected_cost
        assert finished_job.diamond_cost == expected_cost
        tx = (
            await bg_db.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))
        ).scalar_one()
        assert tx.amount == -expected_cost
        assert tx.reason == "ai_generation"

    assert _fake_push.calls
    push_user_id, push_title, _, push_data = _fake_push.calls[0]
    assert push_user_id == user.id
    assert push_title == "Quiz tayyor!"
    assert push_data == {"type": "ai_quiz_ready", "quiz_id": str(category.id)}


@pytest.mark.anyio
async def test_job_failure_is_recorded_and_pushed(_isolated_session_maker, _fake_push, monkeypatch):
    async def _boom(topic, instruction, count):
        raise QuizGenerationError("AI xizmati hozircha sozlanmagan")

    monkeypatch.setattr(ai_quiz, "generate_questions_from_topic", _boom)
    background_tasks = BackgroundTasks()

    async with _isolated_session_maker() as db:
        user = await _create_user(db)
        await db.commit()

        started = await generate_ai_quiz_async(
            make_request(),
            background_tasks,
            file=None,
            instruction=None,
            topic="Tarix",
            question_count=1,
            topic_category_id=None,
            current_user=user,
            db=db,
        )
    await _run_scheduled_tasks(background_tasks)

    async with _isolated_session_maker() as bg_db:
        job = await bg_db.get(AiQuizGenerationJob, started.job_id)
        assert job.status == "failed"
        assert job.error_message == "AI xizmati hozircha sozlanmagan"

        # 2026-09-16 xavfsizlik auditi: generatsiya butunlay
        # muvaffaqiyatsiz bo'lgani uchun band qilingan taxminiy narx
        # TO'LIQ qaytarildi - hech narsa to'lanmadi.
        refreshed_user = await bg_db.get(User, user.id)
        assert refreshed_user.diamond_balance == 100_000
        all_tx = (
            await bg_db.execute(select(CurrencyTransaction).where(CurrencyTransaction.user_id == user.id))
        ).scalars().all()
        assert all_tx == []

    assert _fake_push.calls
    _, push_title, _, push_data = _fake_push.calls[0]
    assert push_title == "Quiz yaratib bo'lmadi"
    assert push_data == {"type": "ai_quiz_failed"}


@pytest.mark.anyio
async def test_generate_async_rejects_when_diamond_balance_is_insufficient(db_session):
    user = await _create_user(db_session, "poor_async@example.com")
    user.diamond_balance = 0
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await generate_ai_quiz_async(
            make_request(),
            BackgroundTasks(),
            file=None,
            instruction=None,
            topic="Tarix",
            question_count=1,
            topic_category_id=None,
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 402
    assert user.diamond_balance == 0


@pytest.mark.anyio
async def test_generate_async_returns_503_and_refunds_when_the_daily_gemini_limit_is_reached(db_session, monkeypatch):
    monkeypatch.setattr(ai_usage_limiter.settings, "MAX_DAILY_GEMINI_CALLS", 0)
    user = await _create_user(db_session, "throttled_async@example.com")
    user.diamond_balance = 100
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await generate_ai_quiz_async(
            make_request(),
            BackgroundTasks(),
            file=None,
            instruction=None,
            topic="Tarix",
            question_count=1,
            topic_category_id=None,
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 503
    assert user.diamond_balance == 100


@pytest.mark.anyio
async def test_generate_async_rejects_when_neither_file_nor_topic(db_session):
    user = await _create_user(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await generate_ai_quiz_async(
            make_request(),
            BackgroundTasks(),
            file=None,
            instruction=None,
            topic=None,
            question_count=1,
            topic_category_id=None,
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_generate_async_counts_a_pending_job_toward_the_daily_limit(db_session, monkeypatch):
    # 2026-09-18, xavfsizlik auditi: `Category` faqat generatsiya
    # TUGAGANDA yaratiladi - agar faqat shuni sanasak, foydalanuvchi
    # ko'p `/generate-async` so'rovini ketma-ket, hech biri tugashini
    # kutmasdan yuborib, kunlik chegarani chetlab o'tishi mumkin edi.
    # Hali 'pending' turgan job ham hisoblanishi shart.
    monkeypatch.setattr(ai_quiz, "MAX_USER_DAILY_AI_GENERATIONS", 1)
    user = await _create_user(db_session)
    db_session.add(AiQuizGenerationJob(user_id=user.id, status="pending", question_count=5))
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await generate_ai_quiz_async(
            make_request(),
            BackgroundTasks(),
            file=None,
            instruction=None,
            topic="Tarix",
            question_count=1,
            topic_category_id=None,
            current_user=user,
            db=db_session,
        )
    assert exc_info.value.status_code == 429


@pytest.mark.anyio
async def test_generate_async_ignores_a_failed_job_in_the_daily_limit(db_session, monkeypatch):
    # Muvaffaqiyatsiz urinish "muvaffaqiyatli" hisoblanmasligi kerak -
    # limitga tegmaydi.
    monkeypatch.setattr(ai_quiz, "MAX_USER_DAILY_AI_GENERATIONS", 1)
    user = await _create_user(db_session)
    db_session.add(AiQuizGenerationJob(user_id=user.id, status="failed", question_count=5))
    await db_session.commit()

    started = await generate_ai_quiz_async(
        make_request(),
        BackgroundTasks(),
        file=None,
        instruction=None,
        topic="Tarix",
        question_count=1,
        topic_category_id=None,
        current_user=user,
        db=db_session,
    )
    assert started.job_id


@pytest.mark.anyio
async def test_get_generation_job_hides_other_users_jobs(db_session):
    owner = await _create_user(db_session, "owner@example.com")
    stranger = await _create_user(db_session, "stranger@example.com")
    job = AiQuizGenerationJob(user_id=owner.id, status="pending", question_count=5)
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    with pytest.raises(HTTPException) as exc_info:
        await get_generation_job(job.id, current_user=stranger, db=db_session)
    assert exc_info.value.status_code == 404

    # Owner can see it.
    result = await get_generation_job(job.id, current_user=owner, db=db_session)
    assert result.status == "pending"
    assert result.quiz is None
